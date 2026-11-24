# 课程学习平台与 RAG 学习助手

数据库课程综合实验项目。通过课程、选课和资料管理形成完整业务流程，再使用 SeekDB 检索教师公共资料与学生个人笔记，生成带来源引用的课程问答。

当前阶段：资料解析版。已实现课程与资料管理、持久化解析 worker、正文分块预览、租约续期、过期任务接管、有限重试和手动重试。向量化及 RAG 问答尚未实现，模型仍待选型。设计文档描述完整目标范围，不表示功能已全部完成。

## 本阶段验证

- SeekDB 1.4.0：主键、唯一约束、外键、CHECK、事务回滚和并发最后名额验证通过。
- 使用独立临时库中的三维合成向量验证参数化检索、私人片段过滤和删除可见性，尚未验证真实 Embedding 检索效果。
- 容器重启后专用验证记录保留，测试记录已清理。
- 48 项测试通过，包含课程与资料流程、worker 并发领取、租约续期、新进程接管、过期任务防覆盖、分块发布回滚和处理超时；测试依赖有一条 Starlette/httpx 弃用提示，不影响本次结果。
- React 生产构建通过；浏览器中已验证学生登录、选退课、课程工作台和教师创建及编辑课程，检查了桌面与 390px 手机布局。
- 机器可读数据库验证记录在 [docs/verification](docs/verification)。完整的 22 项验收场景尚未全部执行。
- 资料页面通过浏览器检查列表、正文预览、标题修改和手机布局。自动文件选择器存在验证限制，上传、替换和下载的接口流程由测试验证，详见 [资料阶段记录](docs/verification/milestone-02.md)。
- 正文解析结果、片段数量及 Markdown 标题已在浏览器确认，详见 [解析阶段记录](docs/verification/milestone-03.md)。

## 使用资料管理

学生先选课，再进入课程工作台上传私人笔记；教师仅能在自己负责课程上传公共资料。“资料与笔记”页面查看本人上传记录，可搜索、下载、改名、替换或删除。

- 支持文本型 PDF、UTF-8 TXT 和 Markdown，默认每份最多 10 MiB；PDF 最多 200 页、单页解压内容流最多 2 MiB、正文最多 100 万字符。加密 PDF、空文件、扫描件和伪造格式会被拒绝。
- 文本预览按纯文本展示，不执行 Markdown 中的 HTML；PDF 当前仅支持下载查看。
- 退课后仍可查看、下载、改名和删除本人已有笔记，但不能新增或替换文件，也不能访问教师资料。
- 上传在同一事务写入资料与持久化任务；启动 worker 后解析正文与分块，显示“已解析 · 待向量化”。处理中的列表每 3 秒自动刷新，失败后可在有修改权限时重试。PARSED 不等于可检索。
- 替换正文递增版本并作废旧任务和当前分块；逻辑删除立刻撤销下载与列表访问。旧文件暂保留在受控目录，物理清理和留存周期将在后续阶段完善。
- 文件位于忽略的 `storage/uploads/`，不通过静态目录公开。配置可用 `STORAGE_DIR` 与 `MAX_UPLOAD_BYTES` 覆盖；界面按默认 10 MiB 限制提示。

## 本地运行

已验证环境为 Windows、Python 3.13.9、Node.js 22.21.0、Docker Desktop。后端要求 Python 3.12 及以上；其他环境需自行执行验证。所有命令从仓库根目录运行，启动前先打开 Docker Desktop。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
.\.venv\Scripts\python.exe scripts/setup_env.py
docker compose up -d seekdb
```

等待 SeekDB 接受连接后初始化业务表与演示数据。初始化命令可重复执行，已有数据和密码会保留，并执行当前已登记的状态约束迁移；它不是通用自动迁移工具。

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m app.bootstrap
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个终端，从仓库根目录启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

浏览器打开 http://127.0.0.1:5173 。前端 `/api` 代理至本机 8000 端口。结束使用时在两个服务终端分别按 Ctrl+C；`docker compose stop` 可停止数据库，命名卷仍保留数据。

本地演示账号为 `teacher1`、`teacher2`、`student1`、`student2`、`student3`。初次初始化使用 `.env` 中的 `DEMO_PASSWORD`，示例默认值为 `LearnDemo2026!`，仅用于本地演示。`setup_env.py` 会生成随机数据库密码及 JWT 密钥并写入忽略的 `.env`，不会覆盖已有配置。不要将演示配置直接用于公网部署。

目前服务默认只监听本机。回答模型尚未接入，不需要模型 API Key，也不会产生模型调用费用。

## 运行资料解析 worker

已有旧版数据库先执行迁移。新环境 bootstrap 已包含此步骤。迁移不删除业务数据，可重复执行；先增加扩展约束，再删除旧约束，避免无约束窗口。

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m app.migrate
..\.venv\Scripts\python.exe -m app.workers.documents
```

worker 使用独立终端运行，Ctrl+C 停止；中断中的任务在租约到期后由重启的 worker 接管。可用 `--once` 处理一个到期任务，`--drain` 处理至当前队列空闲，`--document-id` 限定诊断资料；`--drain` 不等待未来的重试时间。

默认租约 30 秒，每 10 秒续租，单轮最多 3 次尝试，瞬时错误指数退避，永久性文件错误直接失败；手动重试重置本轮次数并增加轮次。超时默认 120 秒，会停止续租并阻止迟到发布，但不会强制终止正在执行的 Python 解析函数。进程异常退出或数据库不可用时，恢复数据库并重启 worker 即可按租约接管；尚未配置系统级服务自动重启。

当前按段落准备最多 600 字符、约 80 字符重叠的片段，保留 PDF 页码、Markdown 标题和规范化正文字符位置。该规则不是模型 Token 限制；确定 Embedding 后还需根据 tokenizer 检查或重新分块。当前片段 `is_current=FALSE`、无向量，不参与智能检索。

## 重复验证

数据库运行且完成初始化后：

```powershell
.\.venv\Scripts\python.exe scripts/verify_seekdb.py
Push-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Pop-Location
Push-Location frontend
npm run build
Pop-Location
```

`verify_seekdb.py` 只创建和删除自己生成的临时验证库。集成测试在项目库内创建临时课程，结束时清理自己的测试记录；不应指向生产数据库。

Embedding 模型维度确定后才执行 `sql/003_vectors.sql.template` 对应迁移；当前业务表没有正式的 embedding 列，以免提前锁定模型。

## 已确定的方向

- 前端：React、TypeScript、Vite。
- 后端：Python、FastAPI。
- 数据：优先由一个 SeekDB Server 实例存储业务关系表和文档分块向量，通过 PyMySQL 执行参数化 SQL。
- 首版用户：教师、学生。演示以《数据库原理》为主，数据结构支持多门课程。
- AI：课程资料与个人笔记的权限受控 RAG，回答展示引用来源。
- 模型：允许小额付费 API；回答模型供应商、具体版本及 Embedding 模型均在 RAG 阶段通过验证再确定。

## 设计入口

| 文档 | 内容 |
| --- | --- |
| [产品需求](docs/product.md) | 用户、范围、权限、页面、交互与产品指标 |
| [数据库设计](docs/database.md) | ER 图、关系模式、约束、索引和事务 |
| [技术架构](docs/architecture.md) | 分层、任务处理、检索权限、模型接口及待验证项 |
| [验收与推进计划](docs/acceptance.md) | 实验要求映射、测试案例和开发顺序 |
| [实验要求原文](docs/experiment-requirements.txt) | 用户提供的实验要求，保留原文便于逐项核对 |

这些文件属于本项目的开发文档；最终实验报告仍需使用教师提供的格式模板。

## 下一步

确定 Embedding 方案、校验模型 Token 限制并建立真实向量检索，再基于评测选择回答模型。模型供应商仍不提前锁定。

## 技术资料

- SeekDB：https://github.com/oceanbase/seekdb
- Python SDK：https://oceanbase.github.io/pyseekdb/
- FastAPI：https://fastapi.tiangolo.com/
- React：https://react.dev/
