"""AsyncPool 基准测试（mock 模式，无需真实数据库）。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from nspydm._async import AsyncPool
from nspydm._async.connection import AsyncConnection


def _make_mock_sync_conn() -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value = MagicMock()
    conn.cursor.return_value.execute.return_value = None
    conn.cursor.return_value.fetchone.return_value = (1,)
    conn.cursor.return_value.close.return_value = None
    conn.close.return_value = None
    conn.ping.return_value = None
    return conn


class MockAsyncConnection(AsyncConnection):
    """不依赖真实数据库的 mock AsyncConnection。"""

    def __init__(self) -> None:
        super().__init__(_make_mock_sync_conn())
        self._closed = False

    async def close(self) -> None:
        self._closed = True

    async def ping(self, reconnect: int = 0) -> None:
        pass


def _make_pool(minsize: int = 1, maxsize: int = 10) -> AsyncPool:
    pool = AsyncPool(minsize, maxsize, 30.0, **{})
    for _ in range(maxsize):
        pool._free.append(MockAsyncConnection())
    return pool


async def bench_pool_acquire_release(n: int = 1000) -> float:
    """测试 pool acquire/release 吞吐量。"""
    pool = _make_pool()

    start = time.monotonic()
    for _ in range(n):
        conn = await pool._acquire()
        await pool.release(conn)
    elapsed = time.monotonic() - start
    return elapsed


async def bench_pool_cursor(n: int = 500) -> float:
    """测试 pool.cursor() 吞吐量。"""
    pool = _make_pool(1, 20)

    start = time.monotonic()
    for _ in range(n):
        async with pool.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
    elapsed = time.monotonic() - start
    return elapsed


async def main() -> None:
    print("=== AsyncPool 基准测试 ===\n")

    n_ar = 2000
    t_ar = await bench_pool_acquire_release(n_ar)
    print(f"acquire/release x{n_ar}: {t_ar:.3f}s  ({n_ar/t_ar:.0f} ops/s)")

    n_cur = 500
    t_cur = await bench_pool_cursor(n_cur)
    print(f"cursor() x{n_cur}:       {t_cur:.3f}s  ({n_cur/t_cur:.0f} ops/s)")


if __name__ == "__main__":
    asyncio.run(main())
