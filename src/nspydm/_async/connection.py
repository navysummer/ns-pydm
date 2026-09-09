"""异步连接、游标与事务。

包装同步的 Connection / Cursor，将阻塞操作通过 ``_to_thread`` 委托到线程池。
兼容 macOS 原生后端（_native）和 Linux/Windows dmPython 后端（_dmpython）。
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import enum
import uuid
import warnings
from collections.abc import AsyncIterator, Sequence
from types import TracebackType
from typing import Any, Optional, cast

from .. import dbapi as _sync_dbapi
from .utils import _ContextManager, _to_thread, get_running_loop

__all__ = (
    "AsyncCursor",
    "AsyncConnection",
    "AsyncTransaction",
    "IsolationLevel",
    "connect",
    "DefaultCompiler",
    "ReadCommittedCompiler",
    "RepeatableReadCompiler",
    "SerializableCompiler",
)


# ---------------------------------------------------------------------------
# 隔离级别编译器（从 dmAsync 移植）
# ---------------------------------------------------------------------------


class IsolationCompiler(abc.ABC):
    """生成事务控制 SQL 的编译器基类。"""

    __slots__ = ("_readonly", "_deferrable")

    def __init__(self, readonly: bool = False, deferrable: bool = False) -> None:
        self._readonly = readonly
        self._deferrable = deferrable

    @abc.abstractmethod
    def begin(self) -> str:
        """构建 START TRANSACTION 语句。"""

    def savepoint(self, unique_id: str) -> str:
        return f"SAVEPOINT {unique_id}"

    def release_savepoint(self, unique_id: str) -> str:
        return f"RELEASE SAVEPOINT {unique_id}"

    def rollback_savepoint(self, unique_id: str) -> str:
        return f"ROLLBACK TO SAVEPOINT {unique_id}"

    def commit(self) -> str:
        return "COMMIT"

    def rollback(self) -> str:
        return "ROLLBACK"


class DefaultCompiler(IsolationCompiler):
    def begin(self) -> str:
        return "START TRANSACTION"


class ReadCommittedCompiler(IsolationCompiler):
    def begin(self) -> str:
        sql = ["START TRANSACTION", "ISOLATION LEVEL READ COMMITTED"]
        if self._readonly:
            sql.append("READ ONLY")
        else:
            sql.append("READ WRITE")
        if self._deferrable:
            sql.append("DEFERRABLE")
        else:
            sql.append("NOT DEFERRABLE")
        return " ".join(sql)


class RepeatableReadCompiler(IsolationCompiler):
    def begin(self) -> str:
        sql = ["START TRANSACTION", "ISOLATION LEVEL REPEATABLE READ"]
        if self._readonly:
            sql.append("READ ONLY")
        else:
            sql.append("READ WRITE")
        if self._deferrable:
            sql.append("DEFERRABLE")
        else:
            sql.append("NOT DEFERRABLE")
        return " ".join(sql)


class SerializableCompiler(IsolationCompiler):
    def begin(self) -> str:
        sql = ["START TRANSACTION", "ISOLATION LEVEL SERIALIZABLE"]
        if self._readonly:
            sql.append("READ ONLY")
        else:
            sql.append("READ WRITE")
        if self._deferrable:
            sql.append("DEFERRABLE")
        else:
            sql.append("NOT DEFERRABLE")
        return " ".join(sql)


# ---------------------------------------------------------------------------
# 隔离级别枚举
# ---------------------------------------------------------------------------


class IsolationLevel(enum.Enum):
    """事务隔离级别。"""

    serializable = SerializableCompiler
    repeatable_read = RepeatableReadCompiler
    read_committed = ReadCommittedCompiler
    default = DefaultCompiler

    def __call__(self, readonly: bool = False, deferrable: bool = False) -> IsolationCompiler:
        return cast(IsolationCompiler, self.value(readonly=readonly, deferrable=deferrable))


# ---------------------------------------------------------------------------
# 事务辅助函数
# ---------------------------------------------------------------------------


async def _commit_transaction(t: AsyncTransaction) -> None:
    await t.commit()


async def _rollback_transaction(t: AsyncTransaction) -> None:
    await t.rollback()


async def _rollback_savepoint(t: AsyncTransaction) -> None:
    await t.rollback_savepoint()


async def _release_savepoint(t: AsyncTransaction) -> None:
    await t.release_savepoint()


async def _close_cursor(c: AsyncCursor) -> None:
    await c.close()


# ---------------------------------------------------------------------------
# AsyncTransaction
# ---------------------------------------------------------------------------


class AsyncTransaction:
    """异步事务管理器。

    通过游标执行 ``START TRANSACTION`` / ``COMMIT`` / ``ROLLBACK`` SQL 语句。
    支持保存点（savepoint）嵌套。

    用法::

        async with conn.cursor() as cur:
            # 方式一：自动管理
            async with cur.begin():
                await cur.execute("INSERT INTO t VALUES (1)")

            # 方式二：手动控制
            txn = await cur.begin()
            await cur.execute("INSERT INTO t VALUES (1)")
            await txn.commit()
    """

    __slots__ = ("_cursor", "_is_begin", "_isolation", "_unique_id")

    def __init__(
        self,
        cursor: AsyncCursor,
        isolation_level: IsolationLevel = IsolationLevel.default,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> None:
        self._cursor = cursor
        self._is_begin = False
        self._isolation = isolation_level(readonly=readonly, deferrable=deferrable)
        self._unique_id: str | None = None

    @property
    def is_begin(self) -> bool:
        return self._is_begin

    async def begin(self) -> AsyncTransaction:
        """开始事务。"""
        if self._is_begin:
            raise RuntimeError("The transaction has been initiated!")
        self._is_begin = True
        await self._cursor.execute(self._isolation.begin())
        return self

    async def commit(self) -> None:
        """提交事务。"""
        if not self._is_begin:
            raise RuntimeError("No transaction is in progress!")
        await self._cursor.execute(self._isolation.commit())
        self._is_begin = False

    async def rollback(self) -> None:
        """回滚事务。"""
        if not self._is_begin:
            raise RuntimeError("No transaction is in progress!")
        if not self._cursor.closed:
            await self._cursor.execute(self._isolation.rollback())
        self._is_begin = False

    async def savepoint(self) -> AsyncTransaction:
        """创建保存点。"""
        if not self._is_begin:
            raise RuntimeError("No transaction is in progress!")
        if self._unique_id is not None:
            raise RuntimeError("Savepoint name is not None")
        self._unique_id = f"s{uuid.uuid1().hex}"
        await self._cursor.execute(self._isolation.savepoint(self._unique_id))
        return self

    async def rollback_savepoint(self) -> None:
        """回滚到保存点。"""
        if self._unique_id is None:
            raise RuntimeError("Savepoint name is None")
        await self._cursor.execute(self._isolation.rollback_savepoint(self._unique_id))
        self._unique_id = None

    async def release_savepoint(self) -> None:
        """释放保存点。"""
        if self._unique_id is None:
            raise RuntimeError("Savepoint name is None")
        await self._cursor.execute(self._isolation.release_savepoint(self._unique_id))
        self._unique_id = None

    def _savepoint(self) -> _ContextManager[AsyncTransaction]:
        """保存点的上下文管理器。"""
        return _ContextManager[AsyncTransaction](
            self.savepoint(),
            _release_savepoint,
            _rollback_savepoint,
        )

    # -- 异步上下文管理器 --

    async def __aenter__(self) -> AsyncTransaction:
        return await self.begin()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()

    def __repr__(self) -> str:
        state = "begun" if self._is_begin else "idle"
        return f"<AsyncTransaction({state}) at 0x{id(self):x}>"


# ---------------------------------------------------------------------------
# AsyncCursor
# ---------------------------------------------------------------------------


class AsyncCursor:
    """异步游标，包装同步 Cursor。

    将同步游标的所有阻塞操作（execute, fetchone, fetchmany, fetchall 等）
    通过 ``_to_thread`` 委托到线程池中执行，提供非阻塞的异步接口。

    实现了 DB-API 2.0 兼容的异步游标接口，同时支持 dmPython 扩展属性：
    - ``name``: 游标名称
    - ``scrollable``: 是否可滚动
    - ``withhold``: 提交后是否保留结果集
    - ``itersize``: 迭代抓取块大小
    - ``mogrify()``: SQL 模板渲染

    用法::

        async with conn.cursor() as cur:
            await cur.execute("SELECT ?", [1])
            row = await cur.fetchone()

        # 事务
        async with conn.cursor() as cur:
            async with cur.begin():
                await cur.execute("INSERT INTO t VALUES (1)")

        # 迭代结果集
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM t")
            async for row in cur:
                print(row)
    """

    def __init__(self, cursor: Any, connection: AsyncConnection | None = None) -> None:
        self._cursor = cursor
        self._closed = False
        self._transaction = AsyncTransaction(self)
        self._connection = connection

    # -- 只读属性代理 --

    @property
    def description(self) -> Any:
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        return cast(int, self._cursor.rowcount)

    @property
    def arraysize(self) -> int:
        return cast(int, self._cursor.arraysize)

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        self._cursor.arraysize = value

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def rownumber(self) -> int:
        return cast(int, self._cursor.rownumber)

    @property
    def with_rows(self) -> bool:
        """是否持有结果集（SELECT 类语句执行后为 True）。"""
        return cast(bool, getattr(self._cursor, "with_rows", False))

    @property
    def column_names(self) -> list[str]:
        """当前结果集的列名列表。"""
        return cast(list[str], getattr(self._cursor, "column_names", []))

    # -- dmPython 兼容属性 --

    @property
    def name(self) -> str:
        """游标名称。"""
        return cast(str, getattr(self._cursor, "name", ""))

    @property
    def scrollable(self) -> bool | None:
        """游标是否可滚动。"""
        return cast(Optional[bool], getattr(self._cursor, "scrollable", None))

    @scrollable.setter
    def scrollable(self, value: bool | None) -> None:
        self._cursor.scrollable = value

    @property
    def withhold(self) -> bool:
        """提交后是否保留结果集。"""
        return cast(bool, getattr(self._cursor, "withhold", False))

    @withhold.setter
    def withhold(self, value: bool) -> None:
        self._cursor.withhold = value

    @property
    def itersize(self) -> int:
        """迭代抓取块大小。"""
        return cast(int, getattr(self._cursor, "itersize", 100))

    @itersize.setter
    def itersize(self, value: int) -> None:
        self._cursor.itersize = value

    @property
    def connection(self) -> AsyncConnection | None:
        """父连接引用。"""
        return self._connection

    @property
    def raw(self) -> Any:
        """底层同步游标对象。"""
        return self._cursor

    @property
    def lastrowid(self) -> int:
        """最后插入行 ID。"""
        return cast(int, getattr(self._cursor, "lastrowid", -1))

    @property
    def query(self) -> str | None:
        """最后执行的 SQL 语句。"""
        return cast(Optional[str], getattr(self._cursor, "query", None))

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"<AsyncCursor({state}) at 0x{id(self):x}>"

    # -- 生命周期 --

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _to_thread(self._cursor.close)

    def __del__(self) -> None:
        if not self._closed:
            try:
                self._cursor.close()
                warnings.warn(
                    f"Unclosed cursor {self!r}",
                    ResourceWarning,
                    stacklevel=2,
                )
            except Exception:
                pass

    def _check_open(self) -> None:
        if self._closed:
            raise _sync_dbapi.InterfaceError("cursor already closed")  # type: ignore[attr-defined]

    # -- 核心 DB-API 2.0 方法 --

    async def execute(
        self,
        operation: str,
        parameters: Sequence[Any] | None = None,
    ) -> AsyncCursor:
        self._check_open()
        await _to_thread(self._cursor.execute, operation, parameters)
        return self

    async def executemany(
        self,
        operation: str,
        seq_of_parameters: Sequence[Sequence[Any]],
    ) -> AsyncCursor:
        self._check_open()
        await _to_thread(self._cursor.executemany, operation, seq_of_parameters)
        return self

    async def fetchone(self) -> Any:
        self._check_open()
        return await _to_thread(self._cursor.fetchone)

    async def fetchmany(self, size: int | None = None) -> list[Any]:
        self._check_open()
        return await _to_thread(self._cursor.fetchmany, size)

    async def fetchall(self) -> list[Any]:
        self._check_open()
        return await _to_thread(self._cursor.fetchall)

    # -- dmPython / 原生后端扩展方法 --

    async def callproc(
        self,
        procname: str,
        parameters: Sequence[Any] | None = None,
    ) -> list[Any]:
        self._check_open()
        return await _to_thread(self._cursor.callproc, procname, parameters)

    async def callfunc(
        self,
        funcname: str,
        returnType: Any,  # noqa: N803
        parameters: Sequence[Any] | None = None,
    ) -> Any:
        self._check_open()
        return await _to_thread(self._cursor.callfunc, funcname, returnType, parameters)

    async def prepare(self, statement: str) -> None:
        self._check_open()
        await _to_thread(self._cursor.prepare, statement)

    async def parse(self, statement: str) -> None:
        """解析 SQL 语句（与 prepare 等价）。"""
        self._check_open()
        await _to_thread(self._cursor.prepare, statement)

    async def nextset(self) -> bool | None:
        self._check_open()
        return await _to_thread(self._cursor.nextset)

    async def setinputsizes(self, sizes: Sequence[Any]) -> None:
        self._check_open()
        await _to_thread(self._cursor.setinputsizes, sizes)

    async def setoutputsize(self, size: int, column: int | None = None) -> None:
        self._check_open()
        await _to_thread(self._cursor.setoutputsize, size, column)

    # -- 原生后端特有方法 --

    async def executedirect(self, operation: str) -> AsyncCursor:
        self._check_open()
        await _to_thread(self._cursor.executedirect, operation)
        return self

    async def scroll(self, value: int, mode: str = "relative") -> None:
        self._check_open()
        await _to_thread(self._cursor.scroll, value, mode)

    async def bindnames(self) -> list[str]:
        """返回命名绑定参数列表。"""
        self._check_open()
        return await _to_thread(self._cursor.bindnames)

    def mogrify(self, operation: str, parameters: Sequence[Any] | None = None) -> str:
        """格式化 SQL 语句和参数（同步方法）。"""
        return cast(str, self._cursor.mogrify(operation, parameters))

    # -- 事务 --

    def begin(
        self,
        isolation_level: IsolationLevel = IsolationLevel.default,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> _ContextManager[AsyncTransaction]:
        """开始事务，返回可 await / async with 的上下文管理器。

        用法::

            async with cur.begin():
                await cur.execute("INSERT INTO t VALUES (1)")

            # 指定隔离级别
            async with cur.begin(IsolationLevel.serializable):
                ...
        """
        txn = AsyncTransaction(
            self, isolation_level=isolation_level, readonly=readonly, deferrable=deferrable
        )
        return _ContextManager[AsyncTransaction](
            txn.begin(),
            _commit_transaction,
            _rollback_transaction,
        )

    def begin_nested(
        self,
        isolation_level: IsolationLevel = IsolationLevel.default,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> _ContextManager[AsyncTransaction]:
        """开始嵌套事务（保存点）。"""
        txn = AsyncTransaction(
            self, isolation_level=isolation_level, readonly=readonly, deferrable=deferrable
        )
        if self._transaction.is_begin:
            if self._transaction._unique_id is not None:
                raise RuntimeError("A savepoint is already active for this cursor")
            return txn._savepoint()
        return _ContextManager[AsyncTransaction](
            txn.begin(),
            _commit_transaction,
            _rollback_transaction,
        )

    # -- 异步迭代 --

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        row = await self.fetchone()
        if row is not None:
            return row
        raise StopAsyncIteration

    # -- 异步上下文管理器 --

    async def __aenter__(self) -> AsyncCursor:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- 后备属性访问 --

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._cursor, name)


# ---------------------------------------------------------------------------
# AsyncConnection
# ---------------------------------------------------------------------------


class AsyncConnection:
    """异步连接，包装同步 Connection。

    将同步连接的所有阻塞操作（commit, rollback, cursor, ping, debug, shutdown,
    explain 等）通过 ``_to_thread`` 委托到线程池中执行，提供非阻塞的异步接口。

    支持异步上下文管理器，正常退出时自动 commit，异常退出时自动 rollback 并 close。

    实现了 DB-API 2.0 兼容的异步连接接口，同时支持 dmPython 扩展属性：
    - ``raw``: 底层同步连接对象
    - ``loop``: 当前事件循环
    - ``last_usage``: 最后使用时间戳
    - ``timeout``: 连接超时
    - ``encoding``: 编码
    - ``server_version``: 服务器版本
    - ``status``: 连接状态
    - ``autocommit``: 自动提交模式

    用法::

        # 基本连接
        conn = await connect(user="SYSDBA", password="xxx", server="127.0.0.1")
        async with conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                print(await cur.fetchone())

        # 事务（自动 commit/rollback）
        async with conn.cursor() as cur:
            async with cur.begin():
                await cur.execute("INSERT INTO t VALUES (1)")

        # 连接池
        pool = await create_pool(user="SYSDBA", password="xxx", minsize=2, maxsize=10)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                ...
    """

    def __init__(self, connection: Any) -> None:
        self._conn = connection
        self._closed = False
        self._last_usage = 0.0
        try:
            self._loop = get_running_loop()
        except RuntimeError:
            self._loop = None  # type: ignore[assignment]

    @property
    def _current_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            return get_running_loop()
        return self._loop

    # -- 生命周期 --

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _to_thread(self._conn.close)

    async def disconnect(self) -> None:
        """断开连接（与 close 等价）。"""
        await self.close()

    async def ensure_closed(self) -> None:
        """确保连接已关闭（dmAsync 兼容）。"""
        if not self._closed:
            await self.close()

    async def commit(self) -> None:
        await _to_thread(self._conn.commit)

    async def rollback(self) -> None:
        await _to_thread(self._conn.rollback)

    async def cursor(self, cursorclass: int | None = None) -> AsyncCursor:
        """创建异步游标。

        用法::

            async with await conn.cursor() as cur:
                await cur.execute("SELECT 1")
        """
        self._last_usage = self._current_loop.time()
        sync_cursor = await _to_thread(self._conn.cursor, cursorclass)
        return AsyncCursor(sync_cursor, connection=self)

    async def ping(self, reconnect: int = 0) -> None:
        await _to_thread(self._conn.ping, reconnect)

    # -- 原生后端特有方法 --

    async def debug(self, level: int = 1) -> None:
        await _to_thread(self._conn.debug, level)

    async def shutdown(self, mode: str = "NORMAL") -> None:
        await _to_thread(self._conn.shutdown, mode)

    async def explain(self, sql: str) -> str | None:
        return await _to_thread(self._conn.explain, sql)

    # -- 属性代理 --

    @property
    def autocommit(self) -> bool:
        return cast(bool, self._conn.autocommit)

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._conn.autocommit = value

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dsn(self) -> str | None:
        return cast(Optional[str], self._conn.dsn)

    @property
    def user(self) -> str:
        return cast(str, self._conn.user)

    @property
    def server(self) -> str:
        return cast(str, self._conn.server)

    @property
    def port(self) -> int:
        return cast(int, self._conn.port)

    @property
    def raw(self) -> Any:
        """底层同步连接对象。"""
        return self._conn

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """当前运行的事件循环。"""
        return self._current_loop

    @property
    def last_usage(self) -> float:
        """最后使用时间戳。"""
        return self._last_usage

    # -- dmPython 兼容属性 --

    @property
    def timeout(self) -> float:
        """连接超时（秒）。"""
        return cast(float, getattr(self._conn, "timeout", 0.0))

    @property
    def encoding(self) -> str:
        """客户端编码。"""
        return cast(str, getattr(self._conn, "local_code", "UTF-8"))

    @property
    def server_version(self) -> int:
        """服务器版本号。"""
        return cast(int, getattr(self._conn, "server_version", 0))

    @property
    def status(self) -> int:
        """连接状态。"""
        return cast(int, getattr(self._conn, "status", 0))

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"<AsyncConnection({state}) at 0x{id(self):x}>"

    # -- 异步上下文管理器 --

    async def __aenter__(self) -> AsyncConnection:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            with contextlib.suppress(Exception):
                await self.rollback()
        await self.close()

    # -- 后备属性访问 --

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._conn, name)

    # -- 资源泄漏检测 --

    def __del__(self) -> None:
        if not self._closed:
            try:
                self._conn.ping()
                warnings.warn(
                    f"Unclosed connection {self!r}",
                    ResourceWarning,
                    stacklevel=2,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


async def connect(
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    server: str | None = None,
    port: int = 5236,
    dsn: str | None = None,
    autoCommit: bool = True,  # noqa: N803
    connection_timeout: int = 0,
    login_timeout: int = 5,
    **kwargs: Any,
) -> AsyncConnection:
    """异步创建达梦数据库连接。

    参数:
        user: 用户名
        password: 密码
        host: 数据库主机地址
        server: 数据库主机地址（与 host 等价）
        port: 端口号（默认 5236）
        dsn: DSN 连接字符串（指定后将忽略 host/server/port）
        autoCommit: 是否自动提交（默认 True）
        connection_timeout: 连接超时（秒，0 表示不限制）
        login_timeout: 登录超时（秒，默认 5）
        **kwargs: 传递给底层驱动的其他参数

    用法::

        conn = await connect(user="SYSDBA", password="xxx", server="127.0.0.1")
        async with conn:
            async with await conn.cursor() as cur:
                await cur.execute("SELECT 1")
                print(await cur.fetchone())
    """
    conn = await _to_thread(
        _sync_dbapi.connect,  # type: ignore[attr-defined]
        user=user,
        password=password,
        host=host,
        server=server,
        port=port,
        dsn=dsn,
        autoCommit=autoCommit,
        connection_timeout=connection_timeout,
        login_timeout=login_timeout,
        **kwargs,
    )
    return AsyncConnection(conn)
