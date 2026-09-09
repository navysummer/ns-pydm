"""异步连接池。

基于 asyncio.Condition 实现生产者-消费者模型，从 dmAsync 移植并适配 ns-pydm 的架构。

支持:
- 最小/最大连接数控制
- 连接老化回收（pool_recycle）
- 自动填充空闲连接
- ``acquire()`` 获取连接的上下文管理器
- ``cursor()`` 便捷方法，一步获取连接+游标
"""

from __future__ import annotations

import asyncio
import collections
from types import TracebackType
from typing import Any, Callable

from .connection import AsyncConnection, AsyncCursor
from .connection import connect as _async_connect
from .utils import _ContextManager, get_running_loop

__all__ = (
    "AsyncPool",
    "create_pool",
)


# ---------------------------------------------------------------------------
# 池内上下文管理器
# ---------------------------------------------------------------------------


class _PoolCursorContextManager:
    """从池获取的游标上下文管理器，自动获取连接并在退出时归还到池。"""

    __slots__ = ("_pool", "_cursorclass", "_conn", "_cursor", "_released")

    def __init__(self, pool: AsyncPool, cursorclass: int | None = None) -> None:
        self._pool = pool
        self._cursorclass = cursorclass
        self._conn: AsyncConnection | None = None
        self._cursor: AsyncCursor | None = None
        self._released = False

    async def __aenter__(self) -> AsyncCursor:
        conn = await self._pool._acquire()
        try:
            c = await conn.cursor(cursorclass=self._cursorclass)
        except BaseException:
            await self._pool.release(conn)
            raise
        self._conn = conn
        self._cursor = c
        return c

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._cursor is not None and not self._cursor.closed:
            await self._cursor.close()
        if self._conn is not None and not self._released:
            self._released = True
            await self._pool.release(self._conn)


# ---------------------------------------------------------------------------
# AsyncPool
# ---------------------------------------------------------------------------


class AsyncPool:
    """异步达梦数据库连接池。

    基于 asyncio.Condition 实现生产者-消费者模型，从 dmAsync 移植并适配 ns-pydm 的架构。

    特性:
    - 最小/最大连接数控制 (minsize/maxsize)
    - 连接老化回收 (pool_recycle)
    - 自动填充空闲连接
    - 连接获取超时控制 (timeout)
    - 创建时回调 (on_connect)
    - ``acquire()`` 获取连接的上下文管理器
    - ``cursor()`` 便捷方法，一步获取连接+游标（支持直接 ``async with pool.cursor()``）
    - 支持异步上下文管理器 (``async with pool``)

    线程安全: 所有方法均在单个事件循环中运行，通过 asyncio 同步原语保证并发安全。

    用法::

        pool = await create_pool(
            user="SYSDBA", password="xxx", server="127.0.0.1",
            minsize=2, maxsize=10,
        )

        # 方式一：获取连接
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                print(await cur.fetchone())

        # 方式二：直接获取游标（自动归还）
        async with pool.cursor() as cur:
            await cur.execute("SELECT 1")
            print(await cur.fetchone())

        pool.close()
        await pool.wait_closed()
    """

    def __init__(
        self,
        minsize: int,
        maxsize: int,
        timeout: float,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        on_connect: Callable[[AsyncConnection], Any] | None = None,
        pool_recycle: float = -1.0,
        validate_on_acquire: bool = True,
        **connect_kwargs: Any,
    ) -> None:
        if minsize < 0:
            raise ValueError("minsize must be >= 0")
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if minsize > maxsize:
            raise ValueError("minsize must be <= maxsize")

        self._connect_kwargs = connect_kwargs
        self._minsize = minsize
        self._maxsize = maxsize
        self._timeout = timeout
        self._loop = loop
        self._on_connect = on_connect
        self._recycle = pool_recycle
        self._validate_on_acquire = validate_on_acquire

        self._free: collections.deque[AsyncConnection] = collections.deque(maxlen=maxsize or None)
        self._used: set[AsyncConnection] = set()
        self._terminated: set[AsyncConnection] = set()
        self._cond: asyncio.Condition | None = None
        self._acquiring = 0
        self._closing = False
        self._closed = False

        # 统计指标
        self._acquire_count = 0
        self._release_count = 0
        self._invalid_count = 0
        self._timeout_count = 0

    @classmethod
    async def create(
        cls,
        minsize: int = 1,
        maxsize: int = 10,
        timeout: float = 60.0,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        on_connect: Callable[[AsyncConnection], Any] | None = None,
        pool_recycle: float = -1.0,
        **connect_kwargs: Any,
    ) -> AsyncPool:
        """创建并初始化连接池。"""
        self = cls(
            minsize=minsize,
            maxsize=maxsize,
            timeout=timeout,
            loop=loop,
            on_connect=on_connect,
            pool_recycle=pool_recycle,
            **connect_kwargs,
        )
        if self._minsize > 0:
            async with self._get_cond():
                await self._fill_free_pool(False)
        return self

    async def _fill_free_pool(self, override_min: bool) -> None:
        """填充空闲连接到 minsize，同时回收过期连接。"""
        # 回收过期连接
        await self._reap_connections()

        target = self._maxsize if override_min else self._minsize
        while self.size < target:
            self._acquiring += 1
            try:
                conn = await _async_connect(**self._connect_kwargs)
                if self._on_connect is not None:
                    await self._on_connect(conn)
                self._free.append(conn)
                self._get_cond().notify()
            finally:
                self._acquiring -= 1

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = get_running_loop()
        return self._loop

    def _get_cond(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def _reap_connections(self) -> None:
        """回收过期连接。"""
        if self._recycle <= 0:
            return
        now = self._get_loop().time()
        recycled: list[AsyncConnection] = []
        while self._free:
            conn = self._free.popleft()
            if now - conn._last_usage > self._recycle:
                recycled.append(conn)
            else:
                self._free.appendleft(conn)
                break
        for conn in recycled:
            await conn.close()

    async def _acquire(self) -> AsyncConnection:
        if self._closing:
            raise RuntimeError("Cannot acquire connection after closing pool")

        async with self._get_cond():
            while True:
                await self._fill_free_pool(True)
                if self._free:
                    conn = self._free.popleft()
                    assert conn not in self._used

                    # 连接验证（可选）
                    if self._validate_on_acquire:
                        try:
                            await conn.ping()
                        except Exception:
                            self._invalid_count += 1
                            await conn.close()
                            continue

                    self._used.add(conn)
                    self._acquire_count += 1
                    return conn

                try:
                    await asyncio.wait_for(self._get_cond().wait(), timeout=self._timeout)
                except asyncio.TimeoutError:
                    self._timeout_count += 1
                    raise RuntimeError(
                        f"Timed out waiting for connection " f"(maxsize={self._maxsize})"
                    ) from None

    def acquire(self) -> _ContextManager[AsyncConnection]:
        """获取连接，返回可 await / async with 的上下文管理器。

        用法::

            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
        """
        coro = self._acquire()
        return _ContextManager[AsyncConnection](coro, self.release)

    def cursor(
        self,
        cursorclass: int | None = None,
    ) -> _PoolCursorContextManager:
        """获取游标（自动获取连接，归还连接）。

        用法::

            async with pool.cursor() as cur:
                await cur.execute("SELECT 1")
                print(await cur.fetchone())  # 游标和连接自动关闭
        """
        return _PoolCursorContextManager(self, cursorclass)

    async def release(self, conn: AsyncConnection) -> None:
        """归还连接到池。"""
        if conn in self._terminated:
            self._terminated.remove(conn)
            return
        self._used.discard(conn)
        if conn._closed:
            return
        if self._closing:
            await conn.close()
        else:
            self._free.append(conn)
            self._release_count += 1
        async with self._get_cond():
            self._get_cond().notify()

    async def clear(self) -> None:
        """关闭所有空闲连接。"""
        async with self._get_cond():
            while self._free:
                conn = self._free.popleft()
                await conn.close()
            self._get_cond().notify()

    def close(self) -> None:
        """标记池为关闭状态，阻止新请求。"""
        if self._closed:
            return
        self._closing = True

    def terminate(self) -> None:
        """立即终止所有连接。"""
        self.close()
        for conn in list(self._used):
            self._terminated.add(conn)
        self._used.clear()

    async def wait_closed(self) -> None:
        """等待所有连接归还后关闭。"""
        if self._closed:
            return
        if not self._closing:
            raise RuntimeError(".wait_closed() should be called after .close()")
        while self._free:
            conn = self._free.popleft()
            await conn.close()
        async with self._get_cond():
            while self.size > self.freesize:
                await self._get_cond().wait()
        self._closed = True

    # -- 属性 --

    @property
    def size(self) -> int:
        return len(self._free) + len(self._used) + self._acquiring

    @property
    def freesize(self) -> int:
        return len(self._free)

    @property
    def minsize(self) -> int:
        return self._minsize

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def closed(self) -> bool:
        return self._closed

    # -- 指标 --
    @property
    def acquire_count(self) -> int:
        """成功获取连接的次数。"""
        return self._acquire_count

    @property
    def release_count(self) -> int:
        """归还连接的次数。"""
        return self._release_count

    @property
    def invalid_count(self) -> int:
        """验证失败并丢弃的连接数。"""
        return self._invalid_count

    @property
    def timeout_count(self) -> int:
        """获取连接超时的次数。"""
        return self._timeout_count

    def get_stats(self) -> dict[str, int]:
        """获取连接池统计信息。"""
        return {
            "size": self.size,
            "free": self.freesize,
            "used": len(self._used),
            "acquired": self._acquire_count,
            "released": self._release_count,
            "invalid": self._invalid_count,
            "timeouts": self._timeout_count,
        }

    # -- 异步上下文管理器（管理池本身） --

    async def __aenter__(self) -> AsyncPool:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
        await self.wait_closed()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "closing" if self._closing else "open"
        return (
            f"<AsyncPool({state}) "
            f"free={len(self._free)} "
            f"used={len(self._used)} "
            f"max={self._maxsize}>"
        )


# ---------------------------------------------------------------------------
# create_pool
# ---------------------------------------------------------------------------


async def create_pool(
    minsize: int = 1,
    maxsize: int = 10,
    timeout: float = 60.0,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    on_connect: Callable[[AsyncConnection], Any] | None = None,
    pool_recycle: float = -1.0,
    validate_on_acquire: bool = True,
    **connect_kwargs: Any,
) -> AsyncPool:
    """创建异步连接池。

    参数:
        minsize: 最小连接数（默认 1）
        maxsize: 最大连接数（默认 10）
        timeout: 获取连接超时（默认 60 秒）
        loop: 事件循环（默认使用当前运行循环）
        on_connect: 连接创建后的回调
        pool_recycle: 连接回收时间（秒），<= 0 表示不回收
        validate_on_acquire: 获取连接时是否验证（默认 True）
        **connect_kwargs: 传递给 nspydm.connect() 的参数

    用法::

        pool = await create_pool(
            user="SYSDBA", password="xxx", server="127.0.0.1",
            minsize=2, maxsize=10, pool_recycle=3600,
        )
        async with pool.acquire() as conn:
            ...
    """
    return await AsyncPool.create(
        minsize=minsize,
        maxsize=maxsize,
        timeout=timeout,
        loop=loop,
        on_connect=on_connect,
        pool_recycle=pool_recycle,
        validate_on_acquire=validate_on_acquire,
        **connect_kwargs,
    )
