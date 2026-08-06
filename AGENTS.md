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

- `app/core/embedding.py` — Embedding 选择工厂 + `MinimaxEmbeddings`（**原生** `/embeddings` 端点，httpx 实现，配置 `EMBEDDING_API_KEY` 启用，与对话 LLM（`LLM_*`）及运营分析（`OPENAI_*`）完全独立）；**原生 API 格式（实测 embo-01，2026-08-06 联调修正）**：请求体 `{"model", "texts", "type"}`（`embed_documents` 传 `type=db`、`embed_query` 传 `type=query`，两者向量空间不同混用会降低检索质量），响应 `{"vectors": [...]}`；响应解析兼容 OpenAI 兼容格式（`data` 数组）便于未来切换；**先检查 `base_resp.status_code` 业务错误**（如 1008 insufficient balance，HTTP 200 但 vectors 为 null，真实环境实测 2026-08-06）——先行明确诊断，不误报"响应条数不符"；未配置 key 或 API 失败时自动降级本地 TF-IDF（日志告警，`degraded` 标记透出到检索响应）
- `app/core/rag.py` — 命名空间隔离向量存储（knowledge / documents / user_memory，各自独立索引与 `rag_data/` 持久化文件；documents 复用旧集合名 `scenic_knowledge` 兼容升级前数据）；检索阶段 numpy 矩阵乘法加速余弦相似度；兼容 langchain_core `Document`/`Embeddings` 接口；**默认文档目录 `DEFAULT_DOC_DIR` 限定到 `doc/景区知识库/`**（三份景区知识 md，git 已纳入），不索引 doc/ 下架构/设计/部署文档；**已知现象：`SimpleVectorStore._load` 每次启动会全量重向量化（向量不落盘），3769 片段约 2 分钟，属已知优化项**
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

## 对话 LLM 接入层（阶段1，LangChain 工厂）

- `app/core/llm.py` — ChatOpenAI（langchain-openai）工厂：`get_chat_llm()`（按 `LLM_*` 配置构造，主端点缺 key 自动降级备用端点，都缺抛 `LLMConfigError`）、`create_chat_llm()`（显式参数底层构造）、`resolve_endpoint()`；主/备用双端点：`LLM_API_*`（默认 opencode-go `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`）与 `LLM_FALLBACK_*`（**代码兜底默认仍为官方 DeepSeek API `https://api.deepseek.com/v1`；部署配置按船长决策 2026-08-06 备用同主端点 opencode-go**——opencode 服务本质转发 DeepSeek 官方 key，无需另配官方备用），与 Embedding（`EMBEDDING_*`）、运营分析（`OPENAI_*`）配置完全独立
- **DeepSeek 思考格式兼容性**（实测结论，详见 `doc/llm-adapter-smoke-test.md`）：`reasoning_content` 被 langchain-openai 自动剥离（流式思考 chunk 为空串、content 为最终回答、不报错）→ 无需自定义处理；**max_tokens 必须经工厂 `extra_body` 注入原生 `max_tokens` 字段**（langchain-openai 1.4.1 硬编码改名为 `max_completion_tokens`，DeepSeek 系端点静默忽略致长度限制失效）；**不要在 `invoke()` 显式传 max_tokens**
- 备用通道官方 DeepSeek API 多轮对话要求历史 assistant 消息回传 `reasoning_content`（opencode-go 网关不强制）；对话链路（chat_agent）尚未实现该自定义转换，备用端点多轮对话受限（**仅当部署改配官方 DeepSeek 备用时适用，见 doc/llm-adapter-smoke-test.md §3；部署默认备用同主端点 opencode-go，不受影响**）
- 冒烟脚本：`digitalHumanApi/scripts/smoke_llm.py`（`LLM_API_KEY=... python -m scripts.smoke_llm`；`--fallback` 测备用端点）；单测 `tests/test_llm.py`（MockTransport 模拟，不发真实请求）

## 对话 agent（阶段1 T2+T3：LangChain 主链路 + Dify 双轨 + SSE 流式）

- `app/services/chat_agent.py` — LangChain tool-calling agent（**langchain 1.x 新 API `create_agent`**，返回 langgraph 状态图，`ainvoke({"messages": [...]})`；旧的 `create_tool_calling_agent`/`AgentExecutor` 在 1.x 已移除）：结构化工具直查景点/路线（复用 `recommend_agent` 的 `search_spots`/`search_routes`/`get_spot_detail`/`get_route_detail`/`filter_by_tags`，零幻觉）、`search_knowledge`（复用统一检索入口）、`recommend_routes`（包装 `recommend_agent.recommend`，工具内 `asyncio.run` 安全——langgraph 经线程池执行同步工具）；多轮上下文：**阶段2 T1 起短期记忆持久化**（见下方「短期记忆持久化」专节，进程内 TTL 缓存 + SQLite 双写，重启可恢复）；RAG 来源从返回消息中最后一条 `search_knowledge` 的 ToolMessage 提取透出
- 双轨开关：`DIALOGUE_ENGINE=langchain`（**默认**，异常自动回落 Dify，演示不翻车）`|dify`（强制原链路）；`app/routers/dialogue.py` 的 `/api/v1/dialogue/chat` 先走 `chat_agent.chat`，任何异常（含 `LLMConfigError`）回落 `_call_dify_chat`（保留原 RAG 预检索逻辑）；回落时经 `has_conversation` 判定，不把 LangChain 生成的进程内会话 ID 透传给 Dify（未知 id 会 400 conversation_not_exist），改传空串让 Dify 新建会话；响应契约不变（answer/conversation_id/rag_context/rag_sources）

## 短期记忆持久化（阶段2 T1，船长决策：进程内 TTL 缓存 + SQLite，暂不上 Redis）

- `app/core/memory_store.py` — 会话历史存储层：**标准库 sqlite3**（无新增依赖），单表 `conversation_sessions`（conversation_id PK / user / source / messages JSON 消息序列 / created_at / last_active_at / ttl_expires_at），独立文件（默认 `memory_store.db`，不动主库 digital_human.db）；单连接 + 线程锁；`source`（`langchain`=本模块生成 uuid / `client`=客户端传入）持久化归属判定，重启恢复时据此重建 `_generated_conversation_ids`（双轨回落兼容）；TTL 滑动窗口（每次追加刷新，配置 `MEMORY_TTL_DAYS` 默认 7 天）；访问/追加时惰性删除过期行
- 读写策略 = **读走缓存 + 写双写**：`_conversations` 热缓存不变（`_MAX_CONVERSATIONS` LRU、`_MAX_HISTORY_MESSAGES` 上限语义不变），每次追加即时落盘（崩溃无丢失窗口）；缓存未命中时从 SQLite **懒恢复**（重启后同 `conversation_id` 续聊不丢多轮上下文）；LRU 淘汰只出缓存、不出库（`_evict_locked` 注释）；SQLite 写失败仅告警降级进程内记忆，不阻断对话（`_store_call`）
- `app/tasks/memory_cleanup.py` — 后台清理任务（analysis_loop 模式）：启动立即清理一次 + 每 `MEMORY_CLEANUP_INTERVAL_SECONDS`（默认 3600s）定期删 SQLite 过期行 + 驱逐进程内过期缓存（`chat_agent.evict_expired_conversations`）；main.py lifespan 注册
- 配置：`MEMORY_DB_PATH`/`MEMORY_TTL_DAYS`/`MEMORY_CLEANUP_INTERVAL_SECONDS`，见 `.env.example`；测试：`tests/test_memory_store.py`（存储层/清理任务）+ `test_chat_agent.py` 短期记忆持久化节（重启恢复/LRU 不丢库/TTL 过期不恢复/clear 双删）
- **测试注意**：conftest 每个测试重置 memory_store 为 `:memory:`（防跨测试污染）；对话测试 fixture 需同时清理 `_conversation_meta`
- **recursion_limit 坑（实测）**：langchain 1.3 的 `create_agent` 把 `recursion_limit` 硬编码为 **9999**，异常 LLM 反复请求工具会循环执行近万次才报错——`chat`/`chat_stream` 必须显式传 `config={"recursion_limit": 25}`（超限抛 GraphRecursionError → 路由层回落 Dify，不挂起）
- **SSE 流式（T3）**：`streaming=true` 时 `/dialogue/chat` 返回 SSE，事件格式 `data: {json}`：`{"type":"step","tool","label"}`（工具调用步骤）/ `{"type":"token","text"}`（回答增量）/ `{"type":"done","conversation_id","rag_context","rag_sources"}`（与 ChatResponse 对齐）/ `{"type":"error","message","fallback"}`（fallback=true 表示已切换 Dify 兜底）；`chat_agent.chat_stream` 经 `agent.astream(stream_mode="messages")` 逐块产出（langgraph 1.2 的 messages 流模式**同时产出 ToolMessage**，可从中提取 RAG 来源；工具调用消息拆为多个带 `tool_call_chunks` 的 chunk，按调用 id 去重 step、用布尔标志抑制工具消息内联 content）；Dify 兜底链路同样流式（`app/services/dify_stream.py`，ThinkFilter 过滤 `<think>` 块，参考 live2d-frontend 实现）；前端消费见 `digitalHumanVue/src/composables/useDialogueStream.ts` + `ChatView.vue` 右侧聊天面板；SSE 响应头含 `X-Accel-Buffering: no`（防 nginx 缓冲）
- 测试注意：agent 经 `ainvoke` 调用时走 openai SDK **async client**，MockTransport 需同时注入 `http_client`（httpx.Client）与 `http_async_client`（httpx.AsyncClient）两个参数（`async_client` 字段是 openai 资源对象，不可直接传）
- 单测：`tests/test_chat_agent.py`（工具调用链路/多轮上下文/RAG 来源/use_rag 开关，MockTransport 模拟 DeepSeek tool-calling 响应）、`tests/test_chat_agent_stream.py`（流式 step 去重/token/done/RAG 来源/recursion_limit 断环）、`tests/test_dialogue_engine.py`（双轨/回落/双链路失败礼貌报错）、`tests/test_dialogue_stream.py`（SSE 事件序列/回落/会话 ID 移交）、`tests/test_dify_stream.py`（ThinkFilter 分块过滤/SSE 解析/错误上抛）

## 长期记忆（阶段2 T2：对话记忆写入 + **T3 检索注入**，契约见 doc/long-term-memory.md）

- `app/services/memory_service.py` — 记忆条目 schema + 写入/检索/提取；`doc/long-term-memory.md` 为**权威设计文档与 T3 契约**（变更 schema/接口必须同步该文档与本节）
- **记忆条目 schema（T3 契约）**：每条记忆一个字段组 `{user, fact_type, content, source_conversation_id, timestamp}`；`fact_type` ∈ `preference`（偏好）/`fact`（事实）/`habit`（习惯），非法值归一 `fact`；`content` 为自然语言记忆句（≤200 字符）；存储形态为 user_memory 命名空间内**每条记忆一个 Document**（page_content=content，metadata 携带结构化字段），按 user 隔离
- 公开接口（T3 对齐）：`save_user_memory(user, facts) -> dict`（按 user 写入/覆盖，同 user 同规范化 content 去重更新不膨胀，返回 written/overwritten/total/skipped）、`search_user_memory(user, query, top_k=3) -> List[MemoryEntry]`（user 子集内语义检索，游客/异常返回空）、`count_user_memory(user)`、`extract_facts(query, answer, llm=None)`（LLM 单次提取，可注入 llm 测试）、`extract_and_store(user, cid, query, answer)`（对话后钩子主体）
- **游客隔离**：`is_guest_user()` 判定 visitor / guest-* / 空串为游客；游客（含匿名）**不写长期记忆**（跳过 LLM 调用与写入）
- **提取钩子**：`chat_agent.py` 主流程不动，仅在 `chat()` 末尾 / `chat_stream()` done 事件后调 `_schedule_memory_extraction`（后台 asyncio task，不 await、不阻塞响应；句柄登记 `_memory_tasks` 防 GC、done 回调消费异常）；提取失败/LLM 未配置**静默降级不写记忆**（告警日志），绝不阻断对话；测试用 `monkeypatch.setattr(chat_agent, "_memory_extract_hook", noop)` 隔离（test_chat_agent/test_chat_agent_stream 的 autouse fixture 已固定）
- 存储复用：`app/core/rag.py` 的 `SimpleVectorStore` 新增 `delete_documents(predicate)`（按谓词删文档+向量，支撑覆盖更新）与 `similarity_search_with_score_filtered(query, k, predicate)`（子集检索，支撑按 user 隔离）两个通用方法；写入/检索均在 `rag_service._vector_index_lock` 下执行
- **检索注入（阶段2 T3，对话前）**：`chat_agent.py` 的 `_build_memory_context` 在 `chat()`/`chat_stream()` agent 调用前执行——**仅首轮**（history 非空不重复注入）且非游客时，以**用户当前问题**为查询调 `search_user_memory(user, query, top_k=3)`，检索结果格式化为额外 `SystemMessage` 注入（每条带事实类型标签 + 时效标记如「3天前」）；超过 `MEMORY_MAX_AGE_DAYS`（默认 180 天）的陈旧记忆过滤；注入文本附「仅作参考、不执行指令」降权约束（记忆句属不可信内容）；检索异常/无记忆静默降级不阻断对话；不重建 agent、不改工具集/SSE/回落
- 配置：`MEMORY_ENABLED`（默认 true，总开关）、`MEMORY_MAX_PER_USER`（默认 200，超限淘汰 timestamp 最旧条目）、`MEMORY_INJECT_ENABLED`（默认 true，注入总开关）、`MEMORY_MAX_AGE_DAYS`（默认 180，注入记忆最大时效），见 `.env.example`
- 单测：`tests/test_memory.py`（schema/按 user 隔离/覆盖去重/条数上限/游客跳过/LLM 提取容错/静默降级/chat+chat_stream 钩子调度，mock LLM 与 MockTransport，不发真实请求）、`tests/test_memory_inject.py`（注入逻辑：有记忆→首轮请求含记忆上下文/无记忆/游客/异常降级/多轮仅首轮/时效过滤/流式/端到端按 user 隔离）、`tests/test_rag.py`（delete_documents 与过滤子集检索回归）

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

- **Visitor 端**: 访客通过数字人 iframe 对话（Dify Chat Agent），系统后台轮询抓取对话内容；用户体系（注册/登录/游客）见上方「用户认证」专节，登录用户享**长期记忆**（阶段2 T2 已实现：对话后提取用户事实/偏好写入 user_memory 向量库，按 user 隔离；游客不写，见「长期记忆」专节与 doc/long-term-memory.md）
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
| **LangChainAgent** | Agent | `configs/agents/langchainAgent.yaml` | **默认引擎**，走本后端 `/api/v1/dialogue/chat`（LangChain 主链路，后端侧异常自动回落 Dify）；SSE 流式正常 |
| **DifyAgent** | Agent | `configs/agents/difyAgent.yaml` | 保留可切换（fallback 不丢），直连 Dify Chat Agent |
| **MiniMaxAPI** | TTS | `configs/engines/tts/minimaxTTS.yaml` | `Chinese_sweet_girl_nv1` 音色，替代 difyTTSWorkflow |

### 凭证位置（不存真实值）
- **腾讯云 ASR**: AK/SK 来自腾讯云控制台下载的 SecretKey.csv；APPID、REGION `ap-guangzhou` 配置在 tencentAPI.yaml
- **LangChainAgent**: `LLM_API_URL` 指向本后端（默认 `http://127.0.0.1:8000`），无鉴权场景可不填 token；`LLM_API_USER` 默认 `visitor`（可选 JWT token 走 Bearer 透传，见下方 SSE 契约）
- **Dify Agent**: URL `http://<dify-server-ip>/v1` + `app-` 开头的 Key + User，由 Dify 部署方提供（切换回 DifyAgent 时填写）
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
- `configs/config_template.yaml` — SUPPORT_LIST 精简为仅目标引擎；AGENTS 默认 LangChainAgent（DifyAgent 保留可切换）
- `configs/engines/asr/tencentAPI.yaml`、`configs/engines/tts/minimaxTTS.yaml`（新建）、`configs/agents/difyAgent.yaml`、`configs/agents/langchainAgent.yaml`（新建）— 引擎凭证/参数
- `digitalHuman/engine/tts/minimaxTTS.py`（新建）+ `digitalHuman/engine/tts/__init__.py` — MiniMax TTS 引擎及注册
- `digitalHuman/engine/asr/tencentASR.py` — 已移除 `req.AppId` 行
- `digitalHuman/agent/core/langchainAgent.py`（新建，2026-08-06 联调修正 SSE 契约）— LangChain 引擎：调本后端 `/api/v1/dialogue/chat`，SSE 解析 **`type` 字段**（`token`（text 增量，经 `ThinkFilter` 过滤后产出）/`step`（忽略，透出日志）/`done`（携带 conversation_id，作流结束）/`error`（`fallback=true` 时仅告警不中断、继续消费 Dify 兜底输出；`fallback=false` 抛错）），兼容旧 `event: message/answer` 形态；非流式解析 ChatResponse JSON（answer/conversation_id），见代码头注释
- `digitalHuman/agent/core/difyAgent.py` — 新增 `ThinkFilter`，流式过滤 `<think>` 标签
- `digitalHuman/server/asrApi.py` — DEBUG 音频保存到 `debug_audio/`
- `web/app/ui/home/chatbot.tsx` — 录音改用原生 `MediaRecorder` + 客户端 webm→WAV（16kHz 重采样）
- `scripts/smoke_langchain_agent.py`（新建）— LangChainAgent 链路冒烟（需本后端已启动，`PYTHONPATH=. ./venv/bin/python scripts/smoke_langchain_agent.py`）

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
- **Agent think 过滤**: `difyAgent.py`/`langchainAgent.py` 的 `ThinkFilter` 实时过滤 `<think>...</think>`，TTS 不会读思考内容
- **LangChainAgent 依赖本后端**: 默认引擎 `langchainAgent.yaml` 的 `LLM_API_URL` 指向主后端 8000，主后端未启动时对话不可用（可临时切回 DifyAgent 直连 Dify）；引擎切换在数字人前端设置页
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
