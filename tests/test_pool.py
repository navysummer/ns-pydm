"""连接池集成测试 — 使用真实达梦数据库。

连接信息：127.0.0.1:5236 SYSDBA/SYSdba@2026
运行前确保 DMCLIENT_PATH 指向正确的 dmclient 二进制文件。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import unittest

# ---------------------------------------------------------------------------
# 连接配置
# ---------------------------------------------------------------------------

DM_HOST = os.environ.get("DM_TEST_HOST", "127.0.0.1")
DM_PORT = int(os.environ.get("DM_TEST_PORT", "5236"))
DM_USER = os.environ.get("DM_TEST_USER", "SYSDBA")
DM_PASSWORD = os.environ.get("DM_TEST_PASSWORD", "SYSdba@2026")


def _connect_kwargs():
    return dict(user=DM_USER, password=DM_PASSWORD, host=DM_HOST, port=DM_PORT)


def _skip_if_no_db(func):
    def wrapper(self, *args, **kwargs):
        try:
            import nspydm
            conn = nspydm.connect(**_connect_kwargs())
            conn.close()
        except Exception as e:
            self.skipTest(f"无法连接达梦数据库: {e}")
        return func(self, *args, **kwargs)
    return wrapper


class TestPoolBasic(unittest.TestCase):
    """连接池基本功能测试。"""

    @_skip_if_no_db
    def test_create_pool_prefills(self):
        """创建池时预填充 min_size 个连接。"""
        import nspydm

        pool = nspydm.create_pool(min_size=3, max_size=10, **_connect_kwargs())
        try:
            self.assertEqual(pool.idle_count, 3)
            self.assertEqual(pool.in_use_count, 0)
            self.assertEqual(pool.size, 3)
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_get_connection(self):
        """从池中获取连接。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=5, **_connect_kwargs())
        try:
            conn = pool.get_connection()
            self.assertEqual(pool.in_use_count, 1)
            self.assertEqual(pool.idle_count, 0)

            # 验证连接可用
            cur = conn.cursor()
            cur.execute("SELECT 1")
            row = cur.fetchone()
            self.assertIsNotNone(row)

            conn.close()
            self.assertEqual(pool.in_use_count, 0)
            self.assertEqual(pool.idle_count, 1)
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_connection_reuse(self):
        """归还后连接被复用。"""
        import nspydm

        pool = nspydm.create_pool(min_size=0, max_size=3, **_connect_kwargs())
        try:
            conn1 = pool.get_connection()
            underlying = conn1._conn
            conn1.close()

            conn2 = pool.get_connection()
            self.assertIs(conn2._conn, underlying)
            conn2.close()
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_max_size_enforced(self):
        """最大连接数限制。"""
        import nspydm

        pool = nspydm.create_pool(min_size=0, max_size=2, max_wait=3.0, **_connect_kwargs())
        try:
            c1 = pool.get_connection()
            c2 = pool.get_connection()
            self.assertEqual(pool.in_use_count, 2)

            # 第三个应超时
            with self.assertRaises(nspydm.InterfaceError):
                pool.get_connection()

            c1.close()
            c2.close()
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_max_size_then_release(self):
        """达到上限后归还连接可重新获取。"""
        import nspydm

        pool = nspydm.create_pool(min_size=0, max_size=2, max_wait=10.0, **_connect_kwargs())
        try:
            c1 = pool.get_connection()
            c2 = pool.get_connection()

            # 归还 c1
            c1.close()

            # 现在可以再次获取
            c3 = pool.get_connection()
            self.assertIsNotNone(c3)

            c2.close()
            c3.close()
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_close_all(self):
        """关闭池。"""
        import nspydm

        pool = nspydm.create_pool(min_size=2, max_size=5, **_connect_kwargs())
        pool.close_all()
        self.assertTrue(pool.closed)
        self.assertEqual(pool.idle_count, 0)
        self.assertEqual(pool.in_use_count, 0)

    @_skip_if_no_db
    def test_close_all_idempotent(self):
        """多次 close_all 不报错。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs())
        pool.close_all()
        pool.close_all()
        self.assertTrue(pool.closed)

    @_skip_if_no_db
    def test_get_after_close_raises(self):
        """关闭后获取连接应抛出异常。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs())
        pool.close_all()
        with self.assertRaises(nspydm.InterfaceError):
            pool.get_connection()

    @_skip_if_no_db
    def test_pool_context_manager(self):
        """连接池上下文管理器。"""
        import nspydm

        with nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs()) as pool:
            self.assertFalse(pool.closed)
            conn = pool.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()

        self.assertTrue(pool.closed)

    @_skip_if_no_db
    def test_pooled_connection_context(self):
        """池中连接的上下文管理器。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs())
        try:
            with pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()

            self.assertEqual(pool.in_use_count, 0)
            self.assertEqual(pool.idle_count, 1)
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_invalid_min_max(self):
        """无效参数应抛出 InterfaceError。"""
        import nspydm

        with self.assertRaises(nspydm.InterfaceError):
            nspydm.create_pool(min_size=5, max_size=3, **_connect_kwargs())

        with self.assertRaises(nspydm.InterfaceError):
            nspydm.create_pool(min_size=-1, max_size=3, **_connect_kwargs())

        with self.assertRaises(nspydm.InterfaceError):
            nspydm.create_pool(min_size=0, max_size=0, **_connect_kwargs())

    @_skip_if_no_db
    def test_pool_repr(self):
        """repr 信息。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=3, **_connect_kwargs())
        try:
            r = repr(pool)
            self.assertIn("ConnectionPool", r)
            self.assertIn("open", r)
        finally:
            pool.close_all()

        r2 = repr(pool)
        self.assertIn("closed", r2)

    @_skip_if_no_db
    def test_size_properties(self):
        """池统计属性。"""
        import nspydm

        pool = nspydm.create_pool(min_size=2, max_size=5, **_connect_kwargs())
        try:
            self.assertEqual(pool.size, 2)
            self.assertEqual(pool.idle_count, 2)
            self.assertEqual(pool.in_use_count, 0)

            conn = pool.get_connection()
            self.assertEqual(pool.size, 2)
            self.assertEqual(pool.idle_count, 1)
            self.assertEqual(pool.in_use_count, 1)

            conn.close()
            self.assertEqual(pool.size, 2)
            self.assertEqual(pool.idle_count, 2)
            self.assertEqual(pool.in_use_count, 0)
        finally:
            pool.close_all()


class TestPoolConcurrency(unittest.TestCase):
    """连接池多线程并发测试。"""

    @_skip_if_no_db
    def test_multi_thread_get_and_return(self):
        """多线程并发获取和归还连接。"""
        import nspydm

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
    def test_pool_stats_consistent(self):
        """高并发下所有线程完成后池统计信息一致性。"""
        import nspydm

        pool = nspydm.create_pool(min_size=3, max_size=10, max_wait=10.0, **_connect_kwargs())

        errors = []

        def worker():
            for _ in range(3):
                try:
                    conn = pool.get_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT 1")
                    cur.fetchone()
                    conn.close()
                except Exception as e:
                    errors.append(str(e))
                    return

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"执行错误: {errors}")

        # 所有线程完成后，统计应一致
        s = pool.size
        i = pool.idle_count
        u = pool.in_use_count
        self.assertEqual(s, i + u, f"完成后统计不一致: size={s} != idle={i} + in_use={u}")

        pool.close_all()

    @_skip_if_no_db
    def test_concurrent_close_all_wakes_waiters(self):
        """close_all() 唤醒等待中的线程。"""
        import nspydm

        pool = nspydm.create_pool(min_size=0, max_size=1, max_wait=30.0, **_connect_kwargs())

        # 占用唯一连接
        pool.get_connection()

        errors = []
        started = threading.Event()

        def waiter():
            started.set()
            try:
                pool.get_connection()
            except Exception as e:
                errors.append(type(e).__name__)

        t = threading.Thread(target=waiter)
        t.start()
        started.wait(timeout=2)
        time.sleep(0.5)  # 给 waiter 进入 wait 的时间

        pool.close_all()
        t.join(timeout=5)

        self.assertFalse(t.is_alive(), "waiter 线程未唤醒")
        self.assertEqual(errors, ["InterfaceError"])


class TestPoolWithRealQueries(unittest.TestCase):
    """连接池 + 真实 SQL 查询测试。"""

    TABLE_NAME = "NSPYDM_POOL_TEST"

    def setUp(self):
        import nspydm
        try:
            self.conn = nspydm.connect(**_connect_kwargs())
        except Exception as e:
            self.skipTest(f"无法连接达梦数据库: {e}")
        self.cur = self.conn.cursor()
        # 创建测试表
        try:
            self.cur.execute(f"DROP TABLE {self.TABLE_NAME}")
            self.conn.commit()
        except Exception:
            pass
        self.cur.execute(
            f"CREATE TABLE {self.TABLE_NAME} (id INT PRIMARY KEY, name VARCHAR(100), value DOUBLE)"
        )
        self.conn.commit()

    def tearDown(self):
        if hasattr(self, "cur"):
            with contextlib.suppress(Exception):
                self.cur.close()
        if hasattr(self, "conn"):
            with contextlib.suppress(Exception):
                cur2 = self.conn.cursor()
                cur2.execute(f"DROP TABLE {self.TABLE_NAME}")
                self.conn.commit()
            with contextlib.suppress(Exception):
                self.conn.close()

    @_skip_if_no_db
    def test_pool_crud(self):
        """连接池中的 CRUD 操作。"""
        import nspydm

        pool = nspydm.create_pool(min_size=1, max_size=5, **_connect_kwargs())
        try:
            with pool.get_connection() as conn:
                # Insert
                conn.autocommit = False
                cur = conn.cursor()
                cur.execute(f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?)", [1, "test", 99.5])
                conn.commit()

            with pool.get_connection() as conn:
                # Select
                cur = conn.cursor()
                cur.execute(f"SELECT name, value FROM {self.TABLE_NAME} WHERE id = ?", [1])
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "test")
                self.assertAlmostEqual(row[1], 99.5)
        finally:
            pool.close_all()

    @_skip_if_no_db
    def test_pool_concurrent_inserts(self):
        """多线程通过连接池并发插入数据。"""
        import nspydm

        pool = nspydm.create_pool(min_size=3, max_size=10, max_wait=10.0, **_connect_kwargs())
        errors = []

        def worker(n):
            try:
                with pool.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?)",
                        [n, f"thread_{n}", float(n) * 1.5]
                    )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"插入错误: {errors}")

        # 验证数据
        self.cur.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME}")
        count = self.cur.fetchone()[0]
        self.assertEqual(count, 10)

        pool.close_all()


if __name__ == "__main__":
    unittest.main(verbosity=2)
