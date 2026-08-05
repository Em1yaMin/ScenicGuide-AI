# Dify 配置总览

> 景区导览 AI 数字人系统 — Dify 平台完整配置指南。

---

## 一、组件关系

系统在 Dify 上需要创建 **2 个应用 + 1 个知识库**：

```
┌─────────────────────────────────────────────────────────┐
│  Dify 平台 (Docker 部署)                                   │
│                                                           │
│  ┌─────────────────────┐    ┌────────────────────────┐   │
│  │ 知识库               │    │ 工作流应用              │   │
│  │ (景区文档资料)        │    │ 量化评估工作流           │   │
│  │                      │    │ DIFY_WORKFLOW_API_KEY  │   │
│  └─────────┬───────────┘    └───────────┬────────────┘   │
│            │ 关联                        │                │
│            ▼                             │                │
│  ┌─────────────────────┐                │                │
│  │ 聊天助手应用          │                │                │
│  │ (景区导览数字人)       │                │                │
│  │ DIFY_API_KEY         │                │                │
│  └─────────┬───────────┘                │                │
│            │                             │                │
└────────────┼─────────────────────────────┼────────────────┘
             │                             │
             ▼                             ▼
   ┌──────────────────┐       ┌──────────────────────┐
   │ 数字人前端(ADH)    │       │ FastAPI 后端          │
   │ LangChainAgent    │       │ polling + analysis   │
   │ 对话交互 → 游客     │       │ 数据抓取 → 大屏/报告  │
   └──────────────────┘       └──────────────────────┘
```

> ADH 默认引擎为 LangChainAgent（走本后端 `/api/v1/dialogue/chat`，后端异常自动回落聊天助手）；DifyAgent 直连保留可切换。聊天助手 API Key 现主要供后端回落与轮询链路使用。

### 组件清单

| 组件 | Dify 应用类型 | API Key 变量 | 调用方 | 用途 |
|------|-------------|-------------|--------|------|
| **聊天助手** | Chatbot | `DIFY_API_KEY` | 本后端（LangChain 回落）／ADH（切回 DifyAgent 时） | 景区导览实时对话 |
| **知识库** | Knowledge | — | 聊天助手（关联） | 提供景区事实信息 |
| **量化工作流** | Workflow | `DIFY_WORKFLOW_API_KEY` | FastAPI 后端 | 对话质量量化分析 |

---

## 二、.env 配置映射

后端 `.env` 文件（`digitalHumanApi/.env`）中 Dify 相关配置：

```bash
# Dify 平台地址（Docker 部署的服务器）
DIFY_API_URL=http://YOUR_DIFY_SERVER_IP/v1

# 聊天助手 API Key（Chat Agent，供后端回落与轮询链路；DifyAgent 直连时也使用）
# 当前值沿用 ADH difyAgent.yaml，如重建聊天助手应用则需更新
DIFY_API_KEY=app-YOUR_CHAT_AGENT_KEY

# 量化工作流 API Key（Workflow，用于对话分析）
# 量化工作流 API Key（2026-07-15 新建）
DIFY_WORKFLOW_API_KEY=app-YOUR_WORKFLOW_KEY

# 轮询用户列表（逗号分隔，每个用户都会拉取对话）
DIFY_LIGHT4AI_USER=YOUR_DIFY_USER,Admin
```

> ⚠️ 当 ADH 切回 DifyAgent 直连时，`DIFY_LIGHT4AI_USER` 必须与 ADH 项目 `configs/agents/difyAgent.yaml` 中的 `DIFY_API_USER` 一致（默认 LangChainAgent 走本后端，对话不经过 Dify，无需匹配）。

### live2d-frontend（ADH）配置

默认引擎为 LangChainAgent（`configs/agents/langchainAgent.yaml`，`LLM_API_URL` 指向本后端，无需 Dify 凭据）；以下 `difyAgent.yaml` 仅切回直连时生效：

```yaml
# 当前生效配置（路径：configs/agents/difyAgent.yaml）
PARAMETERS:
  - NAME: "DIFY_API_URL"
    DEFAULT: "http://YOUR_DIFY_SERVER_IP/v1"
  - NAME: "DIFY_API_KEY"
    DEFAULT: "app-YOUR_CHAT_AGENT_KEY"  # 聊天助手应用 Key
  - NAME: "DIFY_API_USER"
    DEFAULT: "YOUR_DIFY_USER"                        # 与 .env DIFY_LIGHT4AI_USER 一致
```

---

## 三、部署顺序

按依赖关系，按以下顺序完成 Dify 配置：

### Step 1: 准备知识库源文档

按照 [知识库配置说明](./knowledge-base-知识库.md) 准备 Markdown 文档：

1. 收集景区资料（景点、路线、票务、设施等）
2. 按模板拆分文档（见 2.1 节推荐目录结构）
3. 编写「景点 × 年龄段」推荐矩阵
4. 准备 50+ 条测试问题

### Step 2: 创建知识库

在 Dify 平台：

1. 进入「知识库」→「创建知识库」
2. 上传准备好的 Markdown 文档
3. 设置分段参数（见知识库文档 3.1 节）
4. 等待索引完成

### Step 3: 创建聊天助手应用

见 [聊天助手提示词设计](./chat-agent-提示词.md)：

1. 创建「聊天助手」类型应用
2. 填入系统提示词（见 2.1 节）
3. 定义变量 `SCENIC_NAME`
4. 关联知识库（Step 2 创建的知识库）
5. 配置模型参数（见 2.3 节）
6. 用测试用例验证（见 4.1-4.4 节）
7. 发布 → 获取 API Key → 填入 `.env` 的 `DIFY_API_KEY`

### Step 4: 创建量化评估工作流

见 [量化评估工作流设计](./quantitative-workflow-量化工作流.md)：

1. 创建「工作流」类型应用
2. 添加 Start → LLM → End 节点（见 2.2 节）
3. 填入 LLM 节点提示词（见 3.1 节）
4. 配置 End 节点输出变量
5. 用测试用例验证（见 4.1-4.5 节）
6. 发布 → 获取 API Key → 填入 `.env` 的 `DIFY_WORKFLOW_API_KEY`

### Step 5: 端到端验证

1. 启动数字人前端（ADH）→ 与数字人对话
2. 等待 30 秒 → 检查 FastAPI 日志是否有轮询新消息
3. 等待 5 分钟（或手动触发 `/api/v1/analysis/run-now`）→ 检查是否评估成功
4. 访问管理后台数据大屏 → 确认数据展示正常

---

## 四、数据流端到端验证

### 4.1 对话抓取验证

```bash
# 查看轮询日志
tail -f digitalHumanApi/backend.log | grep "Polled"

# 预期输出（每 30 秒）：
# Polled 2 new messages from 1 conversations (user=YOUR_DIFY_USER)
```

### 4.2 量化评估验证

```bash
# 手动触发一次评估
curl -X POST http://localhost:8000/api/v1/analysis/run-now

# 预期响应：
# {"evaluated_count": 1}

# 查看评估日志
tail -f digitalHumanApi/backend.log | grep "Evaluated"

# 预期输出：
# Evaluated 1 dialogues in batch
```

### 4.3 大屏数据验证

```bash
# 查询数据大屏 API
curl http://localhost:8000/api/v1/admin/dashboard

# 预期响应包含：
# sentiment_trend, satisfaction_distribution, hot_issues, spot_heatmap
```

---

## 五、故障排查

| 现象 | 可能原因 | 排障步骤 |
|------|----------|----------|
| 数字人回答质量差 | 知识库内容不足或提示词未生效 | 1. 检查知识库是否关联到聊天助手 2. 在 Dify 预览中测试 3. 检查分段是否合理 |
| 数字人回答空白 | 对话链路异常 | 1. 检查本后端 `/api/v1/dialogue/chat`（LangChainAgent 的 `LLM_API_URL`）2. 若切回 DifyAgent 直连，检查 `difyAgent.yaml` 的 API Key 3. 检查 Dify 日志 |
| 对话没被抓取 | 轮询用户与对话 user 不一致 | 1. 检查 `.env` 中 `DIFY_LIGHT4AI_USER` 2. 检查 ADH 引擎 user 配置（LangChainAgent `LLM_API_USER`／DifyAgent `DIFY_API_USER`）是否一致 3. 用 Dify API 直接测 `/conversations?user=xxx` |
| 工作流评估失败 | 输出格式不正确 | 1. 在 Dify 工作流中手动测试 2. 检查 LLM 输出是否为合法 JSON 3. 检查 LLM 温度是否过高导致格式不稳定 |
| 大屏无数据 | evaluations 表为空 | 1. 确认 analysis_loop 正在运行 2. 检查 `DIFY_WORKFLOW_API_KEY` 是否正确 3. 手动触发 `/api/v1/analysis/run-now` |
| 评分异常（全满分/全低分） | 提示词约束过强或过弱 | 1. 用测试用例 4.1-4.5 在 Dify 工作流中逐一验证 2. 调整评分标准描述 3. 考虑提高 Temperature 增加评分多样性 |

---

## 六、相关文档

| 文档 | 路径 |
|------|------|
| 架构分析文档 | `doc/architecture-analysis.md` |
| Dify 聊天助手提示词 | `doc/dify配置/chat-agent-提示词.md` |
| Dify 量化工作流设计 | `doc/dify配置/quantitative-workflow-量化工作流.md` |
| Dify 知识库配置 | `doc/dify配置/knowledge-base-知识库.md` |
| 数字人部署研究 | `.rpiv/artifacts/research/2026-07-13_19-05-29_local-digital-human-deploy.md` |
