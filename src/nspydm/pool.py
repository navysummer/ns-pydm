"""达梦数据库连接池实现（原生后端，线程安全）。

使用示例::

    from nspydm import create_pool

    pool = create_pool(user="SYSDBA", password="SYSDBA", server="127.0.0.1",
                       min_size=2, max_size=20)

    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()

    pool.close_all()
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING, Any

from .dbapi import InterfaceError  # type: ignore[attr-defined]
from .dbapi import connect as _raw_connect  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from ._backends._native import Connection, Cursor
else:
    from . import dbapi as _sync_dbapi
    Connection = _sync_dbapi.Connection  # type: ignore[attr-defined]
    Cursor = _sync_dbapi.Cursor  # type: ignore[attr-defined]


class PooledConnection:
    """连接代理：包装 Connection，close() 时归还到连接池。"""

    def __init__(self, pool: ConnectionPool, conn: Connection) -> None:
        self._pool = pool
        self._conn = conn
        self._closed = False

    def close(self) -> None:
        """归还连接到池。"""
        if self._closed:
            return
        self._closed = True
        pool = self._pool
        if pool is not None:
            self._pool = None  # type: ignore[assignment]
            pool.return_connection(self._conn)

    def commit(self) -> None:
        if self._closed:
            raise InterfaceError("connection already closed")
        self._conn.commit()

    def rollback(self) -> None:
        if self._closed:
            raise InterfaceError("connection already closed")
        self._conn.rollback()

    def cursor(self) -> Cursor:
        if self._closed:
            raise InterfaceError("connection already closed")
        return self._conn.cursor()

    def __enter__(self) -> PooledConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    @property
    def autocommit(self) -> bool:
        return self._conn.autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._conn.autocommit = value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_PooledConnection__"):
            raise AttributeError(name)
        if self._closed:
            raise InterfaceError("connection already closed")
        return getattr(self._conn, name)


class ConnectionPool:
    """线程安全的达梦数据库连接池。

    参数:
        min_size: 初始连接数（默认 1）
        max_size: 最大连接数（默认 10）
        max_wait: 获取连接超时秒数（默认 30）
        **connect_kwargs: 传递给 nspydm.connect() 的参数
    """

    def __init__(
        self,
        min_size: int = 1,
        max_size: int = 10,
        max_wait: float = 30.0,
        **connect_kwargs: Any,
    ) -> None:
        if min_size < 0:
            raise InterfaceError("min_size must be >= 0")
        if max_size < 1:
            raise InterfaceError("max_size must be >= 1")
        if min_size > max_size:
            raise InterfaceError("min_size must be <= max_size")

        self._min_size = min_size
        self._max_size = max_size
        self._max_wait = max_wait
        self._connect_kwargs = connect_kwargs

        self._pool: list[Connection] = []
        self._in_use: set[Connection] = set()
        self._condition = threading.Condition()
        self._closed = False
        self._total_created = 0

        self._prefill()

    def _prefill(self) -> None:
        for _ in range(self._min_size):
            try:
                conn = _raw_connect(**self._connect_kwargs)
                self._pool.append(conn)
            except Exception:
                continue

    def _create_connection(self) -> Connection:
        conn = _raw_connect(**self._connect_kwargs)
        self._total_created += 1
        return conn

    def _current_size(self) -> int:
        return len(self._pool) + len(self._in_use)

    def _ping(self, conn: Connection) -> bool:
        """检查连接是否存活。"""
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        except Exception:
            return False

    def get_connection(self) -> PooledConnection:
        """从池中获取一个可用连接。"""
        deadline = time.monotonic() + self._max_wait

        with self._condition:
            while True:
                if self._closed:
                    raise InterfaceError("Connection pool has been closed")

                # 1. 从空闲池取有效连接
                while self._pool:
                    conn = self._pool.pop()
                    if self._ping(conn):
                        self._in_use.add(conn)
                        return PooledConnection(self, conn)
                    with contextlib.suppress(Exception):
                        conn.close()
                    continue

                # 2. 池未满，创建新连接
                if self._current_size() < self._max_size:
                    try:
                        conn = self._create_connection()
                        self._in_use.add(conn)
                        return PooledConnection(self, conn)
                    except Exception:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise InterfaceError(
                                f"Unable to create connection (size={self._current_size()}, max={self._max_size})"
                            ) from None
                        self._condition.wait(timeout=min(remaining, 0.5))
                        continue

                # 3. 池已满，等待归还
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterfaceError(
                        f"Timed out waiting for connection (max_size={self._max_size})"
                    )
                self._condition.wait(timeout=min(remaining, 1.0))

    def return_connection(self, conn: Connection) -> None:
        """归还连接到空闲池。"""
        with self._condition:
            if conn not in self._in_use:
                return
            self._in_use.remove(conn)
            if self._closed:
                conn.close()
            else:
                self._pool.append(conn)
            self._condition.notify()

    def close_all(self) -> None:
        """关闭所有连接，标记池为关闭状态。"""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for conn in list(self._in_use):
                conn.close()
            self._in_use.clear()
            for conn in self._pool:
                conn.close()
            self._pool.clear()
            self._condition.notify_all()

    @property
    def size(self) -> int:
        with self._condition:
            return self._current_size()

    @property
    def idle_count(self) -> int:
        with self._condition:
            return len(self._pool)

    @property
    def in_use_count(self) -> int:
        with self._condition:
            return len(self._in_use)

    @property
    def total_created(self) -> int:
        return self._total_created

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def __enter__(self) -> ConnectionPool:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close_all()

    def __repr__(self) -> str:
        with self._condition:
            state = "closed" if self._closed else "open"
            return f"<ConnectionPool({state}) idle={len(self._pool)} in_use={len(self._in_use)} max={self._max_size}>"


def create_pool(
    min_size: int = 1,
    max_size: int = 10,
    max_wait: float = 30.0,
    **connect_kwargs: Any,
) -> ConnectionPool:
    """创建连接池（便捷函数）。

    Args:
        min_size: 初始连接数
        max_size: 最大连接数
        max_wait: 获取连接超时秒数
        **connect_kwargs: 传递给 nspydm.connect() 的参数

    Returns:
        ConnectionPool 实例
    """
    return ConnectionPool(
        min_size=min_size,
        max_size=max_size,
        max_wait=max_wait,
        **connect_kwargs,
    )
