"""ns-pydm 异步模块使用示例。

运行前确保:
  1. dmclient 二进制可用（macOS）或 dmPython 已安装（Linux/Windows）
  2. 达梦数据库运行在 127.0.0.1:5236
  3. SYSDBA/SYSdba@2026 可登录

用法::

    python examples/async_usage.py
"""

from __future__ import annotations

import asyncio
import os

# 方式一：从顶层包导入
from nspydm import async_connect, async_create_pool

# 方式二：从 async_api 导入
# from nspydm.async_api import connect, create_pool
# 方式三：从 _async 子模块导入
# from nspydm._async import connect, create_pool

HOST = os.environ.get("DM_TEST_HOST", "127.0.0.1")
PORT = int(os.environ.get("DM_TEST_PORT", "5236"))
USER = os.environ.get("DM_TEST_USER", "SYSDBA")
PASSWORD = os.environ.get("DM_TEST_PASSWORD", "SYSdba@2026")


async def demo_connection() -> None:
    """基本连接与查询。"""
    print("=== 基本连接与查询 ===")

    conn = await async_connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    async with conn, await conn.cursor() as cur:
        await cur.execute("SELECT ?", [1])
        row = await cur.fetchone()
        print(f"fetchone: {row}")

        # 异步迭代
        await cur.execute("SELECT level FROM dual CONNECT BY level <= 3")
        results: list[int] = []
        async for row in cur:
            results.append(row[0])
        print(f"async for: {results}")


async def demo_transaction() -> None:
    """事务与保存点。"""
    print("\n=== 事务与保存点 ===")

    conn = await async_connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    async with conn, await conn.cursor() as cur:
        # 自动事务管理
        async with cur.begin():
            await cur.execute("SELECT 1")

        # 手动事务控制
        txn = await cur.begin()
        await cur.execute("SELECT 1")
        await txn.commit()

        # 嵌套事务（保存点）
        async with cur.begin():
            await cur.execute("SELECT 1")
            async with cur.begin_nested():
                await cur.execute("SELECT 2")


async def demo_pool() -> None:
    """连接池。"""
    print("\n=== 连接池 ===")

    pool = await async_create_pool(
        user=USER,
        password=PASSWORD,
        host=HOST,
        port=PORT,
        minsize=2,
        maxsize=5,
        pool_recycle=3600,  # 1小时回收
    )

    async with pool.acquire() as conn, await conn.cursor() as cur:
        await cur.execute("SELECT 1")
        print(f"conn: {await cur.fetchone()}")

    # 更简洁：pool.cursor() 自动管理连接
    async with pool.cursor() as cur:
        await cur.execute("SELECT 1")
        print(f"cursor: {await cur.fetchone()}")

    pool.close()
    await pool.wait_closed()


async def main() -> None:
    try:
        await demo_connection()
    except Exception as e:
        print(f"[跳过 connection 示例] {e}")

    try:
        await demo_transaction()
    except Exception as e:
        print(f"[跳过 transaction 示例] {e}")

    try:
        await demo_pool()
    except Exception as e:
        print(f"[跳过 pool 示例] {e}")


if __name__ == "__main__":
    asyncio.run(main())
