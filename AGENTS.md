# AGENTS.md

## 项目目标

为 Windows 11 上的 Codex Desktop 提供只读的最近任务侧边栏，显示任务最近活动时间与运行、待操作、待查看状态，并支持精确跳转、左右吸附和独立置顶。

## 技术栈

- Python 3.12+
- 标准库优先：`tkinter`、`sqlite3`、`json`、`subprocess`、`ctypes`
- PowerShell 7 用于安装、启动与验收脚本
- 不新增全局依赖；确需第三方依赖时先说明并获得用户同意

## 目录与命名

- `src/codex_sidebar/`：应用源码，模块名使用英文 `snake_case`
- `tests/`：固定夹具和自动测试，不得引用真实用户任务数据
- `scripts/`：PowerShell 7 构建、启动和验收脚本
- `assets/`：图标及静态资源
- `build/`：本地构建产物，不提交

## 启动、测试与构建

- 启动：`pwsh -File ./scripts/start.ps1`
- 测试：`python -m unittest discover -s tests -v`
- 完整验收：`pwsh -File ./scripts/qa.ps1`

## 完成标准

- 只读加载 `state_5.sqlite`、`session_index.jsonl`、`.codex-global-state.json` 和任务 rollout 文件
- 排除归档任务、真实子线程和内部记录，并按工作目录分组、按活动时间倒序
- 状态优先级固定为：待操作 → 运行中 → 待查看 → 普通时间
- 点击任务通过唯一 Thread ID 打开 `codex://threads/{id}`
- 支持左吸附、右吸附、跟随 Codex 前后台及独立置顶
- 登录后由无界面监视器驻留；Codex 窗口出现时自动启动且不重复，Codex 完全退出时正常关闭侧栏
- 数据解析不阻塞 UI，长任务列表可平滑滚动
- 自动测试、固定脱敏夹具测试和真实界面检查通过

## 安全限制

- Codex 数据源必须只读；禁止迁移、替换、写入、压缩或上传
- 不打印、提交或上传真实任务标题、Thread ID、正文、用户名路径、Token 或账号响应
- 不读取、打印或持久化认证文件和 Token
- 用量信息只能调用 Codex 官方 `app-server`，失败不得影响任务列表
- 不修改 Codex 安装目录、注册表、数据库结构或未读状态
- 未经授权不得安装全局依赖、修改系统设置、提交或推送
