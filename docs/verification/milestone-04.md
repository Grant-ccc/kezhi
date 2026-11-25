# Embedding 接入准备阶段验证

日期：2026-11-25。环境沿用前阶段的 Windows、Python 3.13.9 和 SeekDB 1.4.0。

## 已实现

- 百炼原生 DashScope HTTP 适配器与独立 EmbeddingProvider 接口；首版模型 qwen3.7-text-embedding，初始 1024 维，可配置支持维度、地域及网络超时。
- 分别指定 query/document，只取稠密向量；返回片段按索引对应输入，不依赖服务商数组顺序。
- 拒绝维度不匹配、缺失或重复索引、无效数值、零向量和畸形响应。供应商用量未返回时保留未知，模型别名不冒充底层快照。
- 错误转换不回显请求正文、密钥和上游原始消息。每次调用最多 8 段，每段最多 600 字符，不截断、不自动重试、不跟随 HTTP 重定向。
- 本地配置及显式真实连通检查命令；默认不联网，--live 最多发送两段固定的自行编写文本。提供了操作指南。
- httpx 从测试依赖移至运行依赖，已有锁文件包含 httpx 0.28.1，无需更换依赖版本。模型缓存忽略规则限定为根目录 /models/，避免误忽略 app/models 源代码，Python 缓存仍忽略。

## 检查结果

1. `python -m pytest tests/test_embedding.py -q`：38 passed。全部为本地配置或 HTTP 替身测试，不使用云端密钥。
2. `python -m pytest -q`：86 passed，44.37 秒，1 条既有的 Starlette/httpx TestClient 弃用提示。原有 48 项课程、资料和 worker 测试连接本机真实 SeekDB；没有变更其测试断言。默认沙箱禁止本机套接字，完整回归使用允许本机数据库访问的执行环境。
3. `python -m pip check`：No broken requirements found。锁文件已包含新增运行依赖对应版本。
4. 默认 `python -m app.models.check_embedding`：受控返回 EMBEDDING_NOT_CONFIGURED，未发送请求。本地 .env 已追加缺失字段名和无密钥默认配置，未覆盖原有字段，Key 和空间 ID 留空。
5. 已修复从 .env 读取字符串形式维度的校验问题，并增加实际临时 dotenv 文件的回归验证。
6. `.env`、根目录模型缓存和 Python __pycache__ 均仍被 Git 忽略。没有前端改动，本阶段未重复浏览器检查和前端构建；既有浏览器验证结果见先前记录。

## 尚未验证或实现

没有百炼真实 Key 与业务空间 ID，因此没有进行真实模型调用，也没有取得真实向量、Token 用量或费用结果。没有将本地替身向量写入业务库，不把它们作为模型检索效果。

资料 worker 仍止于 PARSED，未增加向量列或发布 READY。SeekDB 真实模型检索、A/B 案例、业务权限与模型上下文的联合验证、DeepSeek 回答接入均待后续完成。输入限制当前只有字符保护，尚未引入该模型 tokenizer；批量入库前还需验证 Token 上限。

下一步由用户按 [配置指南](../embedding-setup.md)在本地填写百炼字段，再执行固定小样本真实检查。该检查只证明连通和结构，不能替代检索及问答验收。

## 后续真实连通检查：2026-11-25

用户完成本地配置后，默认检查发现空间字段包含完整北京接入域名，已仅提取其中业务空间 ID，未改变 Key。配置检查随后通过。真实命令 `python -m app.models.check_embedding --live` 正常退出，完成且仅完成两次请求，无重试：

| 文本角色 | 返回向量 | 服务商报告 Token | 调用耗时 |
| --- | --- | --- | --- |
| document | 1 个，1024 维 | 28 | 1220 ms |
| query | 1 个，1024 维 | 22 | 838 ms |

两次均通过适配器的非零、有限数值和维度校验。报告总用量为 50 Token；没有查询服务商账单，实际扣费未知。请求编号及摘要保存在 [embedding-connectivity.json](embedding-connectivity.json)，未保存密钥、业务空间 ID 或完整向量。

本次仅发送代码中固定的自行编写文本，没有发送用户上传文件，没有写入或发布业务向量，也没有调用 DeepSeek。上述“尚未验证”中的真实模型调用现已完成，SeekDB 检索、权限联合验证和回答质量仍未验证。没有变更运行代码，未重复执行已通过的 86 项测试；本次验证依据是实际 CLI 调用及返回结果。
