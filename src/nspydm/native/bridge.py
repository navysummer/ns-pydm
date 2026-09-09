"""native 后端：通过 subprocess 与 dmclient 可执行文件通信。

dmclient 是 GraalVM native-image 编译的独立可执行文件，
通过 stdin/stdout JSON 行协议与达梦数据库交互，无需 JVM。
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# dmclient 可执行文件查找
# ---------------------------------------------------------------------------

def _get_arch_suffix() -> str:
    """返回当前平台的架构后缀，用于查找对应的 dmclient 二进制。"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # macOS
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        elif machine in ("x86_64", "amd64", "i386", "i686"):
            return "macos-x64"
    # Linux
    elif system == "linux":
        if machine in ("arm64", "aarch64"):
            return "linux-arm64"
        elif machine in ("x86_64", "amd64"):
            return "linux-x64"
    # Windows
    elif system == "windows":
        if machine in ("arm64", "aarch64"):
            return "windows-arm64"
        elif machine in ("x86_64", "amd64", "i386", "i686"):
            return "windows-x64"

    # 未知平台，返回通用名
    return ""


def _find_dmclient() -> str:
    """查找 dmclient 可执行文件，支持多架构。"""
    system = platform.system().lower()
    arch_suffix = _get_arch_suffix()

    # Windows 需要 .exe 后缀
    if system == "windows":
        base_names = [f"dmclient-{arch_suffix}.exe"] if arch_suffix else []
        base_names += ["dmclient.exe"]
    else:
        base_names = [f"dmclient-{arch_suffix}"] if arch_suffix else []
        base_names += ["dmclient"]

    # 搜索路径
    search_dirs = [
        Path(__file__).resolve().parent.parent / "native_bridge" / "target",
        Path(__file__).resolve().parent.parent.parent / "native_bridge" / "target",
        Path.cwd() / "native_bridge" / "target",
    ]

    # 环境变量
    env_path = os.environ.get("DMCLIENT_PATH")
    if env_path:
        search_dirs.insert(0, Path(env_path).parent)
        # 如果 DMCLIENT_PATH 直接指向文件
        if Path(env_path).exists():
            return str(Path(env_path))

    for d in search_dirs:
        if not d.exists():
            continue
        for name in base_names:
            candidate = d / name
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

    # 最后尝试 PATH
    import shutil
    for name in base_names:
        found = shutil.which(name)
        if found:
            return found

    # 错误提示包含当前架构信息
    arch_info = f"{system}-{platform.machine()}" if arch_suffix else "unknown"
    raise FileNotFoundError(
        f"找不到 dmclient 可执行文件（当前架构：{arch_info}）。\n"
        f"请先编译 native_bridge 项目：\n"
        f"  cd native_bridge && ./build.sh\n"
        f"或者设置环境变量 DMCLIENT_PATH 指向正确的二进制文件。"
    )


# ---------------------------------------------------------------------------
# DmClient 进程管理
# ---------------------------------------------------------------------------

class DmClientError(Exception):
    """dmclient 通信错误。"""
    pass


class DmClient:
    """管理 dmclient 子进程，处理 JSON 行协议通信。

    线程安全：使用锁保护 stdin/stdout 操作。
    """

    def __init__(self, dmclient_path: str | None = None):
        self._path = dmclient_path or _find_dmclient()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        """启动 dmclient 子进程。"""
        if self._started:
            return

        with self._lock:
            if self._started:
                return

            self._proc = subprocess.Popen(
                [self._path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._started = True

            # 读取启动时的 stderr 输出（native-image 的警告等）
            # 不做阻塞等待，由后续操作自然处理

    def stop(self) -> None:
        """停止 dmclient 子进程，关闭所有文件句柄。"""
        import warnings
        if not self._started:
            return
        with self._lock:
            if not self._started or self._proc is None:
                return
            proc = self._proc
            self._proc = None
            self._started = False

        # 在 suppress 上下文中操作，避免 Popen.__del__ 触发 ResourceWarning
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            try:
                with contextlib.suppress(Exception):
                    self._send_raw({"cmd": "quit"})
                with contextlib.suppress(Exception):
                    proc.stdin.close()
                # communicate() 会读取剩余输出并关闭所有管道
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
            finally:
                # 显式将文件句柄设为 None，避免 Popen.__del__ 警告
                try:
                    proc.stdin = None
                    proc.stdout = None
                    proc.stderr = None
                except Exception:
                    pass

    def __enter__(self) -> DmClient:
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()

    def _send_raw(self, obj: dict) -> dict:
        """发送 JSON 命令并读取响应。"""
        if not self._started or self._proc is None:
            raise DmClientError("dmclient not started")

        line = json.dumps(obj, ensure_ascii=False)
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                # 检查 stderr
                stderr_output = ""
                with contextlib.suppress(Exception):
                    stderr_output = self._proc.stderr.read()
                raise DmClientError(
                    "dmclient process terminated unexpectedly."
                    + (f" stderr: {stderr_output}" if stderr_output else "")
                )
            return json.loads(resp_line.strip())
        except (BrokenPipeError, OSError) as e:
            raise DmClientError(f"dmclient communication error: {e}") from e

    def send(self, cmd: dict) -> dict:
        """线程安全地发送命令。"""
        with self._lock:
            return self._send_raw(cmd)


# ---------------------------------------------------------------------------
# 高级 API（在 DmClient 基础上封装）
# ---------------------------------------------------------------------------

class NativeConnection:
    """原生数据库连接句柄，通过 dmclient 子进程操作。"""

    def __init__(self, client: DmClient, handle: int):
        self._client = client
        self._handle = handle
        self._closed = False

    @property
    def handle(self) -> int:
        return self._handle

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.send({"cmd": "close", "handle": self._handle})

    def execute(self, sql: str, params: list[Any] | None = None) -> NativeResult:
        cmd: dict = {"cmd": "execute", "handle": self._handle, "sql": sql}
        if params is not None:
            cmd["params"] = params
        resp = self._client.send(cmd)
        if "error" in resp:
            raise DmClientError(resp["error"])
        return NativeResult(
            client=self._client,
            result_handle=resp.get("result_handle", -1),
            stmt_handle=resp.get("stmt_handle", -1),
            update_count=resp.get("update_count", -1),
            col_count=resp.get("col_count", 0),
        )

    def execute_batch(self, sql: str, params_batch: list[list[Any]]) -> list[int]:
        cmd: dict = {
            "cmd": "execute_batch",
            "handle": self._handle,
            "sql": sql,
            "params_batch": params_batch,
        }
        resp = self._client.send(cmd)
        if "error" in resp:
            raise DmClientError(resp["error"])
        return resp.get("update_counts", [])

    def commit(self) -> None:
        resp = self._client.send({"cmd": "commit", "handle": self._handle})
        if "error" in resp:
            raise DmClientError(resp["error"])

    def rollback(self) -> None:
        resp = self._client.send({"cmd": "rollback", "handle": self._handle})
        if "error" in resp:
            raise DmClientError(resp["error"])

    def set_autocommit(self, value: bool) -> None:
        resp = self._client.send({
            "cmd": "set_autocommit",
            "handle": self._handle,
            "value": value,
        })
        if "error" in resp:
            raise DmClientError(resp["error"])


class NativeResult:
    """原生查询结果集。"""

    def __init__(self, client: DmClient, result_handle: int,
                 stmt_handle: int, update_count: int, col_count: int):
        self._client = client
        self._result_handle = result_handle
        self._stmt_handle = stmt_handle
        self._update_count = update_count
        self._col_count = col_count
        self._column_names: list[str] | None = None
        self._column_types: list[int] | None = None
        self._freed = False

    @property
    def has_result_set(self) -> bool:
        return self._result_handle > 0

    @property
    def update_count(self) -> int:
        return self._update_count

    def _load_columns(self) -> None:
        """延迟加载列信息。"""
        if self._column_names is not None:
            return
        if not self.has_result_set:
            return
        resp = self._client.send({"cmd": "columns", "handle": self._result_handle})
        if "error" in resp:
            raise DmClientError(resp["error"])
        self._column_names = resp.get("names", [])
        self._column_types = resp.get("types", [])

    @property
    def column_names(self) -> list[str]:
        self._load_columns()
        return self._column_names or []

    @property
    def column_types(self) -> list[int]:
        self._load_columns()
        return self._column_types or []

    def fetchone(self) -> list[Any] | None:
        if not self.has_result_set or self._freed:
            return None
        resp = self._client.send({"cmd": "fetch", "handle": self._result_handle})
        if "error" in resp:
            raise DmClientError(resp["error"])
        if resp.get("eof"):
            return None
        return resp.get("row", [])

    def fetchall(self) -> list[list[Any]]:
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def free(self) -> None:
        if self._freed:
            return
        self._freed = True
        if self._result_handle > 0 or self._stmt_handle > 0:
            self._client.send({
                "cmd": "free_result",
                "handle": self._result_handle,
                "stmt_handle": self._stmt_handle,
            })


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def open_native(url: str, user: str = "", password: str = "",
                client: DmClient | None = None) -> tuple[DmClient, NativeConnection]:
    """打开原生数据库连接。

    Args:
        url: JDBC URL (如 jdbc:dm://127.0.0.1:5236)
        user: 用户名
        password: 密码
        client: 可复用的 DmClient 实例

    Returns:
        (DmClient, NativeConnection) 元组
    """
    if client is None:
        client = DmClient()
        client.start()

    resp = client.send({
        "cmd": "open",
        "url": url,
        "user": user,
        "password": password,
    })
    if "error" in resp:
        raise DmClientError(resp["error"])

    return client, NativeConnection(client, resp["handle"])
