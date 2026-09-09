"""ns-pydm 异步支持模块。

将同步的 nspydm 驱动包装为 async/await 接口，
兼容 macOS 原生后端和 dmPython 后端。
所有阻塞操作通过 ``asyncio.to_thread`` 委托到线程池，不阻塞事件循环。

用法::

    from nspydm._async import connect, create_pool

    async def main():
        conn = await connect(user="SYSDBA", password="xxx", server="127.0.0.1")
        async with conn.cursor() as cur:
            await cur.execute("SELECT ?", [1])
            row = await cur.fetchone()
            print(row)

        pool = await create_pool(user="SYSDBA", password="xxx", minsize=2, maxsize=10)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                print(await cur.fetchall())
"""

from .connection import (
    AsyncConnection,
    AsyncCursor,
    AsyncTransaction,
    DefaultCompiler,
    IsolationLevel,
    ReadCommittedCompiler,
    RepeatableReadCompiler,
    SerializableCompiler,
    connect,
)
from .log import logger
from .pool import AsyncPool, create_pool
from .utils import get_running_loop

__all__ = (
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
)
