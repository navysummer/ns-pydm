"""ns-pydm — 达梦数据库 DB-API 2.0 驱动。

macOS 使用 GraalVM native-image dmclient 原生后端。
Linux / Windows 透传官方 dmPython 驱动。

用法::

    import nspydm

    # 同步连接
    conn = nspydm.connect(user="SYSDBA", password="xxx", server="127.0.0.1")
    cur = conn.cursor()
    cur.execute("SELECT ?", [1])
    print(cur.fetchone())

    # 异步连接
    async def main():
        conn = await nspydm.async_connect(user="SYSDBA", password="xxx")
        async with await conn.cursor() as cur:
            await cur.execute("SELECT ?", [1])
            print(await cur.fetchone())

        pool = await nspydm.async_create_pool(user="SYSDBA", password="xxx", minsize=2)
        async with pool.acquire() as conn:
            async with await conn.cursor() as cur:
                ...
"""

from __future__ import annotations

from . import _async as async_api  # noqa: F401
from ._async import connect as async_connect  # noqa: F401
from ._async import create_pool as async_create_pool  # noqa: F401
from .dbapi import *  # noqa: F401, F403  # type: ignore[attr-defined]
from .pool import ConnectionPool, create_pool  # noqa: F401

__all__ = [
    # DB-API 2.0 必须项
    "apilevel",
    "threadsafety",
    "paramstyle",
    "connect",
    "Connection",
    "Cursor",
    "Warning",
    "Error",
    "InterfaceError",
    "DatabaseError",
    "DataError",
    "OperationalError",
    "IntegrityError",
    "InternalError",
    "ProgrammingError",
    "NotSupportedError",
    "STRING",
    "BINARY",
    "NUMBER",
    "DATETIME",
    "ROWID",
    "Binary",
    "Date",
    "Time",
    "Timestamp",
    "DateFromTicks",
    "TimeFromTicks",
    "TimestampFromTicks",
    # dmPython 扩展工具函数
    "StringFromBytes",
    # LOB / BFILE / ObjectVar 类型
    "LobObject",
    "BFILEObject",
    "ObjectVar",
    # 游标类型常量
    "TupleCursor",
    "DictCursor",
    # 连接池（ns-pydm 扩展）
    "create_pool",
    "ConnectionPool",
    # 异步支持
    "async_api",
    "async_connect",
    "async_create_pool",
]
