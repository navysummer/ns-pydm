"""达梦数据库 DB-API 2.0 驱动 — 跨平台统一入口。

平台路由策略：
- **macOS**（Intel / Apple Silicon）：使用 GraalVM native-image dmclient 原生后端
- **Linux / Windows**：直接透传官方 dmPython 驱动，零性能损失

用法（所有平台一致）：
    import nspydm
    conn = nspydm.connect(user="SYSDBA", password="xxx", server="127.0.0.1", port=5236)
    cur = conn.cursor()
    cur.execute("SELECT ? as x", [1])
    print(cur.fetchone())
"""

import platform as _platform

_IS_MACOS = _platform.system() == "Darwin"

# ---------------------------------------------------------------------------
# 平台路由
# ---------------------------------------------------------------------------

if _IS_MACOS:
    # macOS → GraalVM native-image dmclient 原生后端（支持 x64 + ARM64）
    from ._backends._native import (  # noqa: F401
        BFILE,
        BIGINT,
        BINARY,
        BLOB,
        BOOLEAN,
        CLOB,
        CURSOR,
        DATETIME,
        DEBUG_CLOSE,
        DEBUG_OPEN,
        DEBUG_SIMPLE,
        DEBUG_SWITCH,
        DECIMAL,
        DOUBLE,
        DSQL_AUTOCOMMIT_OFF,
        DSQL_AUTOCOMMIT_ON,
        DSQL_CB_CLOSE,
        DSQL_CB_PRESERVE,
        DSQL_FALSE,
        DSQL_MODE_READ_ONLY,
        DSQL_MODE_READ_WRITE,
        DSQL_MPP_LOGIN_GLOBAL,
        DSQL_MPP_LOGIN_LOCAL,
        DSQL_RWSEPARATE_OFF,
        DSQL_RWSEPARATE_ON,
        DSQL_TRUE,
        DSQL_TRX_ACTIVE,
        DSQL_TRX_COMPLETE,
        FIXED_BINARY,
        FIXED_STRING,
        FIXED_UNICODE_STRING,
        # dmPython 扩展类型常量
        INTERVAL,
        ISO_LEVEL_READ_COMMITTED,
        ISO_LEVEL_READ_DEFAULT,
        ISO_LEVEL_READ_UNCOMMITTED,
        ISO_LEVEL_REPEATABLE_READ,
        ISO_LEVEL_SERIALIZABLE,
        LANGUAGE_CN,
        LANGUAGE_CNT_HK,
        LANGUAGE_EN,
        LOB,
        LONG_BINARY,
        LONG_STRING,
        NUMBER,
        OBJECTVAR,
        PG_BIG5,
        PG_EUC_JP,
        PG_EUC_KR,
        PG_GB18030,
        PG_GBK,
        PG_ISO_8859_1,
        PG_ISO_8859_9,
        PG_ISO_8859_11,
        PG_KOI8R,
        PG_SQL_ASCII,
        PG_UTF8,
        REAL,
        ROWID,
        SHUTDOWN_ABORT,
        # 模块级整数/字符串常量
        SHUTDOWN_DEFAULT,
        SHUTDOWN_IMMEDIATE,
        SHUTDOWN_NORMAL,
        SHUTDOWN_TRANSACTIONAL,
        # DB-API 2.0 类型常量
        STRING,
        TIME_WITH_TIMEZONE,
        TIMESTAMP_WITH_TIMEZONE,
        UNICODE_STRING,
        YEAR_MONTH_INTERVAL,
        BFILEObject,
        # 工具函数
        Binary,
        Connection,
        Cursor,
        DatabaseError,
        DataError,
        Date,
        DateFromTicks,
        DictCursor,
        Error,
        IntegrityError,
        InterfaceError,
        InternalError,
        # LOB / BFILE / ObjectVar 类
        LobObject,
        NotSupportedError,
        ObjectVar,
        OperationalError,
        ProgrammingError,
        StringFromBytes,
        Time,
        TimeFromTicks,
        Timestamp,
        TimestampFromTicks,
        TupleCursor,
        # 异常
        Warning,
        apilevel,
        buildtime,
        # 连接
        connect,
        exBFILE,
        objectvar,
        paramstyle,
        threadsafety,
        version,
    )
else:
    # Linux / Windows → 官方 dmPython 驱动，直接透传
    from ._backends._dmpython import *  # noqa: F401, F403
    from ._backends._dmpython import (  # noqa: F401
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
