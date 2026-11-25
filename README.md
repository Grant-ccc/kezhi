# 课程学习平台与 RAG 学习助手

数据库课程综合实验项目。通过课程、选课和资料管理形成完整业务流程，再使用 SeekDB 检索教师公共资料与学生个人笔记，生成带来源引用的课程问答。

当前阶段：资料向量发布。已实现课程与资料管理、解析 worker、分块预览及指定单份资料的百炼向量化 worker；全部向量原子发布后变为 READY。已用自编笔记验证上传、解析、向量发布及 SeekDB 授权 SQL 检索。检索 HTTP 接口、问答页面和 DeepSeek 回答尚未接通。设计文档描述完整目标范围，不表示功能已全部完成。

## 本阶段验证

- SeekDB 1.4.0：主键、唯一约束、外键、CHECK、事务回滚和并发最后名额验证通过。
- 最初的独立临时库验证使用三维合成向量，仅检查 SQL 能力；后续真实模型实验单独记录，不能混为效果成绩。
- 容器重启后专用验证记录保留，测试记录已清理。
- 114 项测试通过，新增向量任务隔离、调用审计、批次失败、原子发布回滚、SQL 写入期间租约过期、旧任务防覆盖和 READY 后删除/替换/退课的检索撤销测试。有一条既有 Starlette/httpx 弃用提示；合成向量及替身测试不代表真实模型效果。
- React 生产构建通过；浏览器中已验证学生登录、选退课、课程工作台和教师创建及编辑课程，检查了桌面与 390px 手机布局。
- 机器可读数据库验证记录在 [docs/verification](docs/verification)。完整的 22 项验收场景尚未全部执行。
- 资料页面通过浏览器检查列表、正文预览、标题修改和手机布局。自动文件选择器存在验证限制，上传、替换和下载的接口流程由测试验证，详见 [资料阶段记录](docs/verification/milestone-02.md)。
- 正文解析结果、片段数量及 Markdown 标题已在浏览器确认，详见 [解析阶段记录](docs/verification/milestone-03.md)。
- Embedding 配置、协议和检查入口的验证边界见 [接入阶段记录](docs/verification/milestone-04.md)。最初两次连通检查返回有效的 1024 维向量，报告用量共 50 Token，见 [连通检查结果](docs/verification/embedding-connectivity.json)。
- 第一条真实检索实验返回用户预先选定的 B/A，候选阶段排除其他学生的 D；13 个 SQL 可见性变体通过。只有一道自编题，未完成端到端 RAG 验收，见 [检索实验记录](docs/verification/milestone-05.md)及[评测运行方法](evals/README.md)。
- 自编笔记的真实服务链路检查通过：上传→解析→向量发布→授权 SQL 检索，两次请求报告 92 Token，专用课程、记录和文件已清理。详见 [发布阶段记录](docs/verification/milestone-06.md)。

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

目前服务默认只监听本机。课程管理及资料解析不需要模型 Key，也不会调用模型。百炼向量化配置见 [接入指南](docs/embedding-setup.md)；带 `--live` 的向量化或实验命令会调用模型并可能计费，默认检查不联网。回答模型尚未接入。

## 运行资料解析 worker

已有旧版数据库先执行迁移。新环境 bootstrap 已包含此步骤。迁移不删除业务数据，可重复执行；先增加扩展约束，再删除旧约束，避免无约束窗口。

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m app.migrate
..\.venv\Scripts\python.exe -m app.workers.documents
```

worker 使用独立终端运行，Ctrl+C 停止；中断中的任务在租约到期后由重启的 worker 接管。可用 `--once` 处理一个到期任务，`--drain` 处理至当前队列空闲，`--document-id` 限定诊断资料；`--drain` 不等待未来的重试时间。

默认租约 30 秒，每 10 秒续租，单轮最多 3 次尝试，瞬时错误指数退避，永久性文件错误直接失败；手动重试重置本轮次数并增加轮次。超时默认 120 秒，会停止续租并阻止迟到发布，但不会强制终止正在执行的 Python 解析函数。进程异常退出或数据库不可用时，恢复数据库并重启 worker 即可按租约接管；尚未配置系统级服务自动重启。

当前按段落准备最多 600 字符、约 80 字符重叠的片段，保留 PDF 页码、Markdown 标题和规范化正文字符位置。该规则不是模型 Token 限制；当前采用小批量短文本范围保护，尚未集成模型 tokenizer。解析得到的片段 `is_current=FALSE`，直到云端向量化全部成功并原子发布后才变为 TRUE。

## 运行单份资料向量化

新环境 bootstrap 已按当前配置建立向量列。旧环境先迁移，再显式选择一份自己有权处理、已解析的资料。下例 N 是待替换的资料编号：

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m app.migrate --vectors
..\.venv\Scripts\python.exe -m app.workers.vectors --live --document-id N
Pop-Location
```

命令仅处理指定资料，不扫描历史上传文件，不启动云端任务守护进程。默认最多 32 个片段、每段最多 600 字符，每批最多 8 段；超出范围在调用前失败，需拆分资料或由维护者调整 EMBEDDING_MAX_CHUNKS_PER_DOCUMENT。这个初始工程上限不是模型的实际 Token 上限，也不是累计费用硬限额。

处理成功时，当前版本全部向量、profile、is_current=TRUE、资料 READY 和任务 SUCCEEDED 在同一事务发布。失败没有部分发布，记录 FAILED 等待手动重试；页面的“重试处理”只重新排队，维护者仍需再次运行指定资料命令。批次请求前持久化 IN_FLIGHT 记录，完成后保存用量和请求 ID，不记录原文或密钥。

中断或租约失效后，可再次显式运行同一命令接管，每轮次数仍有上限。旧调用可能已计费，未完成的 IN_FLIGHT 审计代表结果未知，不能当作零费用；不要盲目重复执行。已知失败不自动重试模型。更换配置时已有向量列维度不符会拒绝运行，不自动调整或重建。

独立重验真实服务链路可运行 `python -m app.evaluation.ingestion --live`（在 backend 目录）：使用固定自编笔记和问题、最多两次请求，并清理自己新建的专用课程、记录和文件，不处理已有上传资料。无 --live 时不运行。

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

初始 Embedding 配置为 1024 维。正式 document_chunks.embedding 列已通过迁移加入；新环境 bootstrap 同样建立它，已有不匹配维度会报错而不修改原列。

## 已确定的方向

- 前端：React、TypeScript、Vite。
- 后端：Python、FastAPI。
- 数据：优先由一个 SeekDB Server 实例存储业务关系表和文档分块向量，通过 PyMySQL 执行参数化 SQL。
- 首版用户：教师、学生。演示以《数据库原理》为主，数据结构支持多门课程。
- AI：课程资料与个人笔记的权限受控 RAG，回答展示引用来源。
- 模型：用户已同意首版使用百炼 `qwen3.7-text-embedding` + SeekDB + DeepSeek；初始维度 1024，具体回答模型及实际效果仍待验证，见 [模型服务决策](docs/decisions/002-model-providers.md)。

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

向量发布已接入资料任务，授权 SQL 检索可在服务级验证。接下来开放带登录校验的检索接口及页面，并继续验证资料充分性，再接入 DeepSeek 回答模型。用户已选择资料不足时提示并建议换题或补充资料，见 [产品决策](docs/decisions/003-insufficient-evidence.md)。

## 技术资料

- SeekDB：https://github.com/oceanbase/seekdb
- Python SDK：https://oceanbase.github.io/pyseekdb/
- FastAPI：https://fastapi.tiangolo.com/
- React：https://react.dev/
