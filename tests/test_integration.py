"""达梦数据库集成测试 — 使用真实数据库测试功能和内存消耗。

连接信息：127.0.0.1:5236 SYSDBA/SYSdba@2026
运行前确保 DMCLIENT_PATH 指向正确的 dmclient 二进制文件。

测试覆盖：
  - 连接管理（connect/close/context manager/autocommit/事务）
  - SQL 执行（CRUD/fetchone/fetchmany/fetchall/executemany/参数类型）
  - 多线程并发安全性
  - 连接池功能
  - 内存消耗监控
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import unittest

import nspydm

# ---------------------------------------------------------------------------
# 连接配置
# ---------------------------------------------------------------------------

DM_HOST = os.environ.get("DM_TEST_HOST", "127.0.0.1")
DM_PORT = int(os.environ.get("DM_TEST_PORT", "5236"))
DM_USER = os.environ.get("DM_TEST_USER", "SYSDBA")
DM_PASSWORD = os.environ.get("DM_TEST_PASSWORD", "SYSdba@2026")


def _connect_kwargs():
    """返回连接参数。"""
    return dict(user=DM_USER, password=DM_PASSWORD, host=DM_HOST, port=DM_PORT)


def _skip_if_no_db(func):
    """装饰器：如果连接失败则跳过测试。"""
    def wrapper(self, *args, **kwargs):
        try:
            conn = nspydm.connect(**_connect_kwargs())
            conn.close()
        except Exception as e:
            self.skipTest(f"无法连接达梦数据库: {e}")
        return func(self, *args, **kwargs)
    return wrapper


def _get_process_memory_mb(pid: int) -> float:
    """获取指定进程的 RSS 内存（MB），macOS/Linux 通用。"""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5
        )
        rss_kb = int(result.stdout.strip().split("\n")[0])
        return rss_kb / 1024.0
    except Exception:
        return -1.0


# ===========================================================================
# 连接管理测试
# ===========================================================================


class TestConnection(unittest.TestCase):
    """连接管理测试。"""

    @_skip_if_no_db
    def test_connect_and_close(self):
        """基本连接和关闭。"""
        conn = nspydm.connect(**_connect_kwargs())
        self.assertFalse(conn._closed)
        conn.close()
        self.assertTrue(conn._closed)

    @_skip_if_no_db
    def test_connect_with_url(self):
        """通过 JDBC URL 连接。"""
        url = f"jdbc:dm://{DM_HOST}:{DM_PORT}"
        conn = nspydm.connect(url=url, user=DM_USER, password=DM_PASSWORD)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        conn.close()

    @_skip_if_no_db
    def test_connect_with_server(self):
        """通过 server 参数连接。"""
        conn = nspydm.connect(user=DM_USER, password=DM_PASSWORD,
                               server=DM_HOST, port=DM_PORT)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        conn.close()

    @_skip_if_no_db
    def test_connect_wrong_password(self):
        """错误密码应抛出 OperationalError。"""
        with self.assertRaises(nspydm.OperationalError):
            nspydm.connect(user=DM_USER, password="wrong_password",
                           host=DM_HOST, port=DM_PORT)

    @_skip_if_no_db
    def test_context_manager_commit(self):
        """上下文管理器正常退出时自动 commit。"""
        with nspydm.connect(**_connect_kwargs()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            row = cur.fetchone()
            self.assertIsNotNone(row)
        # 退出后连接应已关闭
        self.assertTrue(conn._closed)

    @_skip_if_no_db
    def test_context_manager_rollback(self):
        """上下文管理器异常退出时自动 rollback。"""
        try:
            with nspydm.connect(**_connect_kwargs()) as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                raise ValueError("test error")
        except ValueError:
            pass
        self.assertTrue(conn._closed)

    @_skip_if_no_db
    def test_double_close(self):
        """双重 close 不应抛出异常。"""
        conn = nspydm.connect(**_connect_kwargs())
        conn.close()
        conn.close()  # 不应报错

    @_skip_if_no_db
    def test_cursor_on_closed_connection(self):
        """关闭连接后创建 cursor 应抛出 InterfaceError。"""
        conn = nspydm.connect(**_connect_kwargs())
        conn.close()
        with self.assertRaises(nspydm.InterfaceError):
            conn.cursor()

    @_skip_if_no_db
    def test_autocommit_default(self):
        """autocommit 默认值为 False。"""
        conn = nspydm.connect(**_connect_kwargs())
        self.assertFalse(conn.autocommit)
        conn.close()

    @_skip_if_no_db
    def test_autocommit_set(self):
        """设置 autocommit 属性。"""
        conn = nspydm.connect(**_connect_kwargs())
        conn.autocommit = True
        self.assertTrue(conn.autocommit)
        conn.autocommit = False
        self.assertFalse(conn.autocommit)
        conn.close()

    @_skip_if_no_db
    def test_commit_rollback(self):
        """commit 和 rollback 操作。"""
        conn = nspydm.connect(**_connect_kwargs())
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.commit()
        conn.rollback()
        conn.close()


# ===========================================================================
# SQL 执行测试
# ===========================================================================


class TestCursor(unittest.TestCase):
    """SQL 执行和结果获取测试。"""

    TABLE_NAME = "NSPYDM_TEST_TAB"

    def setUp(self):
        try:
            self.conn = nspydm.connect(**_connect_kwargs())
        except Exception as e:
            self.skipTest(f"无法连接达梦数据库: {e}")
        self.cur = self.conn.cursor()
        # 清理可能残留的测试表
        try:
            self.cur.execute(f"DROP TABLE {self.TABLE_NAME}")
            self.conn.commit()
        except Exception:
            pass

    def tearDown(self):
        if hasattr(self, "cur") and self.cur:
            with contextlib.suppress(Exception):
                self.cur.close()
        if hasattr(self, "conn") and self.conn:
            try:
                cur2 = self.conn.cursor()
                cur2.execute(f"DROP TABLE {self.TABLE_NAME}")
                self.conn.commit()
            except Exception:
                pass
            with contextlib.suppress(Exception):
                self.conn.close()

    def test_select_literal(self):
        """SELECT 常量。"""
        self.cur.execute("SELECT 1 AS x")
        row = self.cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)

    def test_select_with_param(self):
        """SELECT 带参数。"""
        self.cur.execute("SELECT ? AS x", [42])
        row = self.cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 42)

    def test_select_multiple_params(self):
        """SELECT 多个参数。"""
        self.cur.execute("SELECT ? AS a, ? AS b, ? AS c", [1, "hello", 3.14])
        row = self.cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "hello")
        self.assertAlmostEqual(row[2], 3.14, places=2)

    def test_create_table(self):
        """CREATE TABLE。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100), score DOUBLE, created_at TIMESTAMP)"
        )
        self.conn.commit()
        # 验证表已创建
        self.cur.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME}")
        row = self.cur.fetchone()
        self.assertEqual(row[0], 0)

    def test_insert_and_select(self):
        """INSERT + SELECT 全流程。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100), score DOUBLE)"
        )
        self.conn.commit()

        self.cur.execute(f"INSERT INTO {self.TABLE_NAME} (id, name, score) VALUES (?, ?, ?)",
                         [1, "Alice", 95.5])
        self.conn.commit()

        self.cur.execute(f"SELECT id, name, score FROM {self.TABLE_NAME} WHERE id = ?", [1])
        row = self.cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "Alice")
        self.assertAlmostEqual(row[2], 95.5)

    def test_update(self):
        """UPDATE 操作。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100), score DOUBLE)"
        )
        self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?)", [1, "Alice", 90.0])
        self.conn.commit()

        self.cur.execute(f"UPDATE {self.TABLE_NAME} SET score = ? WHERE id = ?", [95.0, 1])
        self.conn.commit()

        self.cur.execute(f"SELECT score FROM {self.TABLE_NAME} WHERE id = ?", [1])
        row = self.cur.fetchone()
        self.assertAlmostEqual(row[0], 95.0)

    def test_delete(self):
        """DELETE 操作。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100))"
        )
        self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [1, "Alice"])
        self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [2, "Bob"])
        self.conn.commit()

        self.cur.execute(f"DELETE FROM {self.TABLE_NAME} WHERE id = ?", [1])
        self.conn.commit()

        self.cur.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME}")
        row = self.cur.fetchone()
        self.assertEqual(row[0], 1)

    def test_fetchone(self):
        """fetchone 逐行获取。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, val INT)"
        )
        for i in range(5):
            self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [i, i * 10])
        self.conn.commit()

        self.cur.execute(f"SELECT id, val FROM {self.TABLE_NAME} ORDER BY id")
        rows = []
        while True:
            row = self.cur.fetchone()
            if row is None:
                break
            rows.append(row)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[4][0], 4)

    def test_fetchmany(self):
        """fetchmany 批量获取。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, val INT)"
        )
        for i in range(10):
            self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [i, i])
        self.conn.commit()

        self.cur.execute(f"SELECT id FROM {self.TABLE_NAME} ORDER BY id")
        batch1 = self.cur.fetchmany(3)
        self.assertEqual(len(batch1), 3)
        batch2 = self.cur.fetchmany(3)
        self.assertEqual(len(batch2), 3)
        batch3 = self.cur.fetchmany(10)  # 只剩 4 行
        self.assertEqual(len(batch3), 4)
        batch4 = self.cur.fetchmany(3)  # 无更多数据
        self.assertEqual(len(batch4), 0)

    def test_fetchall(self):
        """fetchall 获取全部。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, val INT)"
        )
        for i in range(5):
            self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [i, i])
        self.conn.commit()

        self.cur.execute(f"SELECT id FROM {self.TABLE_NAME} ORDER BY id")
        rows = self.cur.fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0][0], 0)

    def test_executemany(self):
        """executemany 批量执行。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100))"
        )
        self.conn.commit()

        params = [(i, f"user_{i}") for i in range(10)]
        self.cur.executemany(
            f"INSERT INTO {self.TABLE_NAME} (id, name) VALUES (?, ?)",
            params
        )
        self.conn.commit()

        self.cur.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME}")
        row = self.cur.fetchone()
        self.assertEqual(row[0], 10)

    def test_param_types(self):
        """各种参数类型。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, int_val INT, float_val DOUBLE, str_val VARCHAR(200), null_val INT)"
        )
        self.conn.commit()

        self.cur.execute(
            f"INSERT INTO {self.TABLE_NAME} (id, int_val, float_val, str_val, null_val) VALUES (?, ?, ?, ?, ?)",
            [1, 42, 3.14, "hello world", None]
        )
        self.conn.commit()

        self.cur.execute(f"SELECT int_val, float_val, str_val, null_val FROM {self.TABLE_NAME}")
        row = self.cur.fetchone()
        self.assertEqual(row[0], 42)
        self.assertAlmostEqual(row[1], 3.14, places=2)
        self.assertEqual(row[2], "hello world")
        self.assertIsNone(row[3])

    def test_param_none(self):
        """NULL 参数。"""
        self.cur.execute("SELECT ? AS x", [None])
        row = self.cur.fetchone()
        self.assertIsNone(row[0])

    def test_param_bool(self):
        """布尔参数。"""
        self.cur.execute("SELECT ? AS x", [True])
        row = self.cur.fetchone()
        self.assertIsNotNone(row)

    def test_param_negative(self):
        """负数参数。"""
        self.cur.execute("SELECT ? AS x", [-42])
        row = self.cur.fetchone()
        self.assertEqual(row[0], -42)

    def test_param_large_int(self):
        """大整数参数。"""
        self.cur.execute("SELECT ? AS x", [999999999])
        row = self.cur.fetchone()
        self.assertEqual(row[0], 999999999)

    def test_description(self):
        """cursor.description 属性。"""
        self.cur.execute("SELECT 1 AS id, 'hello' AS name")
        desc = self.cur.description
        self.assertIsNotNone(desc)
        self.assertEqual(len(desc), 2)
        self.assertEqual(desc[0][0], "id")
        self.assertEqual(desc[1][0], "name")

    def test_rowcount_insert(self):
        """INSERT 后 rowcount。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, val INT)"
        )
        self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?)", [1, 100])
        self.assertGreaterEqual(self.cur.rowcount, 1)

    def test_cursor_close(self):
        """cursor.close() 后操作应抛出异常。"""
        self.cur.close()
        with self.assertRaises(nspydm.InterfaceError):
            self.cur.execute("SELECT 1")

    def test_iterator(self):
        """cursor 迭代器。"""
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY)"
        )
        for i in range(3):
            self.cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?)", [i])
        self.conn.commit()

        self.cur.execute(f"SELECT id FROM {self.TABLE_NAME} ORDER BY id")
        ids = [row[0] for row in self.cur]
        self.assertEqual(ids, [0, 1, 2])

    def test_setinputsizes_setoutputsize(self):
        """setinputsizes / setoutputsize 应不报错。"""
        self.cur.setinputsizes([10])
        self.cur.setoutputsize(100)


# ===========================================================================
# 多线程并发测试
# ===========================================================================


class TestMultiThread(unittest.TestCase):
    """多线程并发安全性测试。"""

    @_skip_if_no_db
    def test_concurrent_queries(self):
        """多线程并发查询，验证线程安全。"""
        errors = []
        results = []

        def worker(n):
            try:
                conn = nspydm.connect(**_connect_kwargs())
                cur = conn.cursor()
                cur.execute("SELECT ? AS x", [n])
                row = cur.fetchone()
                results.append((n, row[0]))
                conn.close()
            except Exception as e:
                errors.append((n, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"线程错误: {errors}")
        self.assertEqual(len(results), 5)

    @_skip_if_no_db
    def test_concurrent_same_connection(self):
        """多线程共享同一个连接执行查询（串行化）。"""
        conn = nspydm.connect(**_connect_kwargs())
        errors = []
        results = []
        lock = threading.Lock()

        def worker(n):
            try:
                with lock:
                    cur = conn.cursor()
                    cur.execute("SELECT ? AS x", [n])
                    row = cur.fetchone()
                    results.append((n, row[0]))
                    cur.close()
            except Exception as e:
                errors.append((n, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        conn.close()
        self.assertEqual(len(errors), 0, f"线程错误: {errors}")
        self.assertEqual(len(results), 5)

    @_skip_if_no_db
    def test_loop_create_connections(self):
        """循环创建 20 个连接，验证无内存泄漏和崩溃。"""
        for _i in range(20):
            conn = nspydm.connect(**_connect_kwargs())
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()

        # 如果到这里没有崩溃，说明循环创建连接正常
        self.assertTrue(True)


# ===========================================================================
# 连接池测试
# ===========================================================================


class TestConnectionPool(unittest.TestCase):
    """连接池集成测试。"""

    @_skip_if_no_db
    def test_pool_basic(self):
        """基本连接池使用。"""
        pool = nspydm.create_pool(min_size=1, max_size=5, **_connect_kwargs())
        self.assertEqual(pool.idle_count, 1)
        self.assertEqual(pool.in_use_count, 0)

        conn = pool.get_connection()
        self.assertEqual(pool.in_use_count, 1)
        self.assertEqual(pool.idle_count, 0)

        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        self.assertIsNotNone(row)

        conn.close()
        self.assertEqual(pool.idle_count, 1)
        self.assertEqual(pool.in_use_count, 0)

        pool.close_all()

    @_skip_if_no_db
    def test_pool_connection_reuse(self):
        """连接复用。"""
        pool = nspydm.create_pool(min_size=0, max_size=3, **_connect_kwargs())

        conn1 = pool.get_connection()
        underlying = conn1._conn
        conn1.close()

        conn2 = pool.get_connection()
        self.assertIs(conn2._conn, underlying, "应复用同一个底层连接")
        conn2.close()

        pool.close_all()

    @_skip_if_no_db
    def test_pool_max_size(self):
        """最大连接数限制。"""
        pool = nspydm.create_pool(min_size=0, max_size=2, max_wait=2.0, **_connect_kwargs())

        c1 = pool.get_connection()
        c2 = pool.get_connection()
        self.assertEqual(pool.in_use_count, 2)

        # 第三个应超时
        with self.assertRaises(nspydm.InterfaceError):
            pool.get_connection()

        c1.close()
        c2.close()
        pool.close_all()

    @_skip_if_no_db
    def test_pool_multithread(self):
        """多线程并发使用连接池。"""
        pool = nspydm.create_pool(min_size=2, max_size=10, max_wait=10.0, **_connect_kwargs())

        errors = []
        num_threads = 10
        iterations = 3

        def worker():
            for _ in range(iterations):
                try:
                    conn = pool.get_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.fetchone()
                    conn.close()
                except Exception as e:
                    errors.append(str(e))
                    return

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"线程错误: {errors}")
        self.assertEqual(pool.in_use_count, 0)
        pool.close_all()

    @_skip_if_no_db
    def test_pool_context_manager(self):
        """连接池上下文管理器。"""
        with nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs()) as pool:
            self.assertFalse(pool.closed)
            conn = pool.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()

        self.assertTrue(pool.closed)

    @_skip_if_no_db
    def test_pool_with_connection_context(self):
        """池中连接的上下文管理器。"""
        pool = nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs())

        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()

        # 连接已归还
        self.assertEqual(pool.in_use_count, 0)
        pool.close_all()


# ===========================================================================
# 内存消耗测试
# ===========================================================================


class TestMemoryConsumption(unittest.TestCase):
    """内存消耗测试 — 验证循环/多线程创建连接不会导致内存无限增长。"""

    @_skip_if_no_db
    def test_loop_connections_memory(self):
        """循环创建 30 个连接，内存增长不超过 200MB。"""
        pid = os.getpid()
        mem_before = _get_process_memory_mb(pid)
        if mem_before < 0:
            self.skipTest("无法获取进程内存信息")

        for i in range(30):
            conn = nspydm.connect(**_connect_kwargs())
            cur = conn.cursor()
            cur.execute("SELECT ? AS x", [i])
            cur.fetchone()
            conn.close()

        mem_after = _get_process_memory_mb(pid)
        mem_growth = mem_after - mem_before

        print(f"\n  循环 30 次连接: 内存 {mem_before:.1f}MB → {mem_after:.1f}MB (增长 {mem_growth:.1f}MB)")
        # 内存增长不应超过 200MB
        self.assertLess(mem_growth, 200,
                        f"循环创建连接后内存增长 {mem_growth:.1f}MB，超过 200MB 上限")

    @_skip_if_no_db
    def test_multi_thread_connections_memory(self):
        """5 线程各创建 10 个连接，内存增长不超过 300MB。"""
        pid = os.getpid()
        mem_before = _get_process_memory_mb(pid)
        if mem_before < 0:
            self.skipTest("无法获取进程内存信息")

        errors = []

        def worker():
            try:
                for _i in range(10):
                    conn = nspydm.connect(**_connect_kwargs())
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.fetchone()
                    conn.close()
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"线程错误: {errors}")

        mem_after = _get_process_memory_mb(pid)
        mem_growth = mem_after - mem_before

        print(f"\n  5线程×10连接: 内存 {mem_before:.1f}MB → {mem_after:.1f}MB (增长 {mem_growth:.1f}MB)")
        self.assertLess(mem_growth, 300,
                        f"多线程创建连接后内存增长 {mem_growth:.1f}MB，超过 300MB 上限")

    @_skip_if_no_db
    def test_dmclient_subprocess_memory(self):
        """dmclient 子进程的内存消耗监控。"""
        from nspydm.native.bridge import DmClient

        client = DmClient()
        client.start()

        dmclient_pid = client._proc.pid
        mem_start = _get_process_memory_mb(dmclient_pid)
        if mem_start < 0:
            client.stop()
            self.skipTest("无法获取 dmclient 进程内存信息")

        # 创建 10 个连接并全部关闭
        handles = []
        for _i in range(10):
            resp = client.send({
                "cmd": "open",
                "url": f"jdbc:dm://{DM_HOST}:{DM_PORT}",
                "user": DM_USER,
                "password": DM_PASSWORD,
            })
            if "error" in resp:
                client.stop()
                self.skipTest(f"连接失败: {resp['error']}")
            handles.append(resp["handle"])

        mem_peak = _get_process_memory_mb(dmclient_pid)

        # 关闭所有连接
        for h in handles:
            client.send({"cmd": "close", "handle": h})

        mem_after_close = _get_process_memory_mb(dmclient_pid)

        client.stop()

        print("\n  dmclient 进程内存:")
        print(f"    启动后: {mem_start:.1f}MB")
        print(f"    10连接峰值: {mem_peak:.1f}MB")
        print(f"    关闭后: {mem_after_close:.1f}MB")
        print(f"    峰值增长: {mem_peak - mem_start:.1f}MB")

        # dmclient 峰值内存不应超过 300MB（无 JVM！）
        self.assertLess(mem_peak, 300,
                        f"dmclient 峰值内存 {mem_peak:.1f}MB，超过 300MB 上限")

    @_skip_if_no_db
    def test_pool_memory_stability(self):
        """连接池长时间使用的内存稳定性。"""
        pid = os.getpid()
        mem_before = _get_process_memory_mb(pid)
        if mem_before < 0:
            self.skipTest("无法获取进程内存信息")

        pool = nspydm.create_pool(min_size=2, max_size=10, **_connect_kwargs())

        # 模拟 50 次获取/归还
        for _ in range(50):
            conn = pool.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()

        mem_after = _get_process_memory_mb(pid)
        mem_growth = mem_after - mem_before

        pool.close_all()

        print(f"\n  连接池 50 次操作: 内存 {mem_before:.1f}MB → {mem_after:.1f}MB (增长 {mem_growth:.1f}MB)")
        # 池化后内存增长应非常小
        self.assertLess(mem_growth, 100,
                        f"连接池操作后内存增长 {mem_growth:.1f}MB，超过 100MB 上限")


# ===========================================================================
# dmclient 子进程通信测试
# ===========================================================================


class TestDmClientProcess(unittest.TestCase):
    """dmclient 子进程通信测试。"""

    @_skip_if_no_db
    def test_dmclient_start_stop(self):
        """dmclient 启动和停止。"""
        from nspydm.native.bridge import DmClient

        client = DmClient()
        client.start()
        self.assertTrue(client._started)
        client.stop()
        self.assertFalse(client._started)

    @_skip_if_no_db
    def test_dmclient_context_manager(self):
        """dmclient 上下文管理器。"""
        from nspydm.native.bridge import DmClient

        with DmClient() as client:
            self.assertTrue(client._started)
        self.assertFalse(client._started)

    @_skip_if_no_db
    def test_dmclient_connection_lifecycle(self):
        """dmclient 连接生命周期。"""
        from nspydm.native.bridge import DmClient

        with DmClient() as client:
            # 打开连接
            resp = client.send({
                "cmd": "open",
                "url": f"jdbc:dm://{DM_HOST}:{DM_PORT}",
                "user": DM_USER,
                "password": DM_PASSWORD,
            })
            self.assertIn("handle", resp)
            handle = resp["handle"]

            # 执行查询
            resp = client.send({
                "cmd": "execute",
                "handle": handle,
                "sql": "SELECT ? AS x",
                "params": [42],
            })
            self.assertIn("result_handle", resp)
            result_handle = resp["result_handle"]

            # 获取列信息
            if result_handle > 0:
                resp = client.send({"cmd": "columns", "handle": result_handle})
                self.assertIn("names", resp)

                # 获取数据
                resp = client.send({"cmd": "fetch", "handle": result_handle})
                self.assertIn("row", resp)
                self.assertEqual(resp["row"][0], 42)

                # 释放结果集
                client.send({
                    "cmd": "free_result",
                    "handle": result_handle,
                    "stmt_handle": resp.get("stmt_handle", -1),
                })

            # 关闭连接
            client.send({"cmd": "close", "handle": handle})

    @_skip_if_no_db
    def test_dmclient_error_handling(self):
        """dmclient 错误处理。"""
        from nspydm.native.bridge import DmClient

        with DmClient() as client:
            resp = client.send({
                "cmd": "open",
                "url": f"jdbc:dm://{DM_HOST}:{DM_PORT}",
                "user": "nonexistent_user",
                "password": "wrong",
            })
            self.assertIn("error", resp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
