"""Linux/Windows 后端：直接透传官方 dmPython，零性能损失。

此模块不做任何包装或转换，`import nspydm` 等同于 `import dmPython`。
"""

try:
    from dmPython import *  # noqa: F401, F403

    # 显式引入 DB-API 2.0 核心符号，确保 IDE 可识别
    from dmPython import (  # noqa: F401
        BINARY,
        DATETIME,
        NUMBER,
        ROWID,
        STRING,
        Binary,
        Connection,
        Cursor,
        DatabaseError,
        DataError,
        Date,
        DateFromTicks,
        Error,
        IntegrityError,
        InterfaceError,
        InternalError,
        NotSupportedError,
        OperationalError,
        ProgrammingError,
        Time,
        TimeFromTicks,
        Timestamp,
        TimestampFromTicks,
        Warning,
        apilevel,
        connect,
        paramstyle,
        threadsafety,
    )
except ImportError as e:
    raise ImportError(
        "ns-pydm 在非 macOS 系统上依赖官方 dmPython 驱动。\n"
        "请先安装：pip install dmPython\n"
        f"原始错误：{e}"
    ) from e
