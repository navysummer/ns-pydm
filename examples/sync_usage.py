"""ns-pydm 同步模块使用示例。

运行前确保:
  1. dmclient 二进制可用（macOS）或 dmPython 已安装（Linux/Windows）
  2. 达梦数据库运行在 127.0.0.1:5236
  3. SYSDBA/SYSdba@2026 可登录

用法::

    python examples/sync_usage.py
"""

from __future__ import annotations

import os
from datetime import date

import nspydm
from nspydm import create_pool

HOST = os.environ.get("DM_TEST_HOST", "127.0.0.1")
PORT = int(os.environ.get("DM_TEST_PORT", "5236"))
USER = os.environ.get("DM_TEST_USER", "SYSDBA")
PASSWORD = os.environ.get("DM_TEST_PASSWORD", "SYSdba@2026")
TIMEOUT = int(os.environ.get("DM_TEST_TIMEOUT", "5"))  # 连接超时（秒）


def _connect(**kw: object) -> nspydm.Connection:
    return nspydm.connect(login_timeout=TIMEOUT, connection_timeout=TIMEOUT, **kw)  # type: ignore[arg-type]


def demo_basic_connection() -> None:
    """基本连接与查询。"""
    print("=== 基本连接与查询 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()
    cur.execute("SELECT ? AS x", [1])
    print(f"fetchone: {cur.fetchone()}")  # (1,)
    conn.close()


def demo_context_manager() -> None:
    """使用上下文管理器（推荐）。"""
    print("\n=== 上下文管理器 ===")
    with _connect(user=USER, password=PASSWORD, host=HOST, port=PORT) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        print(f"fetchone: {cur.fetchone()}")
    # 正常退出 → 自动 commit + close
    # 异常退出 → 自动 rollback + close


def demo_transaction() -> None:
    """事务管理。"""
    print("\n=== 事务管理 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO dual VALUES (1)")
        conn.commit()
        print("commit 成功")
    except Exception:
        conn.rollback()
        print("rollback")
        raise
    finally:
        conn.close()


def demo_crud() -> None:
    """CRUD 操作。"""
    print("\n=== CRUD 操作 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()

    cur.execute("SELECT 1 AS id, 'Alice' AS name")
    row = cur.fetchone()
    print(f"查询结果: id={row[0]}, name={row[1]}")

    conn.close()


def demo_iteration() -> None:
    """游标迭代。"""
    print("\n=== 游标迭代 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()
    cur.execute("SELECT level FROM dual CONNECT BY level <= 3")
    for row in cur:
        print(f"row: {row}")
    conn.close()


def demo_pool() -> None:
    """连接池。"""
    print("\n=== 连接池 ===")
    pool = create_pool(
        user=USER,
        password=PASSWORD,
        host=HOST,
        port=PORT,
        min_size=2,
        max_size=10,
        max_wait=30.0,
    )

    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        print(f"pool query: {cur.fetchone()}")

    print(f"池状态: size={pool.size}, idle={pool.idle_count}, in_use={pool.in_use_count}")
    pool.close_all()


def demo_autocommit() -> None:
    """自动提交模式。"""
    print("\n=== 自动提交 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT ?", [1])
    print(f"autocommit={conn.autocommit}, result={cur.fetchone()}")
    conn.close()


def demo_parameter_binding() -> None:
    """参数绑定。"""
    print("\n=== 参数绑定 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()

    # qmark 风格
    cur.execute("SELECT ? AS v", [42])
    print(f"qmark: {cur.fetchone()}")

    # 多个参数
    cur.execute("SELECT ? AS a, ? AS b", [1, 2])
    print(f"多参数: {cur.fetchone()}")

    conn.close()


def demo_date_type() -> None:
    """日期类型。"""
    print("\n=== 日期类型 ===")
    conn = _connect(user=USER, password=PASSWORD, host=HOST, port=PORT)
    cur = conn.cursor()
    cur.execute("SELECT ?", [date.today()])
    row = cur.fetchone()
    print(f"date: {row}, type={type(row).__name__}")
    conn.close()


def main() -> None:
    funcs = [
        demo_basic_connection,
        demo_context_manager,
        demo_transaction,
        demo_crud,
        demo_iteration,
        demo_pool,
        demo_autocommit,
        demo_parameter_binding,
        demo_date_type,
    ]

    for func in funcs:
        try:
            func()
        except Exception as e:
            print(f"[跳过 {func.__name__}] {e}")


if __name__ == "__main__":
    main()
