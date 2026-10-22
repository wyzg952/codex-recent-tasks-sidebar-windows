# Codex 最近任务栏（Windows）

Windows 11 本地伴生工具：显示最近 2 天、7 天或 30 天的 Codex 任务、最近活动时间、运行状态和剩余用量，并支持精确跳转、左右吸附与独立置顶。

## 环境要求

- Windows 11
- Codex Desktop
- Python 3.12 或更高版本（安装时启用 `Add python.exe to PATH`）
- PowerShell 7

本项目只使用 Python 标准库，不需要安装额外的 Python 包。

## 从源码运行

使用 Git：

```powershell
git clone https://github.com/wyzg952/codex-recent-tasks-sidebar-windows.git
cd .\codex-recent-tasks-sidebar-windows
pwsh -File .\scripts\start.ps1
```

也可以在 GitHub 页面选择 `Code → Download ZIP`，解压后双击：


```text
启动 Codex 最近任务.cmd
```

## 随 Codex 自动启动

安装一次登录启动监视器：

```powershell
pwsh -File .\scripts\install-autostart.ps1
```

监视器本身没有窗口。检测到 Codex 桌面窗口后会自动打开侧栏；Codex 最小化、关闭到托盘或退出时，侧栏完全隐藏并驻留右下角系统托盘；恢复 Codex 后侧栏自动出现且不抢焦点。吸附和单独置顶模式均遵循此规则。侧栏始终不占下方任务栏位置，隐藏时也不会重复启动。托盘图标可能位于 Windows 的折叠区域。

多窗口时跟随最近操作的 Codex 窗口，该窗口最小化或隐藏后，其他后台窗口不会让侧栏留在桌面。侧栏右上角「×」只收进托盘，不退出；双击托盘图标可在 Codex 可见时恢复，也可以将绑定的 Codex 窗口最小化后再恢复，让侧栏重新出现。

如需取消：

```powershell
pwsh -File .\scripts\uninstall-autostart.ps1
```

## 功能

- 只读加载 `~/.codex/state_5.sqlite`、`session_index.jsonl`、全局未读状态和 rollout 状态。
- 查看范围可在 `2天 / 7天 / 30天` 间切换，默认 7 天并记住选择。
- 排除归档任务、真实子线程和内部智能体记录。
- 已删除或改名后不再存在的项目目录会从侧栏隐藏；不会删除 Codex 历史，目录恢复后会自动重新显示。
- 同一 Windows 路径下的任务会忽略扩展前缀、分隔符和大小写差异，合并到一个项目组；点击项目标题可以折叠或展开，并记住选择。搜索时匹配项目会临时展开。
- 列表同时跟踪内容更新时间和 Codex 最近打开时间；最新项目以及出现新活动或待关注状态的项目会自动展开，完整任务列表每 30 秒静默同步一次。
- 状态优先级：待操作 → 运行中 → 待查看 → 普通时间。
- 绿色“待查看”跟随 Codex 的 unread 数据；从侧栏点开后会按任务及其更新时间记住已查看，新回复到达后才会再次提示。
- 点击任务通过唯一 Thread ID 打开对应 `codex://threads/{id}`。
- 吸附在 Codex 左侧或右侧，并随 Codex 前后台切换层级。
- 吸附模式下可拖动右下角缩放手柄自由调整宽度和高度，尺寸会被记住。
- 与 Codex 窗口零缝贴合；当所选一侧空间不足时自动换到可用侧，并同步更新界面选中状态。
- 可切换为独立置顶窗口并自由移动。
- 剩余用量只通过 Codex 官方 `app-server` 读取；失败不影响任务列表。

## 验证

```powershell
pwsh -File .\scripts\qa.ps1
```

自动测试使用固定脱敏夹具。真实环境自检只输出任务数量和用量周期数量，并对数据源执行前后 SHA-256 比对。

## 安全边界

- 不修改 Codex 数据库、任务索引、rollout、未读状态或安装目录。
- 不读取或打印任务正文、Token 和账号原始响应。
- 不上传任务数据。
- 侧栏自己的本地设置只保存窗口偏好、折叠项目路径，以及已查看任务的 ID 和对应更新时间。

## ChatGPT 会话边界

当前版本只显示 Codex 任务。ChatGPT 普通对话没有公开的本地会话索引或官方“列出最近对话”接口；本工具不会读取浏览器 Cookie、调用未公开私有接口或抓取聊天正文来伪造支持。

## 开源许可

本项目采用 [MIT License](./LICENSE)。你可以使用、复制、修改、重新发布或打包成自己的程序，但需要在副本或主要衍生部分中保留原版权声明和许可证文本。
