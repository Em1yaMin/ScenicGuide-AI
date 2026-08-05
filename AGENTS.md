# Project agent memory

景区导览 AI 数字人系统的项目记忆文件（唯一权威记忆源；CLAUDE.md 是指向本文件的 symlink）。语言约定：与用户交流及代码注释默认使用中文。

# Project Overview

景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台提供对话抓取与情感分析，另含纯本地推荐引擎、本地 RAG 知识库和 AI 运营分析。前端提供访客端（数字人对话）和管理后台（数据大屏 + 报告 + 知识库管理）。

# Architecture

```
digitalHumanApi/          # Python FastAPI 后端
  app/
    main.py               # 应用入口（lifespan、CORS、全局异常处理、router 注册）
    core/                 # config.py (Settings) + database.py + embedding.py (Embedding 工厂/Minimax) + rag.py (命名空间向量存储)
    data/                 # 本地 JSON 数据源（景点、路线）
    models/               # SQLAlchemy 2.0 ORM 实体 → models/CLAUDE.md
    schemas/              # Pydantic v2 请求/响应模型 → schemas/CLAUDE.md
    services/             # 业务逻辑、外部 API 客户端 → services/CLAUDE.md
    routers/              # FastAPI HTTP 端点 → routers/CLAUDE.md
    tasks/                # 后台 asyncio 循环（polling、analysis）+ 一次性补拉 CLI（backfill，详见 doc/dify-data-migration-report.md）
  tests/                  # pytest 测试套件 → tests/CLAUDE.md

digitalHumanVue/          # Vue 3 + TypeScript + Vite 前端
  src/
    api/index.ts          # 集中式端点常量
    views/                # 页面组件 → views/CLAUDE.md
    layouts/  stores/  composables/  styles/

live2d-frontend/          # ADH 本地数字人（见下方专节）
```

请求流: Vue → fetch(/api/*) → Vite proxy → FastAPI → SQLite
数据流: Dify API ← polling task → SQLite → analysis task → Dify Workflow → SQLite

## 个性化推荐（纯本地，无外部 API）

- `app/services/recommend_agent.py` — 基于 LangChain Tool Calling 模式 + 规则引擎/关键词匹配，工具读取 `app/data/` 本地 JSON 数据源推理推荐
- `app/routers/recommend.py` — `POST /api/v1/recommend`

## 本地 RAG 知识库（无外部向量库依赖）

- `app/core/embedding.py` — Embedding 选择工厂 + `MinimaxEmbeddings`（OpenAI 兼容 `/embeddings` 端点，httpx 实现，配置 `EMBEDDING_API_KEY` 启用，与对话 LLM（`LLM_*`）及运营分析（`OPENAI_*`）完全独立）；未配置 key 或 API 失败时自动降级本地 TF-IDF（日志告警，`degraded` 标记透出到检索响应）
- `app/core/rag.py` — 命名空间隔离向量存储（knowledge / documents / user_memory，各自独立索引与 `rag_data/` 持久化文件；documents 复用旧集合名 `scenic_knowledge` 兼容升级前数据）；检索阶段 numpy 矩阵乘法加速余弦相似度；兼容 langchain_core `Document`/`Embeddings` 接口；**默认文档目录 `DEFAULT_DOC_DIR` 限定到 `doc/景区知识库/`**（三份景区知识 md，git 已纳入），不索引 doc/ 下架构/设计/部署文档
- `app/services/rag_service.py` — 文档加载、分块、向量化、检索；`RAG_MAX_CONTEXT_LEN`（默认 2000）控制注入上下文长度；`search_documents`/`index_documents` 均接受 `namespace` 参数（默认检索 knowledge+documents 公共命名空间）；`search_documents` 为**统一检索入口**（对话/后台/推荐 Agent 共用），同步实现 `_search_documents_sync` 供推荐 Agent 工具调用；另有 `index_knowledge_entries` 将知识库条目（DB 行）重建到 knowledge 命名空间，供 CRUD 变更后同步
- `app/routers/rag.py` — `POST /api/rag/search`（`namespace` 参数，响应含 `namespaces`/`embedding_backend`/`degraded` 标记）、`POST /api/rag/index`（默认写入 documents 命名空间，文档源 `doc/景区知识库/`）、`GET /api/rag/status`（各命名空间状态）
- `app/routers/dialogue.py` — `POST /api/v1/dialogue/chat` 支持 RAG 增强（`use_rag`、`rag_top_k` 参数；RAG 无结果时回退正常 Dify 调用）；另有 `/api/v1/dialogue/rag-context` 供 ADH 调用
- `app/services/recommend_agent.py` — 新增 `search_knowledge` 工具（同步包装统一检索入口，供 Agent 检索讲解词/FAQ）；推荐主流程仍用结构化工具直查（景点/路线不走向量，零幻觉原则）

## 用户认证（前端登录/注册/游客模式）

后端接口（PR #14）：`POST /api/v1/auth/register|login|guest`、`GET /api/v1/auth/me`（Bearer JWT）。

- 前端：`digitalHumanVue/src/stores/auth.ts`（Pinia）+ `src/api/auth.ts` + `src/views/LoginView.vue` / `RegisterView.vue`（顶层路由 `/login` `/register`，访客端与管理后台布局均可直达）
- token 持久化：`localStorage` 的 `auth_token`（用户/游客 JWT）+ `auth_user`（用户信息 JSON）；键名沿用 snake_case 风格（`visitor_id`、`admin_api_token`）
- 游客兜底：未登录可用全部公开能力；对话页（ChatView）自动签发短期游客 token 并随 iframe `auth_token` 参数透传给 ADH（ADH 侧消费待后续任务）
- 刷新恢复：`App.vue` 启动时调 `restoreSession()` 用 `/auth/me` 校验 localStorage token，401 则清除登录态；网络失败保留本地缓存
- 错误提示：`fetchWithCodeEnvelope` 解析 FastAPI `detail`（401 "用户名或密码错误"、409 "用户名已存在"、422 校验字段）
- 无需新增前端环境变量（后端 JWT 配置见 `digitalHumanApi/.env.example`）

## 知识库管理（X-Admin-Token 认证）

- `app/models/knowledge.py` + `app/services/knowledge_service.py` + `app/routers/knowledge.py` — `/api/admin/knowledge*` CRUD + `/import`
- **CRUD/导入变更后自动同步向量索引**：增/删/改/导入后 `knowledge_service._sync_knowledge_index` 以 DB 全量条目重建 knowledge 命名空间（`index_knowledge_entries`，条目量小、全量重建开销可忽略）；索引失败仅告警日志不阻断 CRUD（下次变更/手动索引自愈）；批量导入仅最后统一同步一次
- 认证：`X-Admin-Token` 请求头，token 来自 `ADMIN_API_TOKEN` 环境变量（`app/core/config.py`）
- 前端：`digitalHumanVue/src/views/admin/KnowledgeView.vue`，token 存 `localStorage` 的 `admin_api_token`
- 导入支持 `.json` 数组和按 `# ` 标题切分的 `.md` 文件

## 用户体系（注册/登录/JWT/游客）

- 模型 `app/models/user.py`（users 表：username 唯一、password_hash、nickname、last_login_at）+ `app/core/security.py`（pwdlib[bcrypt] 哈希 + PyJWT 签发/校验）+ `app/routers/auth.py`（`/api/v1/auth/register|login|me|guest`）+ `app/services/auth_service.py`
- 密码绝不落明文/日志；`JWT_SECRET` 未配置时启动生成随机临时密钥（重启后 token 失效，生产必须设置）
- **对话接口用户标识**：`/api/v1/dialogue/chat` 优先从 `Authorization: Bearer` token 解析（登录用户→用户 id、游客→guest-xxx），无 token 回退请求体 `user`（默认 `visitor`，向后兼容）；token 无效时降级 `visitor` 并告警（不阻断对话），认证接口严格 401（决策注释见 `security.py resolve_dialogue_user`）
- 配置：`JWT_SECRET`、`JWT_EXPIRES_MINUTES`（默认 1440）、`GUEST_TOKEN_EXPIRES_MINUTES`（默认 30），见 `.env.example`

## 对话 LLM 接入层（阶段1，LangChain 工厂，未接对话链路）

- `app/core/llm.py` — ChatOpenAI（langchain-openai）工厂：`get_chat_llm()`（按 `LLM_*` 配置构造，主端点缺 key 自动降级备用端点，都缺抛 `LLMConfigError`）、`create_chat_llm()`（显式参数底层构造）、`resolve_endpoint()`；主/备用双端点：`LLM_API_*`（默认 opencode-go `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`）与 `LLM_FALLBACK_*`（默认官方 DeepSeek API），与 Embedding（`EMBEDDING_*`）、运营分析（`OPENAI_*`）配置完全独立
- **DeepSeek 思考格式兼容性**（实测结论，详见 `doc/llm-adapter-smoke-test.md`）：`reasoning_content` 被 langchain-openai 自动剥离（流式思考 chunk 为空串、content 为最终回答、不报错）→ 无需自定义处理；**max_tokens 必须经工厂 `extra_body` 注入原生 `max_tokens` 字段**（langchain-openai 1.4.1 硬编码改名为 `max_completion_tokens`，DeepSeek 系端点静默忽略致长度限制失效）；**不要在 `invoke()` 显式传 max_tokens**
- 备用通道官方 DeepSeek API 多轮对话要求历史 assistant 消息回传 `reasoning_content`（opencode-go 网关不强制）→ 对话链路任务需自定义消息转换
- 冒烟脚本：`digitalHumanApi/scripts/smoke_llm.py`（`LLM_API_KEY=... python -m scripts.smoke_llm`；`--fallback` 测备用端点）；单测 `tests/test_llm.py`（MockTransport 模拟，不发真实请求）

## 对话 agent（阶段1 T2：LangChain 主链路 + Dify 双轨）

- `app/services/chat_agent.py` — LangChain tool-calling agent（**langchain 1.x 新 API `create_agent`**，返回 langgraph 状态图，`ainvoke({"messages": [...]})`；旧的 `create_tool_calling_agent`/`AgentExecutor` 在 1.x 已移除）：结构化工具直查景点/路线（复用 `recommend_agent` 的 `search_spots`/`search_routes`/`get_spot_detail`/`get_route_detail`/`filter_by_tags`，零幻觉）、`search_knowledge`（复用统一检索入口）、`recommend_routes`（包装 `recommend_agent.recommend`，工具内 `asyncio.run` 安全——langgraph 经线程池执行同步工具）；进程内多轮会话（`conversation_id` → 消息历史 dict + 线程锁，阶段1 不持久化）；RAG 来源从返回消息中最后一条 `search_knowledge` 的 ToolMessage 提取透出
- 双轨开关：`DIALOGUE_ENGINE=langchain`（**默认**，异常自动回落 Dify，演示不翻车）`|dify`（强制原链路）；`app/routers/dialogue.py` 的 `/api/v1/dialogue/chat` 先走 `chat_agent.chat`，任何异常（含 `LLMConfigError`）回落 `_call_dify_chat`（保留原 RAG 预检索逻辑）；响应契约不变（answer/conversation_id/rag_context/rag_sources）
- 测试注意：agent 经 `ainvoke` 调用时走 openai SDK **async client**，MockTransport 需同时注入 `http_client`（httpx.Client）与 `http_async_client`（httpx.AsyncClient）两个参数（`async_client` 字段是 openai 资源对象，不可直接传）
- 单测：`tests/test_chat_agent.py`（工具调用链路/多轮上下文/RAG 来源/use_rag 开关，MockTransport 模拟 DeepSeek tool-calling 响应）、`tests/test_dialogue_engine.py`（双轨/回落/双链路失败礼貌报错）

## AI 运营分析（LangChain + OpenAI，可回退）

- `app/services/analytics_service.py` + `app/routers/analysis.py` — `GET /api/v1/analysis/overview`、`/trends`、`POST /run-now`
- 使用 `langchain_openai.ChatOpenAI` 生成分析；未配置 `OPENAI_API_KEY` 或 LLM 输出异常时自动回退纯 DB 统计（趋势、热点问题、满意度等）

# Commands

| 命令 | 位置 | 用途 |
|------|------|------|
| `uvicorn app.main:app --reload` | `digitalHumanApi/` | 启动 API 开发服务器 (端口 8000) |
| `pytest -v` | `digitalHumanApi/` | 运行测试套件 |
| `npm run dev` | `digitalHumanVue/` | 启动 Vue 开发服务器 (端口 5173) |
| `npm run build` | `digitalHumanVue/` | 生产构建 |
| `docker-compose up -d` | 根目录 | Docker Compose 部署 (api + web + nginx) |

# Business Context

- **Visitor 端**: 访客通过数字人 iframe 对话（Dify Chat Agent），系统后台轮询抓取对话内容；用户体系（注册/登录/游客）见上方「用户认证」专节，登录用户享后续长期记忆（记忆功能后续任务实现）
- **Admin 端**: 管理后台展示情感趋势、满意度、热点关注和景点热度（数据来自 Dify Workflow 量化分析），以及知识库管理（讲解词/FAQ/资料 CRUD + 批量导入）
- **数据隔离**: 轮询/量化仍仅跟踪单一虚拟访客（`DIFY_LIGHT4AI_USER`），无多租户、无按用户的数据隔离（JWT 认证仅标记对话 user，不隔离数据，见上方「用户体系」）

# 开发指南

<important if="you are adding a new domain entity end-to-end">
## 添加新领域实体（全栈）
1. 后端: 按 models/CLAUDE.md → schemas/CLAUDE.md → services/CLAUDE.md → routers/CLAUDE.md 顺序
2. 后端: 在 `main.py` 注册 router
3. 前端: 在 `api/index.ts` 添加端点常量 → 创建 View（见 views/CLAUDE.md）→ 添加路由
</important>

<important if="you are adding or modifying environment configuration">
## 环境配置
- 后端: `digitalHumanApi/.env`，通过 `app/core/config.py` 的 `Settings` Pydantic 模型加载；`digitalHumanApi/.env.example` 含全部变量与用途注释
- 前端: `digitalHumanVue/.env`，仅 `VITE_` 前缀变量在客户端可用，类型声明在 `env.d.ts`
- Docker: 通过 `docker-compose.yml` 的 `environment` 字段传入
- 凭证（Dify Key、OpenAI Key、`ADMIN_API_TOKEN`、腾讯云/MiniMax 凭证）只从 `.env` / 云平台控制台获取，绝不写入代码库；仓库内 `.env.example` 仅含占位符
</important>

<important if="you are writing or modifying tests">
## 测试
- 后端: pytest + 内存 SQLite（见 tests/CLAUDE.md），不使用第三方 mock 库（允许 pytest 内置 monkeypatch 与 httpx.MockTransport）
- 前端: 暂无测试基础设施
</important>

# ADH 本地数字人 (live2d-frontend/)

`live2d-frontend/`（下称 ADH）提供 Live2D 数字人前端 + Python 后端，替代 cloud light4ai 实现本地数字人对话。

**引擎架构**: Registry + Factory + Pool 三层，引擎通过 `@Register("Name")` 装饰器自动注册（详见 `doc/local-digital-human-deploy-plan.md`）。

## 引擎配置现状

| 引擎 | 类型 | 配置 | 状态 |
|------|------|------|------|
| **TencentAPI** | ASR | `configs/engines/asr/tencentAPI.yaml` | 引擎可用，麦克风录音链路调试中 |
| **DifyAgent** | Agent | `configs/agents/difyAgent.yaml` | 流式对话正常 |
| **MiniMaxAPI** | TTS | `configs/engines/tts/minimaxTTS.yaml` | `Chinese_sweet_girl_nv1` 音色，替代 difyTTSWorkflow |

### 凭证位置（不存真实值）
- **腾讯云 ASR**: AK/SK 来自腾讯云控制台下载的 SecretKey.csv；APPID、REGION `ap-guangzhou` 配置在 tencentAPI.yaml
- **Dify Agent**: URL `http://<dify-server-ip>/v1` + `app-` 开头的 Key + User，由 Dify 部署方提供
- **MiniMax TTS**: API Key 与 Group ID 来自 MiniMax 开放平台，Model `speech-2.8-hd`

### 启动命令
```bash
# 后端 (端口 8001，避免与主后端 8000 冲突)
cd live2d-frontend/
nohup ./venv/Scripts/python.exe main.py > backend.log 2>&1 &

# 前端 (端口 3000) — 必须绕过 pnpm dev bug
cd live2d-frontend/web/
nohup node node_modules/next/dist/bin/next dev -p 3000 > ../frontend.log 2>&1 &
```

### 关键修改文件（相对于 ADH 根目录）
- `configs/config_template.yaml` — SUPPORT_LIST 精简为仅目标引擎
- `configs/engines/asr/tencentAPI.yaml`、`configs/engines/tts/minimaxTTS.yaml`（新建）、`configs/agents/difyAgent.yaml` — 引擎凭证
- `digitalHuman/engine/tts/minimaxTTS.py`（新建）+ `digitalHuman/engine/tts/__init__.py` — MiniMax TTS 引擎及注册
- `digitalHuman/engine/asr/tencentASR.py` — 已移除 `req.AppId` 行
- `digitalHuman/agent/core/difyAgent.py` — 新增 `ThinkFilter`，流式过滤 `<think>` 标签
- `digitalHuman/server/asrApi.py` — DEBUG 音频保存到 `debug_audio/`
- `web/app/ui/home/chatbot.tsx` — 录音改用原生 `MediaRecorder` + 客户端 webm→WAV（16kHz 重采样）

### ASR 已知问题
- `tencentcloud-sdk-python-asr` v3.1.130 的 `SentenceRecognitionRequest` 无 `AppId` 字段，设置会序列化为 `PpId` 导致 `UnknownParameter` 错误 → 已移除该行
- 浏览器麦克风录音链路：旧方案 `js-audio-recorder`（ScriptProcessorNode）在新 Chrome 录音全为静音；已替换为 MediaRecorder 方案，但麦克风输入设备选择仍需调试
- `test/src/audio/zh.wav` 文件 ASR 识别正常，证明后端引擎代码正确

<important if="you are working on the ADH local digital human">
## ADH 注意事项
- **Python 环境**: 使用 ADH 的 `live2d-frontend/venv/`（Python 3.11 或更低；部署实测为 3.10，用 `virtualenv -p python3.10 venv` 创建，见 README「前置系统依赖」），不污染主项目环境
- **嵌套 git**: ADH 为独立 git 仓库，改动不影响主仓库 `git status`
- **前端启动**: 不要用 `pnpm dev`（会触发失败的依赖检查），直接用 `node node_modules/next/dist/bin/next dev -p 3000`
- **Live2D 默认模型**: 已改为 Hiyori，首次访问需清 localStorage `character-storage`
- **Agent think 过滤**: `difyAgent.py` 的 `ThinkFilter` 实时过滤 `<think>...</think>`，TTS 不会读思考内容
- **重启后端**: 改 yaml 或 Python 代码后必须重启后端；前端代码由 Next HMR 自动热更
</important>

## CI (GitHub Actions)

- Workflow: `.github/workflows/ci.yml` — backend `pytest` (Python 3.12) + frontend `npm run build` (Node 22), parallel on push to master / PR.

## Testing (pytest)

- Tests use in-memory SQLite (`tests/conftest.py` fixtures), no DATABASE_URL / .env needed.
- Gotcha: `app/core/config.py` instantiates `Settings()` at import with a validator that rejects empty Dify env vars — running pytest without `.env` or placeholder `DIFY_API_URL`/`DIFY_API_KEY`/`DIFY_WORKFLOW_API_KEY`/`DIFY_RECOMMEND_WORKFLOW_API_KEY` raises ImportError. CI injects dummy values (tests never call real APIs).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
