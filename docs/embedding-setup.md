# 配置百炼 Embedding

本项目已选择百炼向量化、SeekDB 检索、DeepSeek 回答。本步骤只配置百炼；DeepSeek 暂不需要填写密钥。

## 你需要完成的操作

1. 打开[阿里云百炼控制台](https://bailian.console.aliyun.com/)，根据控制台要求开通模型服务，选择北京地域。
2. 按[官方获取 API Key 指南](https://help.aliyun.com/zh/model-studio/get-api-key)获取该地域、业务空间下可用的 Key，并找到对应的业务空间 ID。不同地域的 Key、空间和模型可用性要匹配；不要把账号 UID 当成业务空间 ID。
3. 在仓库根目录本地 `.env` 中填写下面两行。只在本地填写真实值，不发送到聊天，不放入前端代码或 `.env.example`。

```dotenv
DASHSCOPE_API_KEY=在本地填写真实密钥
DASHSCOPE_WORKSPACE_ID=在本地填写对应业务空间ID
```

其余初始配置见 `.env.example`：地域 `cn-beijing`，模型 `qwen3.7-text-embedding`，维度 `1024`。若账号使用新加坡地域，设置 `DASHSCOPE_REGION=ap-southeast-1` 并使用匹配配置；不要仅更改地域来复用北京 Key。

`DASHSCOPE_WORKSPACE_ID` 只填写 ID 本身。若复制到的是 `某个ID.cn-beijing.maas.aliyuncs.com`，应只保留 `.cn-beijing.maas.aliyuncs.com` 之前的部分；不要填写完整域名、URL 或“默认业务空间”这个名称。

没有免费额度时可先查看控制台计费设置和余额，再决定是否进行真实检查；本项目不会自动充值。待向量化的文本会发送到百炼。

## 先检查配置，再检查连通

从仓库根目录打开 PowerShell：

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m app.models.check_embedding
Pop-Location
```

此命令不访问网络，不查询数据库，不计费。`configuration_present_not_verified` 只说明本地字段已填写，不能证明密钥有效、模型已开通或网络可达。

准备好进行真实检查后运行：

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m app.models.check_embedding --live
Pop-Location
```

这会把两段固定的自行编写文本（事务原子性说明、转账问题）分别按 document 和 query 发送，最多两次请求，无自动重试。每段均不足 600 字符，可能计费；当前北京地域模型单价为每百万输入 Token 0.5 元，[价格以百炼页面为准](https://help.aliyun.com/zh/model-studio/model-pricing)。实际用量从返回结果读取，不把字符数当作精确 Token 数或账单。

成功的 `embedding_connectivity_passed` 仅说明 API 连通、数量和维度等结构检查通过。输出含服务商请求 ID、实际返回的用量和耗时，未返回的用量为 null；不输出完整向量、密钥或业务资料。出现超时也可能已被服务商处理或计费，核对后再手动重试。

本阶段不会自动发送任何已上传文件，不会改变资料状态，也不会调用 DeepSeek。真正的验收仍需后续 SeekDB 授权检索及问答实验。
