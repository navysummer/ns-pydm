"""macOS 后端：GraalVM native-image dmclient 原生驱动。

通过 subprocess + JSON 行协议与 dmclient 可执行文件通信，无需 JVM。
支持 Intel (x64) 和 Apple Silicon (ARM64) 两种架构。
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import (
    Any,
    Callable,
)

from ..native.bridge import DmClient, DmClientError, NativeConnection, NativeResult

threadsafety = 1
paramstyle = "qmark"
apilevel = "2.0"

# ---------------------------------------------------------------------------
# 版本信息（与 dmPython 对齐）
# ---------------------------------------------------------------------------

version = "2.5.30"
buildtime = "2025-09-03 00:00:00"

# ---------------------------------------------------------------------------
# 模块级整数/字符串常量（与 dmPython 对齐）
# ---------------------------------------------------------------------------

# shutdown 模式
SHUTDOWN_DEFAULT = "NORMAL"
SHUTDOWN_ABORT = "ABORT"
SHUTDOWN_IMMEDIATE = "IMMEDIATE"
SHUTDOWN_TRANSACTIONAL = "TRANSACTIONAL"
SHUTDOWN_NORMAL = "NORMAL"

# debug 标志
DEBUG_CLOSE = 0
DEBUG_OPEN = 1
DEBUG_SWITCH = 2
DEBUG_SIMPLE = 3

# 游标类型
TupleCursor = 0
DictCursor = 1

# 事务隔离级别
ISO_LEVEL_READ_DEFAULT = 2
ISO_LEVEL_READ_UNCOMMITTED = 1
ISO_LEVEL_READ_COMMITTED = 2
ISO_LEVEL_REPEATABLE_READ = 4
ISO_LEVEL_SERIALIZABLE = 8

# 连接访问模式
DSQL_MODE_READ_ONLY = 1
DSQL_MODE_READ_WRITE = 0

# 自动提交
DSQL_AUTOCOMMIT_ON = 1
DSQL_AUTOCOMMIT_OFF = 0

# 布尔
DSQL_TRUE = 1
DSQL_FALSE = 0

# 读写分离
DSQL_RWSEPARATE_ON = 1
DSQL_RWSEPARATE_OFF = 0

# 事务状态
DSQL_TRX_ACTIVE = 1
DSQL_TRX_COMPLETE = 2

# MPP 登录模式
DSQL_MPP_LOGIN_GLOBAL = 0
DSQL_MPP_LOGIN_LOCAL = 1

# 游标回滚行为
DSQL_CB_CLOSE = 0
DSQL_CB_PRESERVE = 1

# 字符编码
PG_SQL_ASCII = 0
PG_EUC_JP = 2
PG_EUC_KR = 5
PG_UTF8 = 6
PG_ISO_8859_1 = 8
PG_ISO_8859_9 = 15
PG_ISO_8859_11 = 16
PG_GBK = 19
PG_BIG5 = 20
PG_KOI8R = 21
PG_GB18030 = 22

# 语言 ID
LANGUAGE_CN = 0
LANGUAGE_EN = 1
LANGUAGE_CNT_HK = 2

# ---------------------------------------------------------------------------
# 异常层次 (DB-API 2.0)
# ---------------------------------------------------------------------------

class Warning(Exception):  # noqa: A001,N818
    pass


class Error(Exception):
    pass


class InterfaceError(Error):
    pass


class DatabaseError(Error):
    pass


class DataError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class InternalError(DatabaseError):
    pass


class ProgrammingError(DatabaseError):
    pass


class NotSupportedError(DatabaseError):
    pass


# ---------------------------------------------------------------------------
# 类型对象 (DB-API 2.0 + dmPython 扩展)
# ---------------------------------------------------------------------------

STRING = "STRING"
BINARY = "BINARY"
NUMBER = "NUMBER"
DATETIME = "DATETIME"
ROWID = "ROWID"

# dmPython 扩展类型常量（与 dmPython 对齐，macOS 后端使用字符串占位）
INTERVAL = "INTERVAL"
YEAR_MONTH_INTERVAL = "YEAR_MONTH_INTERVAL"
BLOB = "BLOB"
CLOB = "CLOB"
LOB = "LOB"
BFILE = "BFILE"
exBFILE = "exBFILE"  # noqa: N816
LONG_BINARY = "LONG_BINARY"
LONG_STRING = "LONG_STRING"
CURSOR = "CURSOR"
FIXED_STRING = "FIXED_STRING"
FIXED_BINARY = "FIXED_BINARY"
UNICODE_STRING = "UNICODE_STRING"
FIXED_UNICODE_STRING = "FIXED_UNICODE_STRING"
OBJECTVAR = "OBJECTVAR"
DOUBLE = "DOUBLE"
REAL = "REAL"
BOOLEAN = "BOOLEAN"
DECIMAL = "DECIMAL"
TIME_WITH_TIMEZONE = "TIME_WITH_TIMEZONE"
TIMESTAMP_WITH_TIMEZONE = "TIMESTAMP_WITH_TIMEZONE"
BIGINT = "BIGINT"

# ---------------------------------------------------------------------------
# LOB 对象（对应 dmPython.LOB）
# ---------------------------------------------------------------------------

class LobObject:
    """达梦 LOB 对象（BLOB / CLOB）。

    macOS 原生后端通过 JDBC 预加载 LOB 数据，因此 LOB 对象是内存快照。
    read() / size() 直接操作内存数据；write() / truncate() 不支持（需要原生 DPI 驱动）。
    """

    def __init__(self, data: bytes | str, is_clob: bool = False) -> None:
        self._data = data
        self._is_clob = is_clob

    def size(self) -> int:
        """返回 LOB 长度。CLOB 返回字符数，BLOB 返回字节数。"""
        return len(self._data)

    def read(self, offset: int = 0, amount: int | None = None) -> bytes | str:
        """读取 LOB 数据。

        Args:
            offset: 1-based 起始偏移（0 或 1 均表示起始）。
            amount: 读取长度，None 表示读到末尾。
        """
        start = max(0, offset - 1) if offset > 0 else 0
        if amount is None:
            return self._data[start:]
        return self._data[start:start + amount]

    def write(self, data: Any, offset: int = 0) -> None:
        """写入 LOB 数据（macOS 原生后端不支持）。"""
        raise NotSupportedError("macOS 原生后端不支持 LOB write 操作，请使用官方 dmPython 驱动")

    def truncate(self, newSize: int = 0) -> None:  # noqa: N803
        """截断 LOB 数据（macOS 原生后端不支持）。"""
        raise NotSupportedError("macOS 原生后端不支持 LOB truncate 操作，请使用官方 dmPython 驱动")

    def __str__(self) -> str:
        if self._is_clob:
            return self._data if isinstance(self._data, str) else self._data.decode("utf-8", errors="replace")
        return repr(self._data)

    def __repr__(self) -> str:
        kind = "CLOB" if self._is_clob else "BLOB"
        return f"<LOB {kind} len={len(self._data)}>"

    def __reduce__(self) -> tuple:
        return (self.__class__, (self._data, self._is_clob))


# ---------------------------------------------------------------------------
# BFILE 对象（对应 dmPython BFILE）
# ---------------------------------------------------------------------------

class BFILEObject:
    """达梦 BFILE 对象（只读外部文件引用）。

    macOS 原生后端将 BFILE 数据预加载为字节序列，只支持 size() 和 read()。
    """

    def __init__(self, data: bytes) -> None:
        if isinstance(data, (bytes, bytearray, memoryview)):
            self._data: bytes = bytes(data)
        else:
            self._data = str(data).encode("utf-8") if data is not None else b""

    def size(self) -> int:
        """返回 BFILE 数据字节数。"""
        return len(self._data)

    def read(self, offset: int = 0, amount: int | None = None) -> bytes:
        """读取 BFILE 数据。offset 为 1-based 偏移量。"""
        start = max(0, offset - 1) if offset > 0 else 0
        if amount is None:
            return self._data[start:]
        return self._data[start:start + amount]

    def __str__(self) -> str:
        return repr(self._data)

    def __repr__(self) -> str:
        return f"<BFILE len={len(self._data)}>"


# ---------------------------------------------------------------------------
# ObjectVar 对象（对应 dmPython objectvar）
# ---------------------------------------------------------------------------

class ObjectVar:
    """达梦对象类型变量（对应 dmPython objectvar / OBJECTVAR）。"""

    def __init__(self, values: list[Any] | None = None, object_type: Any = None) -> None:
        self._values: list[Any] = list(values) if values is not None else []
        self._type = object_type

    @property
    def type(self) -> Any:
        """对象类型描述。"""
        return self._type

    @property
    def valuecount(self) -> int:
        """对象属性数量。"""
        return len(self._values)

    def getvalue(self) -> list[Any]:
        """返回对象属性值列表副本。"""
        return self._values[:]

    def setvalue(self, pos: int, value: Any) -> None:
        """设置指定位置（0-based）的属性值。"""
        while len(self._values) <= pos:
            self._values.append(None)
        self._values[pos] = value

    def __repr__(self) -> str:
        return f"<ObjectVar type={self._type!r} valuecount={len(self._values)}>"


# objectvar 作为类型常量（与 dmPython 对齐：objectvar 既是类型名也是类）
objectvar = ObjectVar

# ---------------------------------------------------------------------------
# dmclient 进程管理（单例）
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_global_client: DmClient | None = None


def _get_client() -> DmClient:
    """获取或创建全局 dmclient 单例。"""
    global _global_client
    if _global_client is not None and _global_client._started:
        return _global_client
    with _client_lock:
        if _global_client is not None and _global_client._started:
            return _global_client
        _global_client = DmClient()
        _global_client.start()
        return _global_client


def _reset_client() -> None:
    """重置全局客户端（用于测试）。"""
    global _global_client
    with _client_lock:
        if _global_client is not None:
            _global_client.stop()
            _global_client = None


# ---------------------------------------------------------------------------
# 参数风格适配
# ---------------------------------------------------------------------------

def _adapt_paramstyle(sql: str) -> str:
    """将 %s 风格的占位符转换为 ?。"""
    if "?" in sql or "%s" not in sql:
        return sql

    out = []
    i = 0
    in_single = False
    in_double = False

    while i < len(sql):
        ch = sql[i]

        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
            i += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue

        if not in_single and not in_double and sql.startswith("%s", i):
            out.append("?")
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


# ---------------------------------------------------------------------------
# JDBC 类型映射
# ---------------------------------------------------------------------------

_JDBC_TYPES = {
    -7: "BIT", -6: "TINYINT", 5: "SMALLINT", 4: "INTEGER",
    -5: "BIGINT", 3: "DECIMAL", 8: "DOUBLE", 6: "FLOAT",
    7: "REAL", 2: "NUMERIC",
    12: "VARCHAR", -1: "LONGVARCHAR", 1: "CHAR",
    -15: "NCHAR", -9: "NVARCHAR", -16: "LONGNVARCHAR",
    91: "DATE", 92: "TIME", 93: "TIMESTAMP",
    2004: "BLOB", 2005: "CLOB", 2011: "NCLOB",
    -2: "BINARY", -3: "VARBINARY", -4: "LONGVARBINARY",
    16: "BOOLEAN", 0: "NULL",
}

_CLOB_TYPES = {2005, 2011, -1, -16}
_BLOB_TYPES = {2004, -4}
_DATE_TYPES = {91, 92, 93}

# ---------------------------------------------------------------------------
# 值转换
# ---------------------------------------------------------------------------

def _convert_value(raw_value: Any, jdbc_type: int) -> Any:
    """将 JSON 原始值转换为 Python 类型。"""
    if raw_value is None:
        return None

    if jdbc_type in _BLOB_TYPES:
        import base64
        return base64.b64decode(raw_value)

    if jdbc_type in _CLOB_TYPES:
        return str(raw_value)

    if jdbc_type in _DATE_TYPES:
        s = str(raw_value)
        if " " in s:
            try:
                return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return _dt.datetime.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
        if s.count("-") == 2 and len(s) <= 10:
            return _dt.date.fromisoformat(s)
        if ":" in s:
            return _dt.time.fromisoformat(s)
        return s

    if jdbc_type == 3:  # DECIMAL
        try:
            return _decimal.Decimal(str(raw_value))
        except (TypeError, ValueError, _decimal.InvalidOperation):
            return str(raw_value)

    if jdbc_type in (2, 8, 6, 7):  # NUMERIC, FLOAT, REAL, DOUBLE
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return str(raw_value)

    if isinstance(raw_value, str):
        return str(raw_value)

    if isinstance(raw_value, (int, float, bool)):
        return raw_value

    return str(raw_value)


def _param_to_json(value: Any) -> Any:
    """将 Python 参数值转换为 JSON 兼容格式。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, _decimal.Decimal):
        return str(value)
    if isinstance(value, _dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        import base64
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, LobObject):
        # LOB 对象：提取原始数据再序列化
        if value._is_clob:
            return str(value._data)
        import base64
        return base64.b64encode(value._data).decode("ascii")
    if isinstance(value, BFILEObject):
        import base64
        return base64.b64encode(value._data).decode("ascii")
    return str(value)


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------

class Cursor:
    arraysize = 1
    bindarraysize = 1

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._closed = False
        self._native_result: NativeResult | None = None
        self.description = None
        self.rowcount = -1
        self.rownumber = 0
        self.statement = None
        self.lastrowid = None
        # dmPython 扩展属性
        self.inputTypeHandler: Callable | None = None
        self.outputTypeHandler: Callable | None = None
        self.rowFactory: Callable | None = None
        self.output_stream: int = 0
        self._dict_mode: bool = False
        self._execid: Any = None
        # 列输出转换器缓存（由 outputTypeHandler 构建）
        self._output_converters: list[Callable | None] | None = None

    # --- 只读属性（与 dmPython 对齐）---

    @property
    def with_rows(self) -> bool:
        """当前游标是否持有结果集（SELECT 类语句执行后为 True）。"""
        return self._native_result is not None and self._native_result.has_result_set

    @property
    def column_names(self) -> list[str]:
        """当前结果集的列名列表。"""
        if self._native_result is None or not self._native_result.has_result_set:
            return []
        return list(self._native_result.column_names)

    @property
    def execid(self) -> Any:
        """执行 ID（macOS 原生后端通过 JDBC 运行，不支持获取执行 ID，返回 None）。"""
        return self._execid

    # --- 生命周期 ---

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._native_result is not None:
            self._native_result.free()
            self._native_result = None
        self._output_converters = None

    def _check_open(self) -> None:
        if self._closed:
            raise InterfaceError("cursor already closed")

    # --- 上下文管理器 ---

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # --- 内部辅助 ---

    def _build_description(self) -> tuple | None:
        """构建 DB-API 2.0 description 元组。"""
        if self._native_result is None or not self._native_result.has_result_set:
            return None
        names = self._native_result.column_names
        types = self._native_result.column_types
        desc = []
        for i, name in enumerate(names):
            type_code = types[i] if i < len(types) else 0
            # (name, type_code, display_size, internal_size, precision, scale, null_ok)
            # macOS 原生后端通过 JDBC 无法获取精度/长度元数据，保持 None 与 cx_Oracle 兼容
            desc.append((name, type_code, None, None, None, None, True))
        return tuple(desc)

    def _build_output_converters(self) -> None:
        """根据有效的 outputTypeHandler 为各列构建转换器列表。"""
        handler = self.outputTypeHandler
        if handler is None:
            handler = getattr(self.connection, "outputTypeHandler", None)
        if handler is None or self._native_result is None:
            self._output_converters = None
            return

        names = self._native_result.column_names
        types = self._native_result.column_types
        converters: list[Callable | None] = []
        for i, name in enumerate(names):
            jdbc_type = types[i] if i < len(types) else 0
            try:
                result = handler(self, name, jdbc_type, None, None, None)
            except Exception:
                result = None
            if result is None:
                converters.append(None)
            elif callable(result):
                converters.append(result)
            elif hasattr(result, "getvalue"):
                # Variable-like 对象（cx_Oracle 兼容）
                v = result
                converters.append(lambda raw, _v=v: _v.getvalue(0) if raw is not None else None)
            else:
                converters.append(None)
        self._output_converters = converters

    def _convert_params(self, params: list[Any]) -> list[Any]:
        """转换参数列表，应用 inputTypeHandler（若存在）。"""
        handler = self.inputTypeHandler
        if handler is None:
            handler = getattr(self.connection, "inputTypeHandler", None)
        if handler is None:
            return [_param_to_json(p) for p in params]

        result = []
        for val in params:
            try:
                converted = handler(self, val, 1)
            except Exception:
                converted = None
            if converted is None:
                result.append(_param_to_json(val))
            elif hasattr(converted, "getvalue"):
                result.append(_param_to_json(converted.getvalue(0)))
            elif callable(converted):
                try:
                    result.append(_param_to_json(converted(val)))
                except Exception:
                    result.append(_param_to_json(val))
            else:
                result.append(_param_to_json(val))
        return result

    def _apply_row(self, raw_row: list[Any]) -> Any:
        """将原始行数据转换为最终返回值（应用类型转换、rowFactory、dict 模式）。"""
        types = self._native_result.column_types  # type: ignore[union-attr]

        if self._output_converters is not None:
            converted = []
            for i, val in enumerate(raw_row):
                jdbc_type = types[i] if i < len(types) else 0
                default_val = _convert_value(val, jdbc_type)
                conv = self._output_converters[i] if i < len(self._output_converters) else None
                if conv is not None:
                    try:
                        converted.append(conv(default_val))
                    except Exception:
                        converted.append(default_val)
                else:
                    converted.append(default_val)
            result_row: Any = tuple(converted)
        else:
            result_row = tuple(
                _convert_value(raw_row[i], types[i] if i < len(types) else 0)
                for i in range(len(raw_row))
            )

        # rowFactory 优先于 dict 模式
        if self.rowFactory is not None:
            return self.rowFactory(result_row)

        if self._dict_mode:
            names = self._native_result.column_names  # type: ignore[union-attr]
            return dict(zip(names, result_row))

        return result_row

    # --- DB-API 2.0 核心方法 ---

    def execute(
        self,
        operation: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> Cursor:
        self._check_open()
        if parameters is not None and isinstance(parameters, Mapping):
            raise NotSupportedError("仅支持序列参数（qmark/format），不支持命名参数")

        sql = _adapt_paramstyle(operation)
        self.statement = sql
        self._output_converters = None

        if self._native_result is not None:
            self._native_result.free()
            self._native_result = None

        try:
            params: list[Any] | None = None
            if parameters is not None:
                params = self._convert_params(list(parameters))

            self._native_result = self.connection._native_conn.execute(sql, params)

            if self._native_result.has_result_set:
                self.rowcount = -1
                self.rownumber = 0
                self.description = self._build_description()
                self._build_output_converters()
            else:
                self.rowcount = self._native_result.update_count
                self.rownumber = 0
                self.description = None
            return self
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def executedirect(self, operation: str) -> Cursor:
        """直接执行 SQL（不预编译）。"""
        return self.execute(operation)

    def executemany(self, operation: str, seq_of_parameters: Iterable[Sequence[Any]]) -> Cursor:
        self._check_open()
        sql = _adapt_paramstyle(operation)
        self.statement = sql

        try:
            params_batch = [self._convert_params(list(p)) for p in seq_of_parameters]
            if not params_batch:
                self.rowcount = -1
                self.description = None
                return self

            update_counts = self.connection._native_conn.execute_batch(sql, params_batch)
            total = sum(uc for uc in update_counts if uc > 0)
            self.rowcount = total if total > 0 else -1
            self.description = None
            return self
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def callproc(self, procname: str, parameters: Sequence[Any] | None = None) -> list[Any]:
        """调用存储过程，返回参数列表。

        注意：macOS 原生后端通过 JDBC 执行，OUT/INOUT 参数无法自动回写。
        对于含 OUT 参数的存储过程，建议使用 SQL 语句方式直接读取结果集。
        """
        self._check_open()
        params = list(parameters) if parameters else []
        placeholders = ", ".join("?" for _ in params)
        sql = f'begin "{procname}"({placeholders}); end;'
        self.execute(sql, params)
        return params

    def callfunc(self, funcname: str, returnType: Any, parameters: Sequence[Any] | None = None) -> Any:  # noqa: N803
        """调用函数，返回函数返回值。returnType 用于类型提示，实际转换由 JDBC 决定。"""
        self._check_open()
        params = list(parameters) if parameters else []
        placeholders = ", ".join("?" for _ in params)
        sql = f'select "{funcname}"({placeholders}) from dual'
        self.execute(sql, params)
        row = self.fetchone()
        if row is not None:
            val = row[0] if not self._dict_mode else next(iter(row.values()))
            # 若 returnType 是可调用类型，尝试转换
            if returnType is not None and callable(returnType):
                try:
                    return returnType(val)
                except Exception:
                    pass
            return val
        return None

    def prepare(self, statement: str) -> None:
        """预编译 SQL 语句（macOS 原生后端为 no-op，记录语句供 execute 使用）。"""
        self._check_open()
        self.statement = _adapt_paramstyle(statement)

    def parse(self, statement: str) -> None:
        """解析 SQL 语句（与 prepare 等价）。"""
        self.prepare(statement)

    def var(self, typ: Any, size: int = 0, arraysize: int = 1, **kwargs: Any) -> None:
        """创建绑定变量（macOS 原生后端不支持）。"""
        raise NotSupportedError("macOS 原生后端不支持绑定变量对象，请使用普通参数传值")

    def arrayvar(self, typ: Any, value: Any, size: int = 0) -> None:
        """创建数组绑定变量（macOS 原生后端不支持）。"""
        raise NotSupportedError("macOS 原生后端不支持数组绑定变量")

    def bindnames(self) -> list[str]:
        """返回命名绑定参数列表（macOS 原生后端使用 qmark 风格，返回空列表）。"""
        self._check_open()
        return []

    def nextset(self) -> bool | None:
        """跳转到下一个结果集（macOS 原生后端不支持多结果集，返回 None）。"""
        self._check_open()
        return None

    # --- 取行方法 ---

    def fetchone(self) -> Any:
        self._check_open()
        if self._native_result is None or not self._native_result.has_result_set:
            return None
        try:
            row = self._native_result.fetchone()
            if row is None:
                return None
            self.rownumber += 1
            return self._apply_row(row)
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def fetchmany(self, size: int | None = None) -> list[Any]:
        self._check_open()
        size = self.arraysize if size is None else int(size)
        rows = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self) -> list[Any]:
        self._check_open()
        if self._native_result is None or not self._native_result.has_result_set:
            return []
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def __iter__(self) -> Iterator[Any]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def setinputsizes(self, sizes: Sequence[Any]) -> None:
        self._check_open()

    def setoutputsize(self, size: int, column: int | None = None) -> None:
        self._check_open()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"<nspydm.Cursor {state}>"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class Connection:
    def __init__(self, native_conn: NativeConnection, client: DmClient,
                 dsn: str | None = None, user: str | None = None,
                 server: str | None = None, port: int = 5236) -> None:
        self._native_conn = native_conn
        self._client = client
        self._closed = False
        self._autocommit_flag: bool = False
        # dmPython 兼容属性
        self.dsn = dsn
        self.server_status = None
        self.warning = None
        self._user = user or ""
        self._server = server or "127.0.0.1"
        self._port = port
        # 扩展属性
        self.inputTypeHandler: Callable | None = None
        self.outputTypeHandler: Callable | None = None
        self._cursor_class: int = TupleCursor
        self.parse_type: Any = None

    def __repr__(self) -> str:
        return f"<nspydm.Connection to {self._user}@{self._server}:{self._port}>"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._native_conn.close()

    def disconnect(self) -> None:
        """关闭连接（与 close() 等价，dmPython 兼容）。"""
        self.close()

    def commit(self) -> None:
        try:
            self._native_conn.commit()
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def rollback(self) -> None:
        try:
            self._native_conn.rollback()
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def cursor(self, cursorclass: int | None = None) -> Cursor:
        if self._closed:
            raise InterfaceError("connection already closed")
        c = Cursor(self)
        effective_class = cursorclass if cursorclass is not None else self._cursor_class
        if effective_class == DictCursor:
            c._dict_mode = True
        return c

    def ping(self, reconnect: int = 0) -> None:
        """检测连接是否有效（通过发送 commit 探测）。"""
        if self._closed:
            raise InterfaceError("connection already closed")
        try:
            self._client.send({"cmd": "commit", "handle": self._native_conn.handle})
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def debug(self, level: int = DEBUG_OPEN) -> None:
        """设置服务器调试级别（macOS 原生后端通过 SQL 实现）。"""
        if self._closed:
            raise InterfaceError("connection already closed")
        try:
            cur = self.cursor()
            cur.execute(f"SP_SET_PARA_VALUE(1, 'SVR_LOG', {int(level)})")
            cur.close()
        except Exception:
            pass  # 静默失败，与 dmPython 保持一致

    def shutdown(self, mode: str = SHUTDOWN_DEFAULT) -> None:
        """关闭数据库服务器。"""
        if self._closed:
            raise InterfaceError("connection already closed")
        try:
            cur = self.cursor()
            cur.execute(f"SHUTDOWN {mode}")
            cur.close()
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    def explain(self, sql: str) -> str | None:
        """获取 SQL 执行计划（通过 EXPLAIN 语句实现）。"""
        if self._closed:
            raise InterfaceError("connection already closed")
        try:
            cur = self.cursor()
            cur.execute(f"EXPLAIN {sql}")
            rows = cur.fetchall()
            cur.close()
            if rows:
                return "\n".join(str(r) for r in rows)
            return ""
        except Exception as e:
            raise OperationalError(str(e)) from e

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    # --- 属性（与 dmPython 对齐）---

    @property
    def cursor_class(self) -> int:
        """默认游标类型（TupleCursor=0 / DictCursor=1）。"""
        return self._cursor_class

    @cursor_class.setter
    def cursor_class(self, value: int) -> None:
        self._cursor_class = int(value)

    @property
    def autocommit(self) -> bool:
        return self._autocommit_flag

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._autocommit_flag = bool(value)
        try:
            self._native_conn.set_autocommit(self._autocommit_flag)
        except DmClientError as e:
            raise OperationalError(str(e)) from e

    @property
    def autoCommit(self) -> bool:  # noqa: N802
        return self._autocommit_flag

    @autoCommit.setter
    def autoCommit(self, value: bool) -> None:  # noqa: N802
        self.autocommit = value

    @property
    def version(self) -> str:
        v = self._get_server_attr("version")
        return v if v is not None else "DM Database"

    @property
    def server_version(self) -> str:
        v = self._get_server_attr("version")
        return v if v is not None else "DM Database"

    @property
    def current_schema(self) -> str:
        return self._get_server_attr("current_schema")

    @property
    def current_catalog(self) -> str:
        return self._get_server_attr("current_catalog")

    @property
    def user(self) -> str:
        return self._user

    @property
    def server(self) -> str:
        return self._server

    @property
    def port(self) -> int:
        return self._port

    @property
    def inst_name(self) -> str:
        return self._get_server_attr("inst_name")

    @property
    def txn_isolation(self) -> int:
        return self._get_server_attr("txn_isolation")

    @txn_isolation.setter
    def txn_isolation(self, value: int) -> None:
        self._set_server_attr("txn_isolation", value)

    @property
    def access_mode(self) -> int:
        return self._get_server_attr("access_mode")

    @access_mode.setter
    def access_mode(self, value: int) -> None:
        self._set_server_attr("access_mode", value)

    @property
    def compress_msg(self) -> int:
        return self._get_server_attr("compress_msg")

    @compress_msg.setter
    def compress_msg(self, value: int) -> None:
        self._set_server_attr("compress_msg", value)

    @property
    def rwseparate(self) -> int:
        return self._get_server_attr("rwseparate")

    @rwseparate.setter
    def rwseparate(self, value: int) -> None:
        self._set_server_attr("rwseparate", value)

    @property
    def rwseparate_percent(self) -> int:
        return self._get_server_attr("rwseparate_percent")

    @rwseparate_percent.setter
    def rwseparate_percent(self, value: int) -> None:
        self._set_server_attr("rwseparate_percent", value)

    @property
    def connection_timeout(self) -> int:
        return self._get_server_attr("connection_timeout")

    @connection_timeout.setter
    def connection_timeout(self, value: int) -> None:
        self._set_server_attr("connection_timeout", value)

    @property
    def login_timeout(self) -> int:
        return self._get_server_attr("login_timeout")

    @login_timeout.setter
    def login_timeout(self, value: int) -> None:
        self._set_server_attr("login_timeout", value)

    @property
    def lang_id(self) -> int:
        return self._get_server_attr("lang_id")

    @lang_id.setter
    def lang_id(self, value: int) -> None:
        self._set_server_attr("lang_id", value)

    @property
    def local_code(self) -> int:
        return self._get_server_attr("local_code")

    @local_code.setter
    def local_code(self, value: int) -> None:
        self._set_server_attr("local_code", value)

    @property
    def server_code(self) -> int:
        return self._get_server_attr("server_code")

    @property
    def app_name(self) -> str:
        return self._get_server_attr("app_name")

    @app_name.setter
    def app_name(self, value: str) -> None:
        self._set_server_attr("app_name", value)

    @property
    def str_case_sensitive(self) -> int:
        return self._get_server_attr("str_case_sensitive")

    @property
    def max_row_size(self) -> int:
        return self._get_server_attr("max_row_size")

    @property
    def trx_state(self) -> int:
        return self._get_server_attr("trx_state")

    @property
    def use_stmt_pool(self) -> int:
        return self._get_server_attr("use_stmt_pool")

    @use_stmt_pool.setter
    def use_stmt_pool(self, value: int) -> None:
        self._set_server_attr("use_stmt_pool", value)

    @property
    def mpp_login(self) -> int:
        return self._get_server_attr("mpp_login")

    @mpp_login.setter
    def mpp_login(self, value: int) -> None:
        self._set_server_attr("mpp_login", value)

    @property
    def cursor_rollback_behavior(self) -> int:
        return self._get_server_attr("cursor_rollback_behavior")

    @property
    def connection_dead(self) -> bool:
        return self._closed

    @property
    def ssl_path(self) -> str:
        return self._get_server_attr("ssl_path")

    @ssl_path.setter
    def ssl_path(self, value: str) -> None:
        self._set_server_attr("ssl_path", value)

    @property
    def async_enable(self) -> int:
        return self._get_server_attr("async_enable")

    @async_enable.setter
    def async_enable(self, value: int) -> None:
        self._set_server_attr("async_enable", value)

    @property
    def auto_ipd(self) -> int:
        return self._get_server_attr("auto_ipd")

    @auto_ipd.setter
    def auto_ipd(self, value: int) -> None:
        self._set_server_attr("auto_ipd", value)

    @property
    def packet_size(self) -> int:
        return self._get_server_attr("packet_size")

    @packet_size.setter
    def packet_size(self, value: int) -> None:
        self._set_server_attr("packet_size", value)

    @property
    def max_identifier_length(self) -> int:
        return self._get_server_attr("max_identifier_length")

    @property
    def stmtcachesize(self) -> int:
        return self._get_server_attr("stmtcachesize")

    @property
    def outputtypehandler(self) -> Callable | None:
        """outputTypeHandler 的小写别名（cx_Oracle 兼容）。"""
        return self.outputTypeHandler

    @outputtypehandler.setter
    def outputtypehandler(self, value: Callable | None) -> None:
        self.outputTypeHandler = value

    def _get_server_attr(self, name: str) -> Any:
        """通过 dmclient 获取连接属性（不支持时返回 None）。"""
        try:
            resp = self._client.send({
                "cmd": "get_conn_attr",
                "handle": self._native_conn.handle,
                "attr": name,
            })
            if "error" in resp:
                return None
            return resp.get("value")
        except Exception:
            return None

    def _set_server_attr(self, name: str, value: Any) -> None:
        """通过 dmclient 设置连接属性（不支持时静默忽略）。"""
        try:
            resp = self._client.send({
                "cmd": "set_conn_attr",
                "handle": self._native_conn.handle,
                "attr": name,
                "value": value,
            })
            if "error" in resp:
                raise OperationalError(resp["error"])
        except DmClientError as e:
            raise OperationalError(str(e)) from e


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------

def connect(
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    server: str | None = None,
    port: int = 5236,
    dsn: str | None = None,
    url: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    autoCommit: bool | None = None,  # noqa: N803
    autocommit: bool | None = None,
    access_mode: int | None = None,
    connection_timeout: int | None = None,
    login_timeout: int | None = None,
    txn_isolation: int | None = None,
    compress_msg: int | None = None,
    use_stmt_pool: int | None = None,
    cursor_rollback_behavior: int | None = None,
    app_name: str | None = None,
    lang_id: int | None = None,
    local_code: int | None = None,
    ssl_path: str | None = None,
    ssl_pwd: str | None = None,
    mpp_login: int | None = None,
    rwseparate: int | None = None,
    rwseparate_percent: int | None = None,
    ukey_name: str | None = None,
    ukey_pin: str | None = None,
    shake_crypto: str | None = None,
    dmsvc_path: str | None = None,
    cursorclass: int | None = None,
    parse_type: Any = None,
    jars: Sequence[str] | None = None,
    jvm_args: Sequence[str] | None = None,
    driver_class: str = "dm.jdbc.driver.DmDriver",
    properties: Mapping[str, str] | None = None,
) -> Connection:
    """创建达梦数据库连接（macOS 原生后端，无需 JVM）。

    参数与 JDBC 版本兼容，jars/jvm_args/driver_class 等 JVM 相关参数将被忽略。
    新增参数：
        cursorclass: 默认游标类型（TupleCursor=0 / DictCursor=1）。
        parse_type:  解析类型（存储到 connection.parse_type，不影响执行行为）。
    """
    if host is not None and server is not None:
        raise InterfaceError("host or server can only set one")

    _autocommit_flag: bool = False
    if autoCommit is not None:
        _autocommit_flag = bool(autoCommit)
    elif autocommit is not None:
        _autocommit_flag = bool(autocommit)

    _server = host if host is not None else (server if server is not None else "127.0.0.1")

    # 构建 JDBC URL
    if url is None:
        if dsn is not None and dsn.startswith("jdbc:"):
            url = dsn
        elif dsn is not None:
            url = f"jdbc:dm://{dsn}"
        else:
            base = f"{_server}:{int(port)}"
            if catalog is not None:
                base = f"{base}/{catalog}"
            url = f"jdbc:dm://{base}"

    # 构建连接参数
    params = []
    if schema is not None:
        params.append(f"schema={schema}")
    if access_mode is not None:
        params.append(f"accessMode={access_mode}")
    if connection_timeout is not None:
        params.append(f"loginTimeout={connection_timeout}")
    if login_timeout is not None:
        params.append(f"loginTimeout={login_timeout}")
    if txn_isolation is not None:
        params.append(f"transactionIsolation={txn_isolation}")
    if compress_msg is not None:
        params.append(f"compress={compress_msg}")
    if use_stmt_pool is not None:
        params.append(f"useStmtPool={use_stmt_pool}")
    if cursor_rollback_behavior is not None:
        params.append(f"cursorRollbackBehavior={cursor_rollback_behavior}")
    if app_name is not None:
        params.append(f"applicationName={app_name}")
    if lang_id is not None:
        params.append(f"langId={lang_id}")
    if local_code is not None:
        params.append(f"localCode={local_code}")
    if ssl_path is not None:
        params.append(f"sslPath={ssl_path}")
    if ssl_pwd is not None:
        params.append(f"sslPwd={ssl_pwd}")
    if mpp_login is not None:
        params.append(f"mppLogin={mpp_login}")
    if rwseparate is not None:
        params.append(f"rwSeparate={rwseparate}")
    if rwseparate_percent is not None:
        params.append(f"rwSeparatePercent={rwseparate_percent}")
    if ukey_name is not None:
        params.append(f"ukeyName={ukey_name}")
    if ukey_pin is not None:
        params.append(f"ukeyPin={ukey_pin}")
    if shake_crypto is not None:
        params.append(f"shakeCrypto={shake_crypto}")
    if dmsvc_path is not None:
        params.append(f"dmSvcPath={dmsvc_path}")
    if properties:
        for k, v in properties.items():
            params.append(f"{k}={v}")

    if params:
        url = url + ("&" if "?" in url else "?") + "&".join(params)

    # 构建 dsn 字符串（与 dmPython 对齐：server:port 格式）
    _dsn = f"{_server}:{int(port)}"

    try:
        client = _get_client()
        resp = client.send({
            "cmd": "open",
            "url": url or "",
            "user": user or "",
            "password": password or "",
        })
        if "error" in resp:
            raise DmClientError(resp["error"])
        native_conn = NativeConnection(client, resp["handle"])
        conn = Connection(
            native_conn, client,
            dsn=_dsn,
            user=user or "",
            server=_server,
            port=int(port),
        )
        conn._autocommit_flag = _autocommit_flag
        if _autocommit_flag:
            conn.autocommit = True
        # 新增参数
        if cursorclass is not None:
            conn._cursor_class = cursorclass
        conn.parse_type = parse_type
        return conn
    except DmClientError as e:
        raise OperationalError(str(e)) from e


# ---------------------------------------------------------------------------
# DB-API 工具函数
# ---------------------------------------------------------------------------

def Binary(x: bytes | bytearray | memoryview) -> bytes:  # noqa: N802
    return bytes(x)


def Date(year: int, month: int, day: int) -> _dt.date:  # noqa: N802
    return _dt.date(year, month, day)


def DateFromTicks(ticks: float) -> _dt.date:  # noqa: N802
    return _dt.date.fromtimestamp(ticks)


def Time(hour: int, minute: int, second: int) -> _dt.time:  # noqa: N802
    return _dt.time(hour, minute, second)


def TimeFromTicks(ticks: float) -> _dt.time:  # noqa: N802
    return _dt.datetime.fromtimestamp(ticks).time()


def Timestamp(year: int, month: int, day: int, hour: int, minute: int, second: int) -> _dt.datetime:  # noqa: N802
    return _dt.datetime(year, month, day, hour, minute, second)


def TimestampFromTicks(ticks: float) -> _dt.datetime:  # noqa: N802
    return _dt.datetime.fromtimestamp(ticks)


def StringFromBytes(data: bytes | bytearray | memoryview | str) -> str:  # noqa: N802
    """将字节序列转换为字符串（UTF-8 解码）。"""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data).decode("utf-8", errors="replace")
    return str(data)
