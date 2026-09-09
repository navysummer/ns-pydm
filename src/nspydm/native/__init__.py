"""nspydm native 后端模块。

通过 subprocess + JSON 行协议与 GraalVM native-image
编译的 dmclient 可执行文件通信，无需 JVM。
"""

from .bridge import DmClient, DmClientError, NativeConnection, NativeResult, open_native

__all__ = [
    "DmClient",
    "DmClientError",
    "NativeConnection",
    "NativeResult",
    "open_native",
]
