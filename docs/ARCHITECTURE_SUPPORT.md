# dmclient 架构支持

`dmclient` 原生可执行文件（macOS 后端）由独立项目维护，包含多架构预编译二进制和 GraalVM native-image 编译说明：

👉 **https://github.com/navysummer/dm-client/releases**

## 环境变量

| 变量名 | 说明 |
|-------|------|
| `DMCLIENT_PATH` | 指定 dmclient 可执行文件的完整路径 |

## 自动架构检测

Python 端会自动检测当前架构并加载对应的 `dmclient` 二进制文件：

```python
from nspydm.native import DmClient
# 自动检测架构，无需手动指定
client = DmClient()
client.start()
```

检测逻辑：
1. 查找 `dmclient-{os}-{arch}`（架构特定文件）
2. 如果找不到，查找通用名 `dmclient`
3. 如果还找不到，报错并提示用户前往 https://github.com/navysummer/dm-client/releases 获取二进制或编译

## 支持的架构

| 操作系统 | 架构 | 二进制文件名 |
|---------|------|-------------|
| macOS | ARM64 (M1/M2/M3/M4) | `dmclient-macos-arm64` |
| macOS | x64 (Intel) | `dmclient-macos-x64` |
| Linux | x64 | `dmclient-linux-x64` |
| Linux | ARM64 (aarch64) | `dmclient-linux-arm64` |
| Windows | x64 | `dmclient-windows-x64.exe` |
