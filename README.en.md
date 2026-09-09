# ns-pydm

Dameng (DM) Database Python driver with DB-API 2.0 interface and **cross-platform smart routing**.

| OS | Backend | Dependencies | Notes |
|----|---------|-------------|-------|
| **macOS** (Intel / Apple Silicon) | GraalVM native-image dmclient | No Java required | Native binary, ~43MB |
| **Linux** | Official dmPython passthrough | `pip install dmPython` | Zero wrapping, zero overhead |
| **Windows** | Official dmPython passthrough | `pip install dmPython` | Zero wrapping, zero overhead |

> 💡 **On non-macOS platforms, dmPython is used directly with zero performance overhead.**

## Features

- Full DB-API 2.0 compliance (`apilevel="2.0"`)
- Cross-platform unified API — one codebase for macOS / Linux / Windows
- macOS native backend without JVM — eliminates OOM and process kill issues
- Built-in thread-safe connection pool (`ConnectionPool`)
- Native async/await support (`AsyncCursor`, `AsyncPool`, `AsyncTransaction`)
- Supports `?` placeholder (qmark), auto-compatible with `%s` format
- Multi-architecture support: macOS ARM64 (M1/M2/M3/M4) + Intel x64
- Transaction management, autocommit, context managers
- Automatic type conversion for BLOB / CLOB / Date / Decimal

## Install

```bash
pip install ns-pydm

# On Linux/Windows, also install:
pip install dmPython
```

## Quick Start

### Basic Connection & Query

```python
import nspydm

conn = nspydm.connect(
    user="SYSDBA",
    password="SYSDBA",
    server="127.0.0.1",
    port=5236,
)

cur = conn.cursor()
cur.execute("SELECT ? AS x", [1])
print(cur.fetchone())  # (1,)

conn.close()
```

### Context Manager (Recommended)

Auto commit/rollback + auto close:

```python
import nspydm

with nspydm.connect(user="SYSDBA", password="SYSDBA", server="127.0.0.1", port=5236) as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())  # (1,)
# Normal exit → auto commit + close
# Exception exit → auto rollback + close
```

## Connection Parameters

```python
nspydm.connect(
    user="SYSDBA",              # Username
    password="SYSDBA",          # Password
    server="127.0.0.1",         # Server address (or use host)
    port=5236,                  # Port, default 5236
    dsn=None,                   # DSN string
    url=None,                   # JDBC URL (highest priority, macOS dmclient backend only)
    schema=None,                # Default schema
    catalog=None,               # Database catalog
    autocommit=False,           # Autocommit (also accepts autoCommit)
    properties=None,            # Extra connection properties (macOS dmclient backend only)
    # Advanced parameters (macOS backend only)
    ssl_path=None,              # SSL certificate path
    ssl_pwd=None,               # SSL password
    login_timeout=None,         # Login timeout (seconds)
    connection_timeout=None,    # Connection timeout (seconds)
    txn_isolation=None,         # Transaction isolation level
)
```

**Notes:**
- `server` and `host` are equivalent; set only one
- `url` has highest priority; if set, server/host/port/dsn are ignored
- If `dsn` starts with `jdbc:`, it's used directly; otherwise auto-prefixed as `jdbc:dm://{dsn}`
- Both `autocommit` and `autoCommit` are accepted

## Parameter Binding

Use `?` placeholder (qmark style, recommended):

```python
cur = conn.cursor()

# Single parameter
cur.execute("SELECT * FROM users WHERE id = ?", [42])

# Multiple parameters
cur.execute("SELECT * FROM users WHERE age > ? AND city = ?", [18, "Beijing"])

# INSERT
cur.execute("INSERT INTO users (name, age) VALUES (?, ?)", ["Alice", 30])

# Also compatible with %s format (auto-converted)
cur.execute("SELECT * FROM users WHERE id = %s", [42])
```

## Transaction Management

```python
import nspydm

conn = nspydm.connect(user="SYSDBA", password="SYSDBA", server="127.0.0.1", port=5236)
cur = conn.cursor()

try:
    cur.execute("INSERT INTO orders (product, qty) VALUES (?, ?)", ["Widget", 10])
    cur.execute("UPDATE inventory SET stock = stock - ? WHERE product = ?", [10, "Widget"])
    conn.commit()
except Exception as e:
    conn.rollback()
    raise
finally:
    conn.close()
```

### Autocommit Mode

```python
# Option 1: Set at connection time
conn = nspydm.connect(..., autocommit=True)

# Option 2: Toggle at runtime
conn.autocommit = True   # Enable
conn.autocommit = False  # Disable
```

## CRUD Operations

### Create Table

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

### Insert Data

```python
# Single row
cur.execute(
    "INSERT INTO employees (id, name, salary, hire_date, active) VALUES (?, ?, ?, ?, ?)",
    [1, "Alice", 85000.50, "2023-01-15", True]
)

# Batch insert
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
print(f"Inserted {cur.rowcount} rows")
```

### Query Data

```python
# fetchone — get one row
cur.execute("SELECT * FROM employees WHERE id = ?", [1])
row = cur.fetchone()
print(row)  # (1, 'Alice', Decimal('85000.50'), datetime.date(2023, 1, 15), True)

# fetchmany — get N rows
cur.execute("SELECT * FROM employees ORDER BY id")
rows = cur.fetchmany(2)
print(rows)  # [(1, 'Alice', ...), (2, 'Bob', ...)]

# fetchall — get all rows
cur.execute("SELECT name, salary FROM employees WHERE active = ?", [True])
for row in cur.fetchall():
    print(f"{row[0]}: {row[1]}")

# Iterator
cur.execute("SELECT name FROM employees")
for row in cur:
    print(row[0])
```

### Update & Delete

```python
# Update
cur.execute("UPDATE employees SET salary = ? WHERE id = ?", [95000.00, 1])
conn.commit()
print(f"Updated {cur.rowcount} rows")

# Delete
cur.execute("DELETE FROM employees WHERE active = ?", [False])
conn.commit()
print(f"Deleted {cur.rowcount} rows")
```

## Type Mapping

| DM Database Type | Python Type | Example |
|-----------------|------------|---------|
| INT / BIGINT / SMALLINT | `int` | `42` |
| DECIMAL / NUMERIC | `Decimal` | `Decimal('85000.50')` |
| DOUBLE / FLOAT / REAL | `float` | `3.14` |
| VARCHAR / CHAR / CLOB | `str` | `'hello'` |
| DATE | `datetime.date` | `date(2023, 1, 15)` |
| TIMESTAMP | `datetime.datetime` | `datetime(2023, 1, 15, 10, 30, 0)` |
| BLOB / BINARY | `bytes` | `b'\x89PNG...'` |
| BOOLEAN | `bool` | `True` |

## Connection Pool

### Create Pool

```python
from nspydm import create_pool

pool = create_pool(
    user="SYSDBA",
    password="SYSDBA",
    server="127.0.0.1",
    port=5236,
    min_size=2,       # Initial connections
    max_size=20,      # Maximum connections
    max_wait=30.0,    # Connection wait timeout (seconds)
)
```

### Use Pool

```python
# Option 1: Context manager (recommended)
with pool.get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())
# Connection auto-returned to pool

# Option 2: Manual get/return
conn = pool.get_connection()
try:
    cur = conn.cursor()
    cur.execute("SELECT 1")
    print(cur.fetchone())
finally:
    conn.close()  # Returns to pool, not actual close
```

### Monitor Pool

```python
print(pool.size)           # Total connections
print(pool.idle_count)     # Idle connections
print(pool.in_use_count)   # In-use connections
print(pool.total_created)  # Total created historically
print(pool.closed)         # Whether pool is closed
print(repr(pool))          # <ConnectionPool(open) idle=2 in_use=0 max=10>
```

## Async Support (async/await)

ns-pydm provides full async/await support. All blocking operations are offloaded to a thread pool via `asyncio.to_thread`, keeping the event loop responsive.

### Async Connection

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

### Async Transactions & Savepoints

```python
async with await conn.cursor() as cur:
    # Auto-managed
    async with cur.begin():
        await cur.execute("INSERT INTO t VALUES (1)")

    # Manual control
    txn = await cur.begin()
    await cur.execute("INSERT INTO t VALUES (2)")
    await txn.commit()

    # Nested (savepoint)
    async with cur.begin():
        await cur.execute("INSERT INTO t VALUES (3)")
        async with cur.begin_nested():
            await cur.execute("INSERT INTO t VALUES (4)")
```

### Async Connection Pool

```python
from nspydm import async_create_pool

async def main():
    pool = await async_create_pool(
        user="SYSDBA", password="SYSDBA", server="127.0.0.1",
        minsize=2, maxsize=10, pool_recycle=3600,
    )

    # Acquire connection
    async with pool.acquire() as conn:
        async with await conn.cursor() as cur:
            await cur.execute("SELECT 1")
            print(await cur.fetchone())

    # Direct cursor (auto return connection)
    async with pool.cursor() as cur:
        await cur.execute("SELECT 1")
        print(await cur.fetchone())

    pool.close()
    await pool.wait_closed()

asyncio.run(main())
```

### Cursor Properties

```python
async with await conn.cursor() as cur:
    await cur.execute("SELECT 1")
    print(cur.rowcount)       # Row count
    print(cur.connection)     # Parent connection
    print(cur.raw)            # Underlying sync cursor
    print(cur.lastrowid)      # Last insert ID
    print(cur.query)          # Last SQL
    print(cur.name)           # Cursor name
    print(cur.scrollable)     # Whether scrollable
    print(cur.withhold)       # Hold rows after commit
    print(cur.itersize)       # Iteration chunk size
```

### Async Pool Benchmark

```text
acquire/release x2000: 0.015s  (129,000 ops/s)
cursor() x500:        0.440s  (1,100 ops/s)
```

## Multi-threading

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

## Complete Examples

### Example 1: Web Service Database Layer

```python
import nspydm
from nspydm import create_pool

pool = create_pool(
    user="SYSDBA", password="your_password", server="192.168.1.100",
    min_size=5, max_size=50, max_wait=10.0,
)

def get_user_by_id(user_id: int):
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email FROM users WHERE id = ?", [user_id])
        return cur.fetchone()

def create_user(name: str, email: str):
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (name, email) VALUES (?, ?)", [name, email])
```

### Example 2: Data Migration

```python
import nspydm

def migrate_data(source_params, target_params):
    src = nspydm.connect(**source_params)
    dst = nspydm.connect(**target_params)
    try:
        src_cur = src.cursor()
        src_cur.execute("SELECT id, name, value FROM source_table")
        rows = src_cur.fetchall()

        dst_cur = dst.cursor()
        dst_cur.executemany(
            "INSERT INTO target_table (id, name, value) VALUES (?, ?, ?)", rows
        )
        dst.commit()
        print(f"Migration complete: {len(rows)} records")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
```

### Example 3: BLOB File Storage

```python
import nspydm

def save_file(filename: str, data: bytes):
    with nspydm.connect(user="SYSDBA", password="SYSDBA",
                         server="127.0.0.1", port=5236) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO files (name, content) VALUES (?, ?)",
            [filename, nspydm.Binary(data)]
        )

def load_file(filename: str) -> bytes:
    with nspydm.connect(user="SYSDBA", password="SYSDBA",
                         server="127.0.0.1", port=5236) as conn:
        cur = conn.cursor()
        cur.execute("SELECT content FROM files WHERE name = ?", [filename])
        row = cur.fetchone()
        return row[0] if row else None
```

## Architecture

```
┌─────────────────────────────────────────────┐
│                 nspydm                      │
│       (Cross-platform unified API)          │
├────────────────┬────────────────────────────┤
│  macOS         │  Linux / Windows          │
│                │                           │
│  dmclient      │  dmPython                 │
│  (native       │  (Official C driver)       │
│   executable)  │                           │
│    ↓           │    ↓                      │
│  DM JDBC       │  DPI (C API)             │
│    ↓           │    ↓                      │
├────────────────┴────────────────────────────┤
│           DM Database Server               │
└─────────────────────────────────────────────┘

┌──────────────────────────────────────┐
│       nspydm.async_api              │
│  (Async/await support)              │
│                                     │
│  AsyncCursor → _to_thread → sync    │
│  AsyncConnection                    │
│  AsyncTransaction → IsolationLevel   │
│  AsyncPool → Pool context manager   │
└──────────────────────────────────────┘
```

> dmclient source & binaries: https://github.com/navysummer/dm-client/releases

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DMCLIENT_PATH` | Full path to dmclient executable (macOS only) |

## dmclient Native Client

The `dmclient` native executable used by the macOS backend is maintained as a separate project:

👉 **https://github.com/navysummer/dm-client/releases**

That repository contains the GraalVM native-image build scripts, precompiled binaries, and detailed build instructions.

## License

This project distributes source code only. The dmclient binary is provided by [navysummer/dm-client](https://github.com/navysummer/dm-client/releases).
Compiling it depends on the Dameng JDBC driver, whose distribution is subject to Dameng's license agreement.

## Migration from Old Version

The old version was based on JPype + JVM; the new version uses cross-platform smart routing. API is fully compatible:

```python
# Old (required Java)
# New (no Java needed; jars/jvm_args params are kept but ignored)
conn = nspydm.connect(user="...", password="...", server="...")
```
