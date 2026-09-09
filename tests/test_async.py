"""ns-pydm 异步模块单元测试。

测试 AsyncCursor / AsyncConnection / AsyncPool / AsyncTransaction
等核心组件的正确性，不依赖真实的达梦数据库连接。
"""

from __future__ import annotations

import asyncio
import unittest

from nspydm._async import (
    AsyncConnection,
    AsyncCursor,
    AsyncPool,
    AsyncTransaction,
    DefaultCompiler,
    IsolationLevel,
    connect,
    create_pool,
    get_running_loop,
)
from nspydm._async.utils import _ContextManager, _to_thread
from nspydm.dbapi import InterfaceError  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Mock 桩
# ---------------------------------------------------------------------------


class MockCursor:
    """模拟同步 Cursor。"""

    def __init__(self) -> None:
        self.description = None
        self.rowcount = -1
        self.arraysize = 1
        self.rownumber = 0
        self._closed = False
        self._data: list[tuple] = []
        self._fetch_index = 0
        self.name = "mock_cursor"
        self.scrollable = None
        self.withhold = False
        self.itersize = 100

    def close(self) -> None:
        self._closed = True

    def execute(self, operation: str, parameters: list | None = None) -> MockCursor:
        self._data = [(1,), (2,), (3,)]
        self._fetch_index = 0
        self.rowcount = 3
        self.description = (("x",),)
        return self

    def fetchone(self) -> tuple | None:
        if self._fetch_index >= len(self._data):
            return None
        row = self._data[self._fetch_index]
        self._fetch_index += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[tuple]:
        result: list[tuple] = []
        for _ in range(size or 1):
            row = self.fetchone()
            if row is None:
                break
            result.append(row)
        return result

    def fetchall(self) -> list[tuple]:
        return self._data[self._fetch_index:]

    def executemany(self, operation: str, seq_of_parameters: list) -> MockCursor:
        self.rowcount = len(seq_of_parameters)
        return self

    def callproc(self, procname: str, parameters: list | None = None) -> list:
        return parameters or []

    def callfunc(self, funcname: str, returnType: object, parameters: list | None = None) -> object:  # noqa: N803
        return 42

    def prepare(self, statement: str) -> None:
        pass

    def nextset(self) -> bool | None:
        return None

    def setinputsizes(self, sizes: list) -> None:
        pass

    def setoutputsize(self, size: int, column: int | None = None) -> None:
        pass

    def mogrify(self, operation: str, parameters: list | None = None) -> str:
        params_str = " ".join(str(p) for p in (parameters or []))
        return f"{operation} {params_str}".strip()

    def executedirect(self, operation: str) -> MockCursor:
        return self.execute(operation)

    def scroll(self, value: int, mode: str = "relative") -> None:
        pass

    def bindnames(self) -> list[str]:
        return []


class MockConnection:
    """模拟同步 Connection。"""

    def __init__(self) -> None:
        self._closed = False
        self.autocommit = False
        self.dsn = "127.0.0.1:5236"
        self.user = "SYSDBA"
        self.server = "127.0.0.1"
        self.port = 5236

    def close(self) -> None:
        self._closed = True

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def cursor(self, cursorclass: int | None = None) -> MockCursor:
        return MockCursor()

    def ping(self, reconnect: int = 0) -> None:
        pass

    def debug(self, level: int = 1) -> None:
        pass

    def shutdown(self, mode: str = "NORMAL") -> None:
        pass

    def explain(self, sql: str) -> str | None:
        return "EXPLAIN PLAN"


# ============================================================================
# 测试用例
# ============================================================================


class TestUtils(unittest.TestCase):
    """_ContextManager / _to_thread 基础工具测试。"""

    def test_get_running_loop(self) -> None:
        async def run() -> None:
            loop = get_running_loop()
            self.assertIsNotNone(loop)
            self.assertTrue(loop.is_running())

        asyncio.run(run())

    def test_to_thread(self) -> None:
        async def run() -> None:
            def sync_add(x: int, y: int = 10) -> int:
                return x + y

            result = await _to_thread(sync_add, 5, y=20)
            self.assertEqual(result, 25)

            result = await _to_thread(sync_add, 3)
            self.assertEqual(result, 13)

        asyncio.run(run())

    def test_context_manager_await(self) -> None:
        async def run() -> None:
            async def acquire() -> str:
                return "hello"

            async def release(r: str) -> None:
                pass

            cm = _ContextManager(acquire(), release)
            result = await cm
            self.assertEqual(result, "hello")

        asyncio.run(run())

    def test_context_manager_async_with(self) -> None:
        async def run() -> None:
            release_called = False

            async def acquire() -> str:
                return "hello"

            async def release(r: str) -> None:
                nonlocal release_called
                release_called = True

            async with _ContextManager(acquire(), release) as r:
                self.assertEqual(r, "hello")

            self.assertTrue(release_called)

        asyncio.run(run())

    def test_context_manager_exception(self) -> None:
        async def run() -> None:
            release_on_exception_called = False

            async def acquire() -> str:
                return "hello"

            async def release(r: str) -> None:
                pass

            async def release_on_exc(r: str) -> None:
                nonlocal release_on_exception_called
                release_on_exception_called = True

            cm = _ContextManager(acquire(), release, release_on_exc)
            with self.assertRaises(ValueError):
                async with cm:
                    raise ValueError("test error")

            self.assertTrue(release_on_exception_called)

        asyncio.run(run())


class TestAsyncCursor(unittest.TestCase):
    """AsyncCursor 基本功能测试。"""

    def setUp(self) -> None:
        self.mock_cursor = MockCursor()
        self.cursor = AsyncCursor(self.mock_cursor)

    def test_properties(self) -> None:
        self.assertEqual(self.cursor.rowcount, -1)
        self.assertEqual(self.cursor.arraysize, 1)
        self.assertFalse(self.cursor.closed)
        self.assertEqual(self.cursor.rownumber, 0)

        self.cursor.arraysize = 10
        self.assertEqual(self.cursor.arraysize, 10)

    def test_raw_property(self) -> None:
        self.assertIs(self.cursor.raw, self.mock_cursor)

    def test_lastrowid_property(self) -> None:
        self.assertEqual(self.cursor.lastrowid, -1)

    def test_query_property(self) -> None:
        self.assertIsNone(self.cursor.query)

    def test_connection_property(self) -> None:
        self.assertIsNone(self.cursor.connection)

    def test_cursor_repr(self) -> None:
        self.assertIn("AsyncCursor", repr(self.cursor))
        self.assertIn("open", repr(self.cursor))

    def test_name_property(self) -> None:
        self.assertEqual(self.cursor.name, "mock_cursor")

    def test_scrollable_property(self) -> None:
        self.assertIsNone(self.cursor.scrollable)

    def test_scrollable_setter(self) -> None:
        self.cursor.scrollable = True
        self.assertTrue(self.mock_cursor.scrollable)

    def test_withhold_property(self) -> None:
        self.assertFalse(self.cursor.withhold)

    def test_withhold_setter(self) -> None:
        self.cursor.withhold = True
        self.assertTrue(self.mock_cursor.withhold)

    def test_itersize_property(self) -> None:
        self.assertEqual(self.cursor.itersize, 100)

    def test_itersize_setter(self) -> None:
        self.cursor.itersize = 200
        self.assertEqual(self.mock_cursor.itersize, 200)

    def test_close(self) -> None:
        async def run() -> None:
            self.assertFalse(self.cursor.closed)
            self.assertFalse(self.mock_cursor._closed)

            await self.cursor.close()

            self.assertTrue(self.cursor.closed)
            self.assertTrue(self.mock_cursor._closed)

        asyncio.run(run())

    def test_close_idempotent(self) -> None:
        async def run() -> None:
            await self.cursor.close()
            await self.cursor.close()  # should not raise
            self.assertTrue(self.cursor.closed)

        asyncio.run(run())

    def test_execute(self) -> None:
        async def run() -> None:
            result = await self.cursor.execute("SELECT ?", [1])
            self.assertIs(result, self.cursor)
            self.assertEqual(self.cursor.rowcount, 3)

        asyncio.run(run())

    def test_fetchone(self) -> None:
        async def run() -> None:
            await self.cursor.execute("SELECT ?", [1])
            row = await self.cursor.fetchone()
            self.assertEqual(row, (1,))

        asyncio.run(run())

    def test_fetchmany(self) -> None:
        async def run() -> None:
            await self.cursor.execute("SELECT ?", [1])
            rows = await self.cursor.fetchmany(2)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows, [(1,), (2,)])

        asyncio.run(run())

    def test_fetchall(self) -> None:
        async def run() -> None:
            await self.cursor.execute("SELECT ?", [1])
            rows = await self.cursor.fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows, [(1,), (2,), (3,)])

        asyncio.run(run())

    def test_async_iteration(self) -> None:
        async def run() -> None:
            await self.cursor.execute("SELECT ?", [1])
            results: list[tuple] = []
            async for row in self.cursor:
                results.append(row)
            self.assertEqual(len(results), 3)

        asyncio.run(run())

    def test_async_context_manager(self) -> None:
        async def run() -> None:
            async with AsyncCursor(MockCursor()) as cur:
                self.assertIsInstance(cur, AsyncCursor)
                await cur.execute("SELECT 1")
            self.assertTrue(cur.closed)

    def test_mogrify(self) -> None:
        sql = self.cursor.mogrify("SELECT ?", [1])
        self.assertEqual(sql, "SELECT ? 1")

    def test_execute_after_close_raises(self) -> None:
        async def run() -> None:
            await self.cursor.close()
            with self.assertRaises(InterfaceError):
                await self.cursor.execute("SELECT 1")

        asyncio.run(run())

    def test_del_resource_warning(self) -> None:
        """未显式关闭的游标在析构时应发出 ResourceWarning。"""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cursor = AsyncCursor(MockCursor())
            del cursor
            # Force garbage collection
            import gc
            gc.collect()
            # Should have emitted ResourceWarning
            resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
            self.assertTrue(len(resource_warnings) > 0)

    def test_callproc(self) -> None:
        async def run() -> None:
            result = await self.cursor.callproc("my_proc", [1, 2])
            self.assertEqual(result, [1, 2])

        asyncio.run(run())

    def test_callfunc(self) -> None:
        async def run() -> None:
            result = await self.cursor.callfunc("my_func", int, [1])
            self.assertEqual(result, 42)

        asyncio.run(run())

    def test_prepare_and_parse(self) -> None:
        async def run() -> None:
            await self.cursor.prepare("SELECT ? FROM dual")
            await self.cursor.parse("SELECT ? FROM dual")

        asyncio.run(run())

    def test_setinputsizes_setoutputsize(self) -> None:
        async def run() -> None:
            await self.cursor.setinputsizes([1])
            await self.cursor.setoutputsize(100, 0)

        asyncio.run(run())

    def test_scroll(self) -> None:
        async def run() -> None:
            await self.cursor.scroll(0, "absolute")

        asyncio.run(run())

    def test_bindnames(self) -> None:
        async def run() -> None:
            names = await self.cursor.bindnames()
            self.assertEqual(names, [])

        asyncio.run(run())

    def test_with_rows_property(self) -> None:
        self.assertFalse(self.cursor.with_rows)

    def test_column_names_property(self) -> None:
        self.assertEqual(self.cursor.column_names, [])


class TestAsyncConnection(unittest.TestCase):
    """AsyncConnection 基本功能测试。"""

    def setUp(self) -> None:
        self.mock_conn = MockConnection()
        self.conn = AsyncConnection(self.mock_conn)

    def test_properties(self) -> None:
        self.assertFalse(self.conn.closed)
        self.assertEqual(self.conn.user, "SYSDBA")
        self.assertEqual(self.conn.server, "127.0.0.1")
        self.assertEqual(self.conn.port, 5236)
        self.assertEqual(self.conn.dsn, "127.0.0.1:5236")
        self.assertFalse(self.conn.autocommit)

    def test_raw_property(self) -> None:
        self.assertIs(self.conn.raw, self.mock_conn)

    def test_last_usage_property(self) -> None:
        self.assertEqual(self.conn.last_usage, 0.0)

    def test_connection_repr(self) -> None:
        self.assertIn("AsyncConnection", repr(self.conn))
        self.assertIn("open", repr(self.conn))

    def test_timeout_property(self) -> None:
        self.assertEqual(self.conn.timeout, 0.0)

    def test_encoding_property(self) -> None:
        self.assertEqual(self.conn.encoding, "UTF-8")

    def test_server_version_property(self) -> None:
        self.assertEqual(self.conn.server_version, 0)

    def test_status_property(self) -> None:
        self.assertEqual(self.conn.status, 0)

    def test_loop_property(self) -> None:
        async def run() -> None:
            import asyncio
            loop = asyncio.get_running_loop()
            self.assertIs(self.conn.loop, loop)

        asyncio.run(run())

    def test_autocommit_setter(self) -> None:
        self.conn.autocommit = True
        self.assertTrue(self.conn.autocommit)
        self.assertTrue(self.mock_conn.autocommit)

    def test_close(self) -> None:
        async def run() -> None:
            self.assertFalse(self.conn.closed)
            await self.conn.close()
            self.assertTrue(self.conn.closed)

        asyncio.run(run())

    def test_disconnect(self) -> None:
        async def run() -> None:
            await self.conn.disconnect()
            self.assertTrue(self.conn.closed)

        asyncio.run(run())

    def test_commit_and_rollback(self) -> None:
        async def run() -> None:
            await self.conn.commit()
            await self.conn.rollback()

        asyncio.run(run())

    def test_cursor(self) -> None:
        async def run() -> None:
            cursor = await self.conn.cursor()
            self.assertIsInstance(cursor, AsyncCursor)
            self.assertIs(cursor.connection, self.conn)

        asyncio.run(run())

    def test_ping(self) -> None:
        async def run() -> None:
            await self.conn.ping()

        asyncio.run(run())

    def test_debug_shutdown_explain(self) -> None:
        async def run() -> None:
            await self.conn.debug(1)
            await self.conn.shutdown("NORMAL")
            plan = await self.conn.explain("SELECT 1")
            self.assertEqual(plan, "EXPLAIN PLAN")

        asyncio.run(run())

    def test_async_context_manager(self) -> None:
        async def run() -> None:
            async with AsyncConnection(MockConnection()) as conn:
                self.assertIsInstance(conn, AsyncConnection)
            self.assertTrue(conn.closed)

        asyncio.run(run())

    def test_ensure_closed(self) -> None:
        async def run() -> None:
            await self.conn.ensure_closed()
            self.assertTrue(self.conn.closed)

        asyncio.run(run())

    def test_context_manager_rollback_on_error(self) -> None:
        """异常退出 async with 时应回滚。"""
        async def run() -> None:
            conn = AsyncConnection(MockConnection())
            with self.assertRaises(RuntimeError):
                async with conn:
                    raise RuntimeError("oops")
            self.assertTrue(conn.closed)

        asyncio.run(run())

    def test_getattr_proxy(self) -> None:
        """未显式代理的属性通过 __getattr__ 访问。"""
        self.assertEqual(self.conn.dsn, "127.0.0.1:5236")

    def test_getattr_private_raises(self) -> None:
        with self.assertRaises(AttributeError):
            _ = self.conn._secret_field

    def test_close_idempotent(self) -> None:
        async def run() -> None:
            await self.conn.close()
            await self.conn.close()

        asyncio.run(run())


class TestIsolationLevel(unittest.TestCase):
    """IsolationLevel 枚举和编译器测试。"""

    def test_default_compiler(self) -> None:
        compiler = IsolationLevel.default()
        self.assertIsInstance(compiler, DefaultCompiler)

    def test_serializable_compiler(self) -> None:
        compiler = IsolationLevel.serializable()
        sql = compiler.begin()
        self.assertIn("SERIALIZABLE", sql)
        self.assertIn("READ WRITE", sql)

    def test_read_committed_compiler(self) -> None:
        compiler = IsolationLevel.read_committed(readonly=True)
        sql = compiler.begin()
        self.assertIn("READ COMMITTED", sql)
        self.assertIn("READ ONLY", sql)

    def test_repeatable_read_compiler(self) -> None:
        compiler = IsolationLevel.repeatable_read()
        sql = compiler.begin()
        self.assertIn("REPEATABLE READ", sql)

    def test_savepoint_roundtrip(self) -> None:
        compiler = IsolationLevel.default()
        sp = compiler.savepoint("sp1")
        self.assertEqual(sp, "SAVEPOINT sp1")
        rsp = compiler.rollback_savepoint("sp1")
        self.assertEqual(rsp, "ROLLBACK TO SAVEPOINT sp1")
        rel = compiler.release_savepoint("sp1")
        self.assertEqual(rel, "RELEASE SAVEPOINT sp1")

    def test_isolation_level_compile_with_params(self) -> None:
        """测试带参数的隔离级别编译。"""
        # Serializable with readonly/deferrable
        compiler = IsolationLevel.serializable(readonly=True, deferrable=True)
        sql = compiler.begin()
        self.assertIn("SERIALIZABLE", sql)
        self.assertIn("READ ONLY", sql)
        self.assertIn("DEFERRABLE", sql)

        # Repeatable read with readonly
        compiler = IsolationLevel.repeatable_read(readonly=True)
        sql = compiler.begin()
        self.assertIn("REPEATABLE READ", sql)
        self.assertIn("READ ONLY", sql)

    def test_isolation_level_default_callable(self) -> None:
        """IsolationLevel.default 可作为可调用对象使用。"""
        compiler = IsolationLevel.default()
        self.assertIsInstance(compiler, DefaultCompiler)
        sql = compiler.begin()
        self.assertEqual(sql, "START TRANSACTION")


class TestAsyncTransaction(unittest.TestCase):
    """AsyncTransaction 事务管理测试（使用 mock cursor）。"""

    def setUp(self) -> None:
        self.mock_cursor = MockCursor()
        self.acursor = AsyncCursor(self.mock_cursor)
        self.txn = AsyncTransaction(self.acursor)

    def test_not_begin_by_default(self) -> None:
        self.assertFalse(self.txn.is_begin)

    def test_transaction_repr(self) -> None:
        self.assertIn("AsyncTransaction", repr(self.txn))
        self.assertIn("idle", repr(self.txn))

    def test_transaction_repr_after_begin(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            self.assertIn("begun", repr(self.txn))

        asyncio.run(run())

    def test_begin_twice_raises(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            with self.assertRaises(RuntimeError):
                await self.txn.begin()

        asyncio.run(run())

    def test_begin_and_commit(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            self.assertTrue(self.txn.is_begin)
            await self.txn.commit()
            self.assertFalse(self.txn.is_begin)

        asyncio.run(run())

    def test_begin_and_rollback(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            await self.txn.rollback()
            self.assertFalse(self.txn.is_begin)

        asyncio.run(run())

    def test_commit_without_begin_raises(self) -> None:
        async def run() -> None:
            with self.assertRaises(RuntimeError):
                await self.txn.commit()

        asyncio.run(run())

    def test_rollback_without_begin_raises(self) -> None:
        async def run() -> None:
            with self.assertRaises(RuntimeError):
                await self.txn.rollback()

        asyncio.run(run())

    def test_savepoint(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            sp = await self.txn.savepoint()
            self.assertIs(sp, self.txn)
            self.assertIsNotNone(self.txn._unique_id)
            await self.txn.release_savepoint()

        asyncio.run(run())

    def test_savepoint_twice_raises(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            await self.txn.savepoint()
            with self.assertRaises(RuntimeError):
                await self.txn.savepoint()

        asyncio.run(run())

    def test_savepoint_rollback(self) -> None:
        async def run() -> None:
            await self.txn.begin()
            await self.txn.savepoint()
            await self.txn.rollback_savepoint()
            # 回滚后不能再 release，unique_id 已清空
            with self.assertRaises(RuntimeError):
                await self.txn.release_savepoint()

        asyncio.run(run())

    def test_rollback_savepoint_without_name_raises(self) -> None:
        async def run() -> None:
            with self.assertRaises(RuntimeError):
                await self.txn.rollback_savepoint()

        asyncio.run(run())

    def test_savepoint_without_begin_raises(self) -> None:
        async def run() -> None:
            with self.assertRaises(RuntimeError):
                await self.txn.savepoint()

        asyncio.run(run())

    def test_async_context_manager_commit_on_success(self) -> None:
        async def run() -> None:
            async with self.acursor.begin() as txn:
                self.assertIsInstance(txn, AsyncTransaction)
                self.assertTrue(txn.is_begin)
            self.assertFalse(self.txn.is_begin)

        asyncio.run(run())

    def test_async_context_manager_rollback_on_error(self) -> None:
        async def run() -> None:
            with self.assertRaises(ValueError):
                async with self.acursor.begin():
                    raise ValueError("test")

        asyncio.run(run())

    def test_begin_method_returns_context_manager(self) -> None:
        async def run() -> None:
            cm = self.acursor.begin()
            self.assertIsInstance(cm, _ContextManager)
            # Also verify it can be used
            async with cm as txn:
                self.assertTrue(txn.is_begin)

        asyncio.run(run())

    def test_begin_nested(self) -> None:
        async def run() -> None:
            cm = self.acursor.begin_nested()
            self.assertIsInstance(cm, _ContextManager)
            async with cm:
                pass

        asyncio.run(run())

    def test_savepoint_context_manager(self) -> None:
        """_savepoint() 返回的上下文管理器自动处理 commit/rollback。"""
        async def run() -> None:
            await self.txn.begin()
            async with self.txn._savepoint():
                pass  # 正常退出，自动 release
            self.assertIsNone(self.txn._unique_id)

        asyncio.run(run())

    def test_savepoint_context_manager_rollback_on_error(self) -> None:
        """_savepoint() 上下文管理器异常时回滚。"""
        async def run() -> None:
            await self.txn.begin()
            with self.assertRaises(ValueError):
                async with self.txn._savepoint():
                    raise ValueError("test")
            # 保存点已被回滚
            self.assertIsNone(self.txn._unique_id)

        asyncio.run(run())

    def test_cursor_begin_with_isolation(self) -> None:
        """cursor.begin() 支持指定隔离级别。"""
        async def run() -> None:
            async with self.acursor.begin(IsolationLevel.serializable) as txn:
                self.assertTrue(txn.is_begin)
            self.assertFalse(self.txn.is_begin)

        asyncio.run(run())

    def test_cursor_begin_with_readonly_deferrable(self) -> None:
        """cursor.begin() 支持 readonly/deferrable 参数。"""
        async def run() -> None:
            async with self.acursor.begin(IsolationLevel.repeatable_read, readonly=True, deferrable=True) as txn:
                self.assertTrue(txn.is_begin)
            self.assertFalse(self.txn.is_begin)

        asyncio.run(run())


class TestAsyncPool(unittest.TestCase):
    """AsyncPool 单元测试（使用 mock）。"""

    def test_init_validates_params(self) -> None:
        with self.assertRaises(ValueError):
            AsyncPool(-1, 10, 30)

        with self.assertRaises(ValueError):
            AsyncPool(1, 0, 30)

        with self.assertRaises(ValueError):
            AsyncPool(5, 3, 30)

    def test_init_success(self) -> None:
        pool = AsyncPool(1, 10, 30)
        self.assertEqual(pool.minsize, 1)
        self.assertEqual(pool.maxsize, 10)
        self.assertFalse(pool.closed)

    def test_properties(self) -> None:
        pool = AsyncPool(2, 20, 60)
        self.assertEqual(pool.minsize, 2)
        self.assertEqual(pool.maxsize, 20)
        self.assertEqual(pool.size, 0)
        self.assertEqual(pool.freesize, 0)

    def test_close_and_repr(self) -> None:
        pool = AsyncPool(1, 10, 30)
        pool.close()
        self.assertIn("closing", repr(pool))
        self.assertFalse(pool.closed)

    def test_repr_open(self) -> None:
        pool = AsyncPool(1, 10, 30)
        self.assertIn("open", repr(pool))

    def test_cursor_direct_async_with(self) -> None:
        """pool.cursor() 直接支持 async with（无需 await）。"""
        async def run() -> None:
            pool = AsyncPool(1, 10, 30)

            # Mock _acquire to return a MockConnection
            async def mock_acquire():
                mock_conn = MockConnection()
                return AsyncConnection(mock_conn)

            pool._acquire = mock_acquire  # type: ignore[method-assign]
            pool._return_connection = lambda c: None  # type: ignore[method-assign]

            async with pool.cursor() as cur:
                self.assertIsInstance(cur, AsyncCursor)
                await cur.execute("SELECT 1")

        asyncio.run(run())

    def test_acquire_context_manager(self) -> None:
        """acquire() 返回上下文管理器。"""
        async def run() -> None:
            pool = AsyncPool(1, 10, 30)

            # Mock _acquire to return a MockConnection
            async def mock_acquire():
                mock_conn = MockConnection()
                return AsyncConnection(mock_conn)

            pool._acquire = mock_acquire  # type: ignore[method-assign]
            pool._return_connection = lambda c: None  # type: ignore[method-assign]

            async with pool.acquire() as conn:
                self.assertIsInstance(conn, AsyncConnection)

        asyncio.run(run())

    def test_metrics_tracking(self) -> None:
        """get_stats() 跟踪获取/释放/超时/无效连接计数。"""
        async def run() -> None:
            pool = AsyncPool(1, 10, 30, validate_on_acquire=False)

            acquire_count = 0

            async def mock_acquire():
                nonlocal acquire_count
                acquire_count += 1
                mock_conn = MockConnection()
                return AsyncConnection(mock_conn)

            pool._acquire = mock_acquire  # type: ignore[method-assign]
            pool._return_connection = lambda c: None  # type: ignore[method-assign]

            # 获取并释放 3 次
            for _ in range(3):
                async with pool.acquire():
                    pass

            # 验证 mock_acquire 被调用了 3 次
            self.assertEqual(acquire_count, 3)

        asyncio.run(run())

    def test_validate_on_acquire(self) -> None:
        """validate_on_acquire=True 时会 ping 验证连接。"""
        async def run() -> None:
            pool = AsyncPool(1, 10, 30, validate_on_acquire=True)

            call_count = 0

            async def mock_acquire():
                nonlocal call_count
                call_count += 1
                mock_conn = MockConnection()
                return AsyncConnection(mock_conn)

            pool._acquire = mock_acquire  # type: ignore[method-assign]
            pool._return_connection = lambda c: None  # type: ignore[method-assign]

            async with pool.acquire():
                pass

            # 验证 ping 被调用了
            self.assertEqual(call_count, 1)

        asyncio.run(run())

    def test_validate_on_acquire_false(self) -> None:
        """validate_on_acquire=False 时不验证连接（更快）。"""
        async def run() -> None:
            pool = AsyncPool(1, 10, 30, validate_on_acquire=False)

            call_count = 0

            async def mock_acquire():
                nonlocal call_count
                call_count += 1
                mock_conn = MockConnection()
                return AsyncConnection(mock_conn)

            pool._acquire = mock_acquire  # type: ignore[method-assign]
            pool._return_connection = lambda c: None  # type: ignore[method-assign]

            async with pool.acquire():
                pass

            # 验证 ping 未被调用
            self.assertEqual(call_count, 1)

        asyncio.run(run())


class TestConnectFunction(unittest.TestCase):
    """connect() 函数测试。"""

    def test_is_coroutine_function(self) -> None:
        self.assertTrue(asyncio.iscoroutinefunction(connect))

    def test_signature(self) -> None:
        import inspect
        sig = inspect.signature(connect)
        params = sig.parameters
        for name in ("user", "password", "host", "server", "port", "dsn",
                     "autoCommit", "connection_timeout", "login_timeout"):
            self.assertIn(name, params, f"connect() missing param {name}")


class TestCreatePoolFunction(unittest.TestCase):
    """create_pool() 函数测试。"""

    def test_is_coroutine_function(self) -> None:
        self.assertTrue(asyncio.iscoroutinefunction(create_pool))

    def test_signature(self) -> None:
        import inspect
        sig = inspect.signature(create_pool)
        params = sig.parameters
        self.assertIn("minsize", params)
        self.assertIn("maxsize", params)
        self.assertIn("timeout", params)
        self.assertIn("pool_recycle", params)

    def test_defaults(self) -> None:
        import inspect
        sig = inspect.signature(create_pool)
        minsize_default = sig.parameters["minsize"].default
        self.assertEqual(minsize_default, 1)
        maxsize_default = sig.parameters["maxsize"].default
        self.assertEqual(maxsize_default, 10)

    def test_with_pool_recycle(self) -> None:
        import inspect
        sig = inspect.signature(create_pool)
        self.assertEqual(sig.parameters["pool_recycle"].default, -1.0)


class TestModuleExports(unittest.TestCase):
    """模块导出完整性测试。"""

    def test_all_exports(self) -> None:
        from nspydm._async import __all__ as exported
        expected = {
            "connect",
            "create_pool",
            "get_running_loop",
            "AsyncConnection",
            "AsyncCursor",
            "AsyncTransaction",
            "AsyncPool",
            "IsolationLevel",
            "DefaultCompiler",
            "ReadCommittedCompiler",
            "RepeatableReadCompiler",
            "SerializableCompiler",
            "logger",
        }
        self.assertSetEqual(set(exported), expected)

    def test_nspydm_re_exports(self) -> None:
        from nspydm import async_api, async_connect, async_create_pool

        # async_api 作为模块的别名
        for name in ("connect", "create_pool", "AsyncConnection", "AsyncCursor", "AsyncPool"):
            self.assertTrue(hasattr(async_api, name), f"nspydm.async_api missing {name}")

        # 顶层便捷入口
        self.assertIs(async_connect, async_api.connect)
        self.assertIs(async_create_pool, async_api.create_pool)


# ============================================================================
# 辅助（避免未定义引用）
# ============================================================================



if __name__ == "__main__":
    unittest.main()
