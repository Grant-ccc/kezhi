# 首版模型服务选择

日期：2026-11-25。状态：用户已同意服务组合，真实效果待验证。

## 已共同确定

- 用户选择云端 Embedding，允许小额付费 API。
- 助手推荐百炼 `qwen3.7-text-embedding`；用户询问 DeepSeek 是否可用，了解两种模型职责后，同意“百炼 Embedding + SeekDB + DeepSeek 问答”的组合。
- 百炼将接收待向量化的资料片段和问题。SeekDB 保留关系数据与向量检索职责，DeepSeek 后续根据授权检索证据生成回答。
- DeepSeek 具体模型、提示词、回答长度和评测标准仍待后续共同验证，尚未实现回答适配器。其公开文档当前未列出 Embedding 接口，因此没有把 DeepSeek 配置为向量化提供方。

## 本次实现默认值

1024 维采用百炼当前模型默认值；这是助手提出的初始工程配置，不是经过评测得出的最佳维度。原生 DashScope HTTP 接口分别指定 `query` 和 `document`，仅取稠密向量，不添加自定义任务指令，不做本地归一化。用 profile 记录模型、地域、维度和处理约定；接口名是服务商别名，服务端未返回的底层模型快照不能自行补写。

接口每批最多 8 段、每段最多 600 字符，作为首轮调用范围约束，不代表模型 Token 上限。超限拒绝，不静默截断，也不自动拆成无限批次。尚未接入模型 tokenizer，真实资料批量入库前仍需检查输入限制和费用记录。请求设置 20 秒网络操作超时，不自动重试；失败后由调用方或用户决定是否重试，不能承诺失败请求不计费。

这些默认值可在小样本实验后调整。更改模型、维度或 query/document 约定后，需要重新生成相应资料和查询向量，不能直接混用。

## 当前证据与边界

本阶段只增加模型适配层和独立检查命令。自动测试使用 HTTP 替身验证协议与错误处理，不是实际 Embedding、检索准确率或权限隔离效果证明。

配置检查默认不联网。显式 `--live` 才发送两段固定的自行编写文本，最多两个请求，无自动重试；只展示数量、维度、请求编号、服务商实际返回的 Token 数和耗时，不展示密钥或完整向量。缺少用量时标记未知，不虚构零费用。失败时保留已完成请求的摘要，服务商账单仍是计费依据。

现有资料 worker 未调用此接口，PARSED 状态不变。没有增加正式向量列、发布 READY 分块、实现 SeekDB 真实向量检索或接入 DeepSeek。下一阶段在配置就绪后检查真实向量，并由用户共同查看 A/B 案例结果。

同日后续验证：用户配置完成，两次固定短文本真实调用通过，均返回 1024 维向量，报告总用量 50 Token。详见 [连通检查结果](../verification/embedding-connectivity.json)。这项结果只确认接口与向量结构，A/B 检索实验仍待执行。

之后的首轮 A/B 实验在独立临时库中实际返回 B/A，D 在候选阶段排除，详见 [检索实验记录](../verification/milestone-05.md)。这更新了前述待验证状态，但样本只有一道自编题，不能据此宣称模型整体效果最优；正式资料向量发布和 DeepSeek 回答仍未实现。

## 官方依据

- [百炼向量化指南](https://help.aliyun.com/zh/model-studio/embedding)：模型、维度及 query/document 角色说明。
- [百炼同步 HTTP 接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)：业务空间域名、请求参数和返回结构。
- [百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)：北京地域本次查阅时为每百万输入 Token 0.5 元；价格和免费额度以实际账号与服务商页面为准。
- [DeepSeek 模型列表](https://api-docs.deepseek.com/api/list-models/)及[模型说明](https://api-docs.deepseek.com/quick_start/pricing/)：后续回答接口选择的依据，不代表已经完成接入。
