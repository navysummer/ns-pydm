# ns-pydm

达梦（DM）数据库 Python 驱动，DB-API 2.0 风格接口，**跨平台智能路由**。

| 操作系统 | 后端 | 依赖 | 说明 |
|---------|------|------|------|
| **macOS** (Intel / M系列) | GraalVM native-image dmclient | 无需 Java | 原生可执行文件，~43MB |
| **Linux** | 官方 dmPython 透传 | `pip install dmPython` | 零包装，零性能损失 |
| **Windows** | 官方 dmPython 透传 | `pip install dmPython` | 零包装，零性能损失 |

> 💡 **非 macOS 上不做任何包装，直接透传 dmPython，零性能损失。**

## 特性

- 完整的 DB-API 2.0 飞行合规（`apilevel="2.0"`）
- 跨平台统一 API，一套代码跑遍 macOS / Linux / Windows
- macOS 原生后端无需 JVM，彻底避免 OOM 和进程被杀问题
- 内置线程安全连接池（`ConnectionPool`）
- 原生异步支持（`async/await` + 异步连接池 `AsyncPool`）
- 支持 `?` 占位符（qmark），自动兼容 `%s` 格式
- 支持多架构：macOS ARM64 (M1/M2/M3/M4) + Intel x64
- 事务管理、自动提交、上下文管理器
- BLOB / CLOB / 日期 / Decimal 类型自动转换

## 安装

```bash
pip install ns-pydm

# 非 macOS 系统（Linux/Windows）需额外安装：
pip install dmPython
```

## 快速开始

### 基本连接与查询

```python
import nspydm

# 创建连接
conn = nspydm.connect(
    user="SYSDBA",
    password="SYSDBA",
    server="127.0.0.1",
    port=5236,
)

# 执行查询
cur = conn.cursor()
cur.execute("SELECT ? AS x", [1])
print(cur.fetchone())  # (1,)

# 关闭连接
conn.close()
```

### 使用上下文管理器（推荐）

自动提交/回滚 + 自动关闭连接：

```python
import nspydm

with nspydm.connect(user="SYSDBA", password="SYSDBA", server="127.0.0.1", port=5236) as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())  # (1,)
# 正常退出 → 自动 commit + close
# 异常退出 → 自动 rollback + close
```

## 连接参数

```python
nspydm.connect(
    user="SYSDBA",              # 用户名
    password="SYSDBA",          # 密码
    server="127.0.0.1",         # 服务器地址（也可用 host）
    port=5236,                  # 端口，默认 5236
    dsn=None,                   # DSN 字符串
    url=None,                   # JDBC URL（优先级最高，仅 macOS dmclient 后端）
    schema=None,                # 默认 schema
    catalog=None,               # 数据库 catalog
    autocommit=False,           # 自动提交（也接受 autoCommit）
    properties=None,            # 额外的连接属性（仅 macOS dmclient 后端）
    # 高级参数（仅 macOS 后端生效）
    ssl_path=None,              # SSL 证书路径
    ssl_pwd=None,               # SSL 密码
    login_timeout=None,         # 登录超时（秒）
    connection_timeout=None,    # 连接超时（秒）
    txn_isolation=None,         # 事务隔离级别
)
```

**参数说明：**
- `server` 和 `host` 效果相同，只能设其中一个
- `url` 优先级最高，设置后将忽略 server/host/port/dsn
- `dsn` 若以 `jdbc:` 开头则直接使用，否则自动拼接为 `jdbc:dm://{dsn}`
- `autocommit` 和 `autoCommit` 都支持

## 参数绑定

推荐使用 `?` 占位符（qmark 风格）：

```python
cur = conn.cursor()

# 单参数
cur.execute("SELECT * FROM users WHERE id = ?", [42])

# 多参数
cur.execute("SELECT * FROM users WHERE age > ? AND city = ?", [18, "Beijing"])

# INSERT
cur.execute("INSERT INTO users (name, age) VALUES (?, ?)", ["Alice", 30])

# 也兼容 %s 格式（自动转换）
cur.execute("SELECT * FROM users WHERE id = %s", [42])
```

## 事务管理

```python
import nspydm

conn = nspydm.connect(user="SYSDBA", password="SYSDBA", server="127.0.0.1", port=5236)
cur = conn.cursor()

try:
    cur.execute("INSERT INTO orders (product, qty) VALUES (?, ?)", ["Widget", 10])
    cur.execute("UPDATE inventory SET stock = stock - ? WHERE product = ?", [10, "Widget"])
    conn.commit()        # 提交事务
except Exception as e:
    conn.rollback()      # 回滚事务
    raise
finally:
    conn.close()
```

### 自动提交模式

```python
# 方式一：连接时设置
conn = nspydm.connect(..., autocommit=True)

# 方式二：运行时切换
conn.autocommit = True   # 开启
conn.autocommit = False  # 关闭
```

## 数据操作 CRUD

### 建表

```python
cur.execute("""
    CREATE TABLE employees (
        id INT PRIMARY KEY,
        name VARCHAR(100),
        salary DECIMAL(10,2),
        hire_date DATE,
        active BOOLEAN
    )
""")
conn.commit()
```

### 插入数据

```python
# 单行插入
cur.execute(
    "INSERT INTO employees (id, name, salary, hire_date, active) VALUES (?, ?, ?, ?, ?)",
    [1, "Alice", 85000.50, "2023-01-15", True]
)

# 批量插入
employees = [
    [2, "Bob", 72000.00, "2023-03-20", True],
    [3, "Carol", 91000.75, "2022-11-01", True],
    [4, "David", 68000.00, "2024-01-10", False],
]
cur.executemany(
    "INSERT INTO employees (id, name, salary, hire_date, active) VALUES (?, ?, ?, ?, ?)",
    employees
)
conn.commit()
print(f"插入 {cur.rowcount} 行")
```

### 查询数据

```python
# fetchone — 取一行
cur.execute("SELECT * FROM employees WHERE id = ?", [1])
row = cur.fetchone()
print(row)  # (1, 'Alice', Decimal('85000.50'), datetime.date(2023, 1, 15), True)

# fetchmany — 取 N 行
cur.execute("SELECT * FROM employees ORDER BY id")
rows = cur.fetchmany(2)
print(rows)  # [(1, 'Alice', ...), (2, 'Bob', ...)]

# fetchall — 取全部
cur.execute("SELECT name, salary FROM employees WHERE active = ?", [True])
for row in cur.fetchall():
    print(f"{row[0]}: {row[1]}")

# 迭代器方式
cur.execute("SELECT name FROM employees")
for row in cur:
    print(row[0])
```

### 更新数据

```python
cur.execute("UPDATE employees SET salary = ? WHERE id = ?", [95000.00, 1])
conn.commit()
print(f"更新 {cur.rowcount} 行")
```

### 删除数据

```python
cur.execute("DELETE FROM employees WHERE active = ?", [False])
conn.commit()
print(f"删除 {cur.rowcount} 行")
```

### 删表

```python
cur.execute("DROP TABLE employees")
conn.commit()
```

## 数据类型映射

| 达梦数据库类型 | Python 类型 | 示例 |
|-------------|------------|------|
| INT / BIGINT / SMALLINT | `int` | `42` |
| DECIMAL / NUMERIC | `Decimal` | `Decimal('85000.50')` |
| DOUBLE / FLOAT / REAL | `float` | `3.14` |
| VARCHAR / CHAR / CLOB | `str` | `'hello'` |
| DATE | `datetime.date` | `date(2023, 1, 15)` |
| TIMESTAMP | `datetime.datetime` | `datetime(2023, 1, 15, 10, 30, 0)` |
| BLOB / BINARY | `bytes` | `b'\x89PNG...'` |
| BOOLEAN | `bool` | `True` |

**写入时的参数转换：**

| Python 类型 | 达梦数据库接受方式 |
|------------|----------------|
| `int` / `float` | 直接传递 |
| `Decimal` | 自动转字符串 |
| `datetime` / `date` / `time` | 自动转 ISO 格式字符串 |
| `bytes` / `bytearray` | 自动 Base64 编码 |
| `str` | 直接传递 |

## 连接池

适用于高频连接场景，避免反复创建/销毁连接的开销：

### 创建连接池

```python
from nspydm import create_pool

pool = create_pool(
    user="SYSDBA",
    password="SYSDBA",
    server="127.0.0.1",
    port=5236,
    min_size=2,       # 初始连接数
    max_size=20,      # 最大连接数
    max_wait=30.0,    # 获取连接超时（秒）
)
```

### 使用连接池

```python
# 方式一：上下文管理器（推荐）
with pool.get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())
# 退出 with 块后连接自动归还到池

# 方式二：手动获取/归还
conn = pool.get_connection()
try:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())
finally:
    conn.close()  # 不是真关闭，而是归还到池
```

### 连接池上下文管理器

```python
# 池级别的上下文管理器
with create_pool(user="SYSDBA", password="SYSDBA", server="127.0.0.1") as pool:
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        print(cur.fetchone())
# 退出时自动关闭所有连接
```

### 监控连接池状态

```python
pool = create_pool(user="SYSDBA", password="SYSDBA", server="127.0.0.1",
                   min_size=2, max_size=10)

print(pool.size)           # 当前总连接数
print(pool.idle_count)     # 空闲连接数
print(pool.in_use_count)   # 正在使用的连接数
print(pool.total_created)  # 历史创建的总连接数
print(pool.closed)         # 池是否已关闭
print(repr(pool))          # <ConnectionPool(open) idle=2 in_use=0 max=10>
```

### 关闭连接池

```python
pool.close_all()  # 关闭所有连接，池进入关闭状态
```

## 异步支持（async/await）

ns-pydm 提供完整的 `async/await` 异步支持，所有阻塞操作通过 `asyncio.to_thread` 委托到线程池，不阻塞事件循环。

### 异步连接

```python
import asyncio
from nspydm import async_connect

async def main():
    conn = await async_connect(user="SYSDBA", password="SYSDBA", server="127.0.0.1")
    async with conn:
        async with await conn.cursor() as cur:
            await cur.execute("SELECT ?", [1])
            row = await cur.fetchone()
            print(row)  # (1,)

asyncio.run(main())
```

### 异步事务与保存点

```python
async with await conn.cursor() as cur:
    # 方式一：自动管理
    async with cur.begin():
        await cur.execute("INSERT INTO t VALUES (1)")

    # 方式二：手动控制
    txn = await cur.begin()
    await cur.execute("INSERT INTO t VALUES (2)")
    await txn.commit()

    # 支持保存点
    async with cur.begin():
        await cur.execute("INSERT INTO t VALUES (3)")
        async with cur.begin_nested():
            await cur.execute("INSERT INTO t VALUES (4)")
            # 回滚保存点
```

### 异步连接池

```python
from nspydm import async_create_pool

async def main():
    pool = await async_create_pool(
        user="SYSDBA", password="SYSDBA", server="127.0.0.1",
        minsize=2, maxsize=10, pool_recycle=3600,
    )

    # 方式一：获取连接
    async with pool.acquire() as conn:
        async with await conn.cursor() as cur:
            await cur.execute("SELECT 1")
            print(await cur.fetchone())

    # 方式二：直接获取游标（自动归还连接）
    async with pool.cursor() as cur:
        await cur.execute("SELECT 1")
        print(await cur.fetchone())

    pool.close()
    await pool.wait_closed()

asyncio.run(main())
```

### 异步游标属性

```python
async with await conn.cursor() as cur:
    await cur.execute("SELECT 1")
    print(cur.rowcount)       # 影响行数
    print(cur.connection)     # 父连接引用
    print(cur.raw)            # 底层同步游标
    print(cur.lastrowid)      # 最后插入 ID
    print(cur.query)          # 最后执行的 SQL
    print(cur.name)           # 游标名称
    print(cur.scrollable)     # 是否可滚动
    print(cur.withhold)       # 提交后是否保留结果集
    print(cur.itersize)       # 迭代块大小
```

### 隔离级别

```python
from nspydm._async import IsolationLevel

# 四种隔离级别
IsolationLevel.default          # 默认
IsolationLevel.read_committed   # 读已提交
IsolationLevel.repeatable_read  # 可重复读
IsolationLevel.serializable     # 可串行化

# 使用时指定
async with cur.begin(IsolationLevel.serializable, readonly=True):
    await cur.execute("SELECT COUNT(*) FROM big_table")
```

### 异步连接池基准

```text
acquire/release x2000: 0.015s  (129,000 ops/s)
cursor() x500:        0.440s  (1,100 ops/s)
```

## 多线程使用

```python
import threading
import nspydm

def worker(pool, thread_id):
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ? AS thread", [thread_id])
        result = cur.fetchone()
        print(f"Thread {thread_id}: {result}")

pool = nspydm.create_pool(
    user="SYSDBA", password="SYSDBA", server="127.0.0.1",
    min_size=2, max_size=10,
)

threads = []
for i in range(10):
    t = threading.Thread(target=worker, args=(pool, i))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

pool.close_all()
```

## DB-API 2.0 兼容性

```python
import nspydm

# 模块属性
print(nspydm.apilevel)       # "2.0"
print(nspydm.threadsafety)   # 1
print(nspydm.paramstyle)     # "qmark"

# 类型常量
print(nspydm.STRING)         # "STRING"
print(nspydm.BINARY)         # "BINARY"
print(nspydm.NUMBER)         # "NUMBER"
print(nspydm.DATETIME)       # "DATETIME"
print(nspydm.ROWID)          # "ROWID"

# 类型构造函数
d = nspydm.Date(2024, 1, 15)              # datetime.date(2024, 1, 15)
t = nspydm.Time(10, 30, 0)                # datetime.time(10, 30)
ts = nspydm.Timestamp(2024, 1, 15, 10, 30, 0)  # datetime.datetime(2024, 1, 15, 10, 30)
b = nspydm.Binary(b"hello")               # b'hello'

# 异常层次
# Warning → Error → InterfaceError / DatabaseError → DataError / OperationalError / ...
```

## 完整使用案例

### 案例1：Web 服务数据库层

```python
import nspydm
from nspydm import create_pool

# 应用启动时创建连接池
pool = create_pool(
    user="SYSDBA",
    password="your_password",
    server="192.168.1.100",
    port=5236,
    min_size=5,
    max_size=50,
    max_wait=10.0,
)

def get_user_by_id(user_id: int):
    """根据 ID 查询用户"""
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email FROM users WHERE id = ?", [user_id])
        return cur.fetchone()

def create_user(name: str, email: str):
    """创建新用户"""
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)",
            [name, email]
        )
        # with 块正常退出自动 commit

def update_user_email(user_id: int, new_email: str):
    """更新用户邮箱"""
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET email = ? WHERE id = ?",
            [new_email, user_id]
        )

# 应用关闭时
# pool.close_all()
```

### 案例2：数据迁移脚本

```python
import nspydm

def migrate_data(source_conn_params, target_conn_params):
    """从源库读取数据，写入目标库"""
    src = nspydm.connect(**source_conn_params)
    dst = nspydm.connect(**target_conn_params)

    try:
        # 读取源数据
        src_cur = src.cursor()
        src_cur.execute("SELECT id, name, value FROM source_table")
        rows = src_cur.fetchall()

        # 批量写入目标库
        dst_cur = dst.cursor()
        dst_cur.executemany(
            "INSERT INTO target_table (id, name, value) VALUES (?, ?, ?)",
            rows
        )
        dst.commit()
        print(f"迁移完成，共 {len(rows)} 条记录")

    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()

# 使用
migrate_data(
    source_conn_params={"user": "SYSDBA", "password": "src_pwd", "server": "10.0.0.1", "port": 5236},
    target_conn_params={"user": "SYSDBA", "password": "dst_pwd", "server": "10.0.0.2", "port": 5236},
)
```

### 案例3：定时报表生成

```python
import nspydm
from datetime import date

def generate_daily_report():
    """生成每日报表"""
    with nspydm.connect(user="SYSDBA", password="SYSDBA",
                         server="127.0.0.1", port=5236) as conn:
        cur = conn.cursor()

        # 查询今日订单汇总
        today = date.today().isoformat()
        cur.execute("""
            SELECT product, SUM(qty) AS total_qty, SUM(amount) AS total_amount
            FROM orders
            WHERE order_date = ?
            GROUP BY product
            ORDER BY total_amount DESC
        """, [today])

        results = cur.fetchall()
        print(f"=== {today} 订单报表 ===")
        print(f"{'产品':<20} {'数量':>10} {'金额':>15}")
        print("-" * 47)
        for row in results:
            print(f"{row[0]:<20} {row[1]:>10} {row[2]:>15}")

        return results

generate_daily_report()
```

### 案例4：BLOB 文件存储

```python
import nspydm

def save_file(filename: str, data: bytes):
    """将文件保存到数据库"""
    with nspydm.connect(user="SYSDBA", password="SYSDBA",
                         server="127.0.0.1", port=5236) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO files (name, content) VALUES (?, ?)",
            [filename, nspydm.Binary(data)]
        )

def load_file(filename: str) -> bytes:
    """从数据库读取文件"""
    with nspydm.connect(user="SYSDBA", password="SYSDBA",
                         server="127.0.0.1", port=5236) as conn:
        cur = conn.cursor()
        cur.execute("SELECT content FROM files WHERE name = ?", [filename])
        row = cur.fetchone()
        return row[0] if row else None

# 使用
with open("report.pdf", "rb") as f:
    save_file("report.pdf", f.read())

data = load_file("report.pdf")
with open("report_copy.pdf", "wb") as f:
    f.write(data)
```

## 架构

```
┌─────────────────────────────────────────────┐
│                 nspydm                      │
│         (跨平台统一 API 入口)                │
├────────────────┬────────────────────────────┤
│  macOS         │  Linux / Windows          │
│                │                           │
│  dmclient      │  dmPython                 │
│  (原生可执行    │  (官方 C 原生驱动)         │
│   文件)         │                           │
│    ↓           │    ↓                      │
│  DM JDBC       │  DPI (C API)             │
│    ↓           │    ↓                      │
├────────────────┴────────────────────────────┤
│           DM Database Server               │
└─────────────────────────────────────────────┘

┌──────────────────────────────────────┐
│         nspydm.async_api            │
│  (异步 async/await 支持)            │
│                                     │
│  AsyncCursor → _to_thread → 同步驱动 │
│  AsyncConnection                    │
│  AsyncTransaction → IsolationLevel   │
│  AsyncPool → Pool 上下文管理器       │
└──────────────────────────────────────┘
```

> dmclient 源码及预编译二进制：https://github.com/navysummer/dm-client/releases

## 环境变量

| 变量名 | 说明 |
|-------|------|
| `DMCLIENT_PATH` | 指定 dmclient 可执行文件的完整路径（仅 macOS） |

## dmclient 原生客户端

macOS 后端所使用的 `dmclient` 原生可执行文件由独立项目维护：

👉 **https://github.com/navysummer/dm-client/releases**

该仓库包含 GraalVM native-image 构建脚本、预编译二进制文件以及详细的编译说明。

## 许可证

本项目仅分发源代码。dmclient 二进制文件由 [navysummer/dm-client](https://github.com/navysummer/dm-client/releases) 提供，
其编译依赖达梦 JDBC 驱动，分发需遵循达梦数据库的许可协议。

## 从旧版迁移

旧版基于 JPype + JVM，新版为跨平台智能路由。API 完全兼容：

```python
# 旧版（需要 Java）
# 新版（无需 Java，jars/jvm_args 参数保留但忽略）
conn = nspydm.connect(user="...", password="...", server="...")
```
