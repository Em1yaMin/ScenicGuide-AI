# Project agent memory

景区导览 AI 数字人系统的项目记忆文件（唯一权威记忆源；CLAUDE.md 是指向本文件的 symlink）。语言约定：与用户交流及代码注释默认使用中文。

# Project Overview

景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台仅提供对话双轨回落与历史数据抓取（阶段4 起量化评估等分析链路已本地 LLM 化，见「Dify 依赖面」专节），另含纯本地推荐引擎、本地 RAG 知识库和 AI 运营分析。前端提供访客端（数字人对话）和管理后台（数据大屏 + 报告 + 知识库管理）。

**景区定位（船长 2026-08-11 决策，覆盖 2026-08-07 旧记录「演示数据为翠微峰/碧波湖、不引入灵山胜境」）**：景区为灵山景区（无锡灵山胜境），对话系统提示词/角色设定/知识库均以灵山为准（角色行「你是「灵山景区」的 AI 导览员」+ 景区背景 + 知识边界「以库内灵山数据为准，不引用库外景区设定」），RAG 文档源 doc/景区知识库/ 含三份 md：灵山胜境_景点结构化数据集_知识库版.md、灵山胜境_历史文化景点特色与个性化游览指南_知识库版.md 为灵山文档，景点景区旅游数据行为分析数据_知识库版.md 为全国 152 个景区的多景区行为分析数据集（仅少数章节与灵山相关）、非灵山文档（该文件在 RAG 索引中的排除已另派任务处理，不在此变更内处理）；**批次 e3（2026-08-11 灵山化）已完成：app/data/spots.json（10 景点）与 routes.json（8 路线）已从翠微峰等演示数据替换为灵山胜境数据**（核心景点：灵山大照壁/佛足坛/菩提大道/九龙灌浴/百子戏弥勒/祥符禅寺/灵山大佛/灵山梵宫/五印坛城/曼飞龙塔；8 条路线：历史文化深度游/自然风光全景游/亲子家庭轻松游/祈福朝圣一日游/佛教艺术精品游/银发康养慢游/浪漫情侣甜蜜游/灵山全景经典游），schema 未变（spot_001…/route_001…），结构化工具（search_spots 等）与首页活数据自动基于灵山；管理后台知识库页（KnowledgeView）新增「景点数据/路线数据」页签可直接管理该 JSON（见「景区数据管理」专节），知识库 CRUD/导入的 knowledge 命名空间与 app/data 结构化工具仍是两套数据但内容同源（灵山）。

# Architecture

```
digitalHumanApi/          # Python FastAPI 后端
  app/
    main.py               # 应用入口（lifespan、CORS、全局异常处理、router 注册）
    core/                 # config.py (Settings) + database.py + embedding.py (Embedding 工厂/Minimax→Qwen) + rag.py (命名空间向量存储)
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
数据流: 对话（deepagents 本地链路）→ dialogues 表（每轮两行直接落库，2026-08-11 起）→ analysis task → 本地 LLM 量化评估 → Evaluation 表（阶段4 起不再经 Dify Workflow）；Dify 历史对话 ← polling task（数据源之一，见「Dify 依赖面」）

## 个性化推荐（纯本地，无外部 API）

- `app/services/recommend_agent.py` — 基于 LangChain Tool Calling 模式 + 规则引擎/关键词匹配，工具读取 `app/data/` 本地 JSON 数据源推理推荐
- `app/routers/recommend.py` — `POST /api/v1/recommend`

## 推荐问句（阶段3 T3：本地 LLM 生成，脱离 Dify）

- `app/services/recommend_service.py` — 推荐问句生成（首页数字人引导区可提问句）：聚合 7 天 Dashboard 数据（concern_tags/mentioned_spots/sentiment_summary/total_dialogues）→ 本地 LLM 生成 questions 数组。**船长决策 2026-08-06 方案B：直接移除 Dify Workflow 调用**（不再有 DIFY_RECOMMEND_WORKFLOW_API_KEY，不留 fallback，为阶段4 Dify 退场铺路）；改用 `app/core/llm.py` 工厂（`get_chat_llm`，`LLM_*` 配置 opencode-go），输出契约不变（questions 数组，前端无感）
  - 入口 `fetch_recommended_questions(llm=None)`（可注入 llm 测试）；**无评估数据降级（2026-08 船长反馈修复）**：`_aggregate_dashboard_7d` 在 7 天窗口无评估数据（concern_tags/mentioned_spots 均空）时返回 None，fetch 改为降级用 `_build_fallback_questions` 基于本地景点/路线数据（app/data/spots.json、routes.json，复用 home_service._load_json）模板构造推荐问句（通用问句优先 → 前 3 热门景点「{名}有什么特色？」 → 路线标签问句如「帮我规划一条半日游路线」「适合拍照的路线推荐」，上限 8 条/去重/不足 3 条返回 None），不依赖 LLM，保证演示环境空库浮层有可点击问句不显示空态；本地数据也缺失时才返回 None；LLM 未配置/调用异常/解析失败仍返回 None 不抛错（LLMConfigError 单独告警），调用方保持 stale-while-revalidate 旧缓存；有评估数据时主路径（LLM 生成）行为不变
  - 输出解析 `_parse_questions_output`（容错：剥 `<think>` 标签/```json``` 围栏/JSON 解码/别名兜底/去重），不依赖 dify_helpers
- `app/tasks/recommend_questions.py` — 后台循环每 5 分钟刷新缓存（recommend_loop，main.py lifespan 注册）；`/api/v1/visitor/recommended-questions` 读缓存、`/refresh-recommend` 手动刷新
- 清理面：dify_client 已删除 `trigger_recommend_workflow`/`recommend_workflow_headers`；config.py 校验器不再要求推荐 key；`.env.example`（根目录与 digitalHumanApi/）与 docker-compose.yml 已移除该变量；对话双轨回落 Dify（dialogue.py/dify_client 其他用途）保持不动
- 测试：`tests/test_recommend_service.py`（解析/提示词/本地 LLM mock/失败降级/无评估数据降级路径）

## 本地 RAG 知识库（无外部向量库依赖）

- `app/core/embedding.py` — Embedding 选择工厂 + `MinimaxEmbeddings`（**请求体格式开关 `EMBEDDING_API_STYLE`，2026-08-07 从 Minimax 迁移 Qwen/DashScope**；httpx 实现，配置 `EMBEDDING_API_KEY` 启用，与对话 LLM（`LLM_*`）及运营分析（`OPENAI_*`）完全独立）：
  - `minimax`（默认，兼容旧配置）：**原生 API 格式（实测 embo-01，2026-08-06 联调修正）**——请求体 `{"model", "texts", "type"}`（`embed_documents` 传 `type=db`、`embed_query` 传 `type=query`，两者向量空间不同混用会降低检索质量），响应 `{"vectors": [...]}`；**先检查 `base_resp.status_code` 业务错误**（如 1008 insufficient balance，HTTP 200 但 vectors 为 null，真实环境实测 2026-08-06）——先行明确诊断，不误报"响应条数不符"
  - `openai`：OpenAI 兼容请求体 `{"model", "input": [...]}`（无 type 字段），响应 `{"data": [{"embedding": [...]}]}`（含 index 乱序恢复）；DashScope compatible-mode 实测（2026-08-07）模型 `qwen3.7-text-embedding`（1024 维），端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，key 为 `sk-ws-` 前缀；base_resp 检查仅 minimax 风格执行（openai 风格错误走 HTTP 状态码）；**单请求 input 上限 20 条（2026-08-11 船长实测 21+ 返回 400 batch size is invalid）——`DEFAULT_BATCH_SIZE` 32→20，`EMBEDDING_BATCH_SIZE` 可覆盖（config.py）**
  - 响应解析两种格式共用（vectors 数组优先，兼容 data 数组）；未配置 key 或 API 失败时自动降级本地 TF-IDF（日志告警，`degraded` 标记透出到检索响应）；单测 `tests/test_embedding.py` 覆盖 minimax/openai 两种请求体 + openai 响应解析（MockTransport）
- `app/core/rag.py` — 命名空间隔离向量存储（knowledge / documents / user_memory，各自独立索引与 `rag_data/` 持久化文件；documents 复用旧集合名 `scenic_knowledge` 兼容升级前数据）；检索阶段 numpy 矩阵乘法加速余弦相似度；兼容 langchain_core `Document`/`Embeddings` 接口；**默认文档目录 `DEFAULT_DOC_DIR` 限定到 `doc/景区知识库/`**（**两份纯灵山景区知识 md**；全国 152 景区行为分析数据 `景点景区旅游数据行为分析数据_知识库版.md` 于 **2026-08-11 船长决策方案A移出至 `doc/archive/`**——纯灵山文档仅占原索引字节约 5%，移出后 search_knowledge 不再命中南禅寺/鼋头渚等库外内容，只保留灵山知识检索面），不索引 doc/ 下架构/设计/部署文档；**部署注意：移出后持久化 rag_data 仍含旧 chunk，需执行一次 `POST /api/rag/index` 全量重建（结果 documents=2）**；**向量落盘（C4，2026-08-08）**：docs 文件旁新增 `{collection}_vectors.json` sidecar（向量 + embedding 绑定标识 + 文档摘要），启动时缓存有效（配置一致/条数维度齐整/摘要匹配）直接复用**跳过 embedding API 重算**（旧：3769 片段每次启动全量重算 ≈ 2 分钟且 Qwen 按量计费）；embedding 配置变更（模型/`EMBEDDING_API_STYLE`/端点）自动失效重建；旧格式无 sidecar 自动回退重算并升级写入；TF-IDF 路径行为不变（词汇表机制，不写 sidecar）；写文件统一临时文件+原子替换（`_atomic_write_json`，os.replace + fsync）；缓存校验/复用逻辑见 `_try_reuse_cached_vectors`
- `app/services/rag_service.py` — 文档加载、分块、向量化、检索；`RAG_MAX_CONTEXT_LEN`（默认 2000）控制注入上下文长度；`search_documents`/`index_documents` 均接受 `namespace` 参数（默认检索 knowledge+documents 公共命名空间）；`search_documents` 为**统一检索入口**（对话/后台/推荐 Agent 共用），同步实现 `_search_documents_sync` 供推荐 Agent 工具调用；另有 `index_knowledge_entries` 将知识库条目（DB 行）重建到 knowledge 命名空间，供 CRUD 变更后同步
- `app/routers/rag.py` — `POST /api/rag/search`（`namespace` 参数，响应含 `namespaces`/`embedding_backend`/`degraded` 标记）、`POST /api/rag/index`（默认写入 documents 命名空间，文档源 `doc/景区知识库/`）、`GET /api/rag/status`（各命名空间状态）
- `app/routers/dialogue.py` — `POST /api/v1/dialogue/chat` 支持 RAG 增强（`use_rag`、`rag_top_k` 参数；RAG 无结果时回退正常 Dify 调用）；另有 `/api/v1/dialogue/rag-context` 供 ADH 调用
- `app/services/recommend_agent.py` — 新增 `search_knowledge` 工具（同步包装统一检索入口，供 Agent 检索讲解词/FAQ）；推荐主流程仍用结构化工具直查（景点/路线不走向量，零幻觉原则）

## RAG 评测与准确率优化（2026-08-14：准确率 46.6% → 100%）

船长要求：RAG 景区信息准确率 ≥90%。权威文档 `doc/rag-eval/README.md`（评测设计/轮次分数/部署注意）；评测集 `doc/rag-eval/eval_set.json`（58 题、9 类题型：景点/门票/演出/位置/路线/历史/贴士/边界混淆/库外拒答）；报告 `doc/rag-eval/report_{baseline,round1,round2_final}.md`。

- **评测引擎**：`app/services/rag_eval.py`（离线可复跑：临时目录从零建索引 + 确定性 TF-IDF；判定=正向题关键词在 top_k 任一候选文本中命中（any/all，按候选独立判定不跨候选拼接）/ negative 题=任一候选无库外关键词 + top1 score<拒答阈值；输出 Markdown 报告）；CLI `scripts/rag_eval.py`（`--top-k/--threshold/--backend tfidf|api`，`--assert-accuracy` 门禁）；pytest 门禁 `tests/test_rag_eval.py`（8 用例，总准确率 ≥90% + 拒答/混淆/景点题型 ≥90% + 基础设施契约）
- **优化 1：标题感知分块**（`rag_service._split_documents` 重构，documents 命名空间专属）：以 Markdown 标题划分 section，chunk 带完整标题链前缀 + metadata `title`；超长 section 按行聚合（字段保整）+ 单行超长句子二次切分；无标题文档回退旧切分；`_parse_markdown_sections` 用标准标题栈（flush 取栈全链，防标题链重复 bug）。效果：碎片 chunk 归零（3769→80 块），基线 46.6%→98.3%——旧策略 56% 为 <50 字符孤立标题/字段行，答案常在 top_k 外
- **优化 2：检索前查询规范化**（`rag_service._normalize_search_query`，统一检索入口 `_search_documents_sync` 与评测引擎共用）：TF-IDF 词袋下口语时间问法（「晚上营业到几点」vs 文档「开放时间 9:00-21:30」）词面无交集、演艺块排第 9 名；追加同义词（几点→时间/营业·开门·开到→开放/关门→闭园时间/门票价格→免费开放——后者的背景是免票景区（拈花湾）文档以「免费开放」表述、门票价格问法会把 210 元付费票价表拉进 top3，评测 confuse_005 暴露），仅追加不替换、目标词已有则跳过 → 100%（58/58）
- **坑（评测可复现性）**：评测建索引必须用全新临时目录（tempfile.mkdtemp），复用持久化目录会叠加旧索引导致分数漂移（实测 46.6%→32.8% 假波动）
- **部署注意**：新分块改变 documents 索引形态，生产需执行一次 `POST /api/rag/index` 全量重建（旧 3769 碎片 chunk 在 sidecar 缓存命中时不会自动重建）；knowledge 命名空间不受影响（`_build_knowledge_documents` 未动）；远端 Embedding 部署用 `--backend api` 复测（拒答阈值按后端自适应：tfidf 0.30/api 0.75，api 值为真实 Qwen embedding 校准结论，见 README 部署注意）；RAG_TOP_K 默认 3 未改

## 用户认证（前端登录/注册/游客模式）

后端接口（PR #14）：`POST /api/v1/auth/register|login|guest`、`GET /api/v1/auth/me`（Bearer JWT）。

- 前端：`digitalHumanVue/src/stores/auth.ts`（Pinia）+ `src/api/auth.ts` + `src/views/LoginView.vue` / `RegisterView.vue`（顶层路由 `/login` `/register`，访客端布局直达；管理后台侧栏仅管理身份区、无用户登录区）
- token 持久化：`localStorage` 的 `auth_token`（用户/游客 JWT）+ `auth_user`（用户信息 JSON）；键名沿用 snake_case 风格（`visitor_id`、`admin_api_token`）
- 游客兜底：未登录可用全部公开能力；对话页（ChatView）自动签发短期游客 token 并随 iframe `auth_token` 参数透传给 ADH（ADH-Web 侧已消费：会话级解析并注入对话请求 `LLM_API_TOKEN` → Bearer 透传 → 后端解析真实用户身份，登录用户长期记忆/对话落库按 user 生效；未登录不注入维持 visitor 兜底，详见「ADH 本地数字人」注意事项「嵌入身份透传」条目）
- 对话页布局（2026-08-07 改造）：数字人 iframe 占满整页，无独立聊天面板；推荐问句为底部浮层（半透明毛玻璃、可折叠），点击经 postMessage `SET_INPUT` 注入 ADH 输入框（不自动发送，注入后自动收起）；对话/SSE 逻辑已随 `useDialogueStream.ts` 删除（ADH 内置完整对话功能）
- 刷新恢复：`App.vue` 启动时调 `restoreSession()` 用 `/auth/me` 校验 localStorage token，401 则清除登录态；网络失败保留本地缓存
- 错误提示：`fetchWithCodeEnvelope` 解析 FastAPI `detail`（401 "用户名或密码错误"、409 "用户名已存在"、422 校验字段）
- 无需新增前端环境变量（后端 JWT 配置见 `digitalHumanApi/.env.example`）

## 管理员账号密码登录（批次9，2026-08-10 船长指示：替代 X-Admin-Token 输入形式）

管理后台登录改为「主页账号密码登录 → 跳转 /admin」，旧 X-Admin-Token 完全保留向后兼容。

- **后端**：`POST /api/v1/admin/login`（`app/routers/admin.py`，前缀 `/api/v1/admin`）——凭据来自 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 环境变量（`config.py`，默认演示值 `admin`/`admin123456`，README 已注明；生产必须修改）；错误凭据 401；成功签发管理员 JWT（`create_access_token(token_type="admin")`，载荷 `typ=admin` 区分普通用户/游客 token）；登录端点复用 `AuthRateLimiter` 限流（同 auth.py 模式，`_admin_login_limiter`，失败 5 次/60s 进冷却 429，成功清零）
- **鉴权双路径**：`app/routers/deps.py::verify_admin_token` 新增 `authorization` Header 参数——① X-Admin-Token（既有 ADMIN_TOKENS/ADMIN_API_TOKEN 映射，向后兼容）② Authorization Bearer 管理员 JWT（`decode_bearer` + `typ=="admin"` → 恒为 admin 角色）；任一命中放行；`require_role` 同型透传；管理员 agent（ADMIN_TOOLS）内部调用方式不变
- **前端**：`src/stores/adminAuth.ts`（Pinia，`admin_jwt`/`admin_username` 持久化）+ `src/api/adminAuth.ts` + `src/views/admin/AdminLoginView.vue`（路由 `/admin/login`）+ 路由守卫（router beforeEach：未登录访问 /admin 重定向 `/admin/login?redirect=`）+ 主页 Hero 区「进入管理后台」入口；管理端 API 请求头统一走 `src/utils/adminHeaders.ts`（同时带 JWT 与旧 X-Admin-Token，旧用法不受影响）；管理页 Token 弹窗已移除（AdminLayout 侧栏显示管理身份 + 退出登录，AdminAgentView 登录失效提示条）
- **配置**：`ADMIN_USERNAME`/`ADMIN_PASSWORD` 需同步三处（digitalHumanApi/.env.example + 根目录 .env.example + docker-compose.yml 透传，默认 `admin`/`admin123456`）；测试 `tests/test_admin_login.py`（10：签发/401/双路径/RBAC/JWT 载荷区分/限流重置 fixture）

## 知识库管理（X-Admin-Token 认证）

- `app/models/knowledge.py` + `app/services/knowledge_service.py` + `app/routers/knowledge.py` — `/api/admin/knowledge*` CRUD + `/import`
- **CRUD/导入变更后自动同步向量索引**：增/删/改/导入后 `knowledge_service._sync_knowledge_index` 以 DB 全量条目重建 knowledge 命名空间（`index_knowledge_entries`，条目量小、全量重建开销可忽略）；索引失败仅告警日志不阻断 CRUD（下次变更/手动索引自愈）；批量导入仅最后统一同步一次
- 认证：`X-Admin-Token` 请求头，token 来自 `ADMIN_API_TOKEN` 环境变量（`app/core/config.py`；校验实现公共依赖 `app/routers/deps.py::verify_admin_token`，公告管理同源复用）；**RBAC 角色分级（批次6 C3）**见下方「安全加固」专节——删除类敏感操作（本模块 `DELETE /{kb_id}`）要求 admin 角色
- 前端：`digitalHumanVue/src/views/admin/KnowledgeView.vue`（管理端鉴权头统一走 `src/utils/adminHeaders.ts`，双凭据见「管理员账号密码登录」专节；批次 e3 起为三页签：知识文档/景点数据/路线数据，见下方「景区数据管理」专节）
- 导入支持 `.json` 数组和按 `# ` 标题切分的 `.md` 文件

## 景区数据管理（批次 e3：知识库页与导览 agent 景区数据同步，方案②）

船长决策 2026-08-11 灵山化：管理后台知识库页能查看/管理导览 agent 的景区数据（spots/routes），**页面数据 = agent 数据单一来源**。选定方案②（页面管理直接读写 app/data JSON 并同步 agent 工具），理由：agent 结构化工具每次调用实时读文件（零改动即生效）；home_service/推荐问句降级/管理端首页数据工具同源联动；方案①（导入 knowledge_base）会破坏结构化字段或需重构同步工具读 DB（违反「不重构对话链路」约束）。权威契约 `doc/scenic-data-api.md`。

- **后端**：`app/routers/scenic.py`（前缀 `/api/admin/scenic`，X-Admin-Token 鉴权，RBAC 同知识库：删除类要求 admin）+ `app/services/scenic_data_service.py`（文件 CRUD：**原子写入** 临时文件+os.replace+fsync、模块级写锁；id 服务端自增 `spot_011`/`route_009` 编号=max+1 不复用）+ `app/schemas/scenic.py`（字段与 app/data JSON 对齐，extra=allow）；端点：`GET/POST /spots`、`GET/PUT/DELETE /spots/{id}`、routes 同构；**业务校验**：景点/路线名称唯一 422（防 agent 工具按名检索歧义）、路线 spot_ids 必须引用已存在景点 id 422（防「未知景点」）、删除被路线引用的景点 422（detail 列出引用路线，防悬挂 spot_ids）、字段纯空白 422、难度仅 轻松/中等/挑战
- **与 agent 工具数据一致（核心验收）**：写入即 `_invalidate_agent_tool_cache` → `tool_cache.invalidate_scenic()`（新增方法，清除 search_spots/search_routes/get_spot_detail/get_route_detail/filter_by_tags/recommend_routes 缓存条目）——静态数据工具 TTL 1h，不失效则管理端改完数据 agent 最长 1 小时读到旧结果；`_STATIC_DATA_TOOLS` 与 agent.py `_CACHEABLE_TOOLS` 同源（新增工具须两边同步）
- **前端**：`src/api/scenic.ts` + `src/types/scenic.ts` + `api/index.ts` 端点常量（API_ADMIN_SCENIC_SPOTS/ROUTES）；KnowledgeView 三页签（知识文档=既有 CRUD/景点数据/路线数据），景点表单（名称/介绍/标签/时长/难度/位置/亮点）、路线表单（名称/介绍/**途经景点勾选**（从景点数据加载）/标签/适合人群/时长/难度/推荐理由），保存提示「数字人导览 Agent 实时生效」
- 测试：`tests/test_scenic_data_api.py`（25：鉴权/RBAC/灵山数据列表/CRUD/名称唯一/spot_ids 引用/删除被引用景点 422 与解除引用后可删/纯空白字段 422/agent 工具实时读到页面写入数据/数据文件原子性/工具缓存失效联动）
- 首页静态兜底（`digitalHumanVue/src/views/HomeView.vue` FALLBACK_SPOTS/FALLBACK_ROUTES）已替换为灵山地标（灵山大佛/九龙灌浴/灵山梵宫 + 三条灵山路线）

## 公告模块 + 首页活数据（批次4 B2：首个真实 Alembic 迁移）

- **公告**：`app/models/announcement.py`（announcements 表：title/content/status[published|draft]/pinned/created_at/updated_at）+ `app/schemas/announcement.py` + `app/services/announcement_service.py` + `app/routers/announcement.py`；迁移 `migrations/versions/e06ede5acb5a`（升级建表/回退删表，downgrade 已补全）
- **管理端** `/api/admin/announcements`（X-Admin-Token 鉴权）：列表（status/keyword 筛选 + 分页）/创建（默认草稿）/部分更新 PUT /{id}/状态切换 PATCH /{id}/status/删除；**访客端** `/api/v1/announcements`（公开，仅已发布，置顶优先 + 时间倒序）
- **鉴权依赖提取**：`app/routers/deps.py` 的 `verify_admin_token` 为公共实现（knowledge.py 同源复用，行为不变）
- **首页活数据**：`GET /api/v1/home/highlights`（公开）——`app/routers/home.py` + `app/services/home_service.py` 每次请求实时读 `app/data/{spots,routes}.json`（默认各 3 条，spots_limit/routes_limit 可调），前端首页卡片不再写死（HomeView 活数据渲染 + 接口失败静态兜底）；文件缺失/损坏降级空数组不报错
- **前端（批次4 B2 前端任务）**：管理端 `digitalHumanVue/src/views/admin/AnnouncementView.vue`（列表/新建编辑/删除/发布下架/置顶，鉴权头同知识库页走 `adminHeaders.ts`）；首页 `HomeView.vue` 公告区（仅已发布、置顶优先、无公告时隐藏）
- **契约文档**：`doc/announcement-api.md`（路径/请求响应 schema/鉴权/排序约定，前端已按此实现）
- 测试：`tests/test_announcement.py`（12）+ `tests/test_home.py`（6）+ `tests/test_migrations.py` 新增 announcements 升级/回退用例；**坑：`config.py` 的 `load_dotenv(override=True)` 使 .env 优先于环境变量，临时改配置需直接改 .env 或用 pytest monkeypatch**

## 用户体系（注册/登录/JWT/游客）

- 模型 `app/models/user.py`（users 表：username 唯一、password_hash、nickname、last_login_at）+ `app/core/security.py`（pwdlib[bcrypt] 哈希 + PyJWT 签发/校验）+ `app/routers/auth.py`（`/api/v1/auth/register|login|me|guest`）+ `app/services/auth_service.py`
- 密码绝不落明文/日志；`JWT_SECRET` 未配置时启动生成随机临时密钥（重启后 token 失效，生产必须设置）
- **对话接口用户标识**：`/api/v1/dialogue/chat` 优先从 `Authorization: Bearer` token 解析（登录用户→用户 id、游客→guest-xxx），无 token 回退请求体 `user`（默认 `visitor`，向后兼容）；token 无效时降级 `visitor` 并告警（不阻断对话），认证接口严格 401（决策注释见 `security.py resolve_dialogue_user`）
- 配置：`JWT_SECRET`、`JWT_EXPIRES_MINUTES`（默认 1440）、`GUEST_TOKEN_EXPIRES_MINUTES`（默认 30），见 `.env.example`

## 对话 LLM 接入层（阶段1，LangChain 工厂）

- `app/core/llm.py` — ChatOpenAI（langchain-openai）工厂：`get_chat_llm()`（按 `LLM_*` 配置构造，主端点缺 key 自动降级备用端点，都缺抛 `LLMConfigError`）、`create_chat_llm()`（显式参数底层构造）、`resolve_endpoint()`；主/备用双端点：`LLM_API_*`（默认 opencode-go `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`）与 `LLM_FALLBACK_*`（**代码兜底默认仍为官方 DeepSeek API `https://api.deepseek.com/v1`；部署配置按船长决策 2026-08-06 备用同主端点 opencode-go**——opencode 服务本质转发 DeepSeek 官方 key，无需另配官方备用），与 Embedding（`EMBEDDING_*`）、运营分析（`OPENAI_*`）配置完全独立
- **DeepSeek 思考格式兼容性**（实测结论，详见 `doc/llm-adapter-smoke-test.md`）：`reasoning_content` 被 langchain-openai 自动剥离（流式思考 chunk 为空串、content 为最终回答、不报错）→ 无需自定义处理；**max_tokens 必须经工厂 `extra_body` 注入原生 `max_tokens` 字段**（langchain-openai 1.4.1 硬编码改名为 `max_completion_tokens`，DeepSeek 系端点静默忽略致长度限制失效）；**不要在 `invoke()` 显式传 max_tokens**
- 备用通道官方 DeepSeek API 多轮对话要求历史 assistant 消息回传 `reasoning_content`（opencode-go 网关不强制）；对话链路（chat_agent 弃用链路与 deepagents 主链路均未实现）尚未实现该自定义转换，备用端点多轮对话受限（**仅当部署改配官方 DeepSeek 备用时适用，见 doc/llm-adapter-smoke-test.md §3；部署默认备用同主端点 opencode-go，不受影响**）
- 冒烟脚本：`digitalHumanApi/scripts/smoke_llm.py`（`LLM_API_KEY=... python -m scripts.smoke_llm`；`--fallback` 测备用端点）；单测 `tests/test_llm.py`（MockTransport 模拟，不发真实请求）

## 对话 agent（阶段1 T2+T3：LangChain 主链路 + Dify 双轨 + SSE 流式）——**已弃用（A3）**

**A3 起对话主链路切换到 deepagents 装配层**（见下方专节），本模块保留为：会话存储层单一数据源（`_get_or_create_conversation`/`_append_history`/`has_conversation`/`evict_expired_conversations`，装配层复用）、记忆钩子（`_schedule_memory_extraction`，装配层复用）、路由回落归属判定（`has_conversation`）。自研双模式对话能力（含 planner_agent）保留为参考实现，A4 收官再评估删除。

- `app/services/chat_agent.py` — LangChain tool-calling agent（**langchain 1.x 新 API `create_agent`**，返回 langgraph 状态图，`ainvoke({"messages": [...]})`；旧的 `create_tool_calling_agent`/`AgentExecutor` 在 1.x 已移除）：结构化工具直查景点/路线（复用 `recommend_agent` 的 `search_spots`/`search_routes`/`get_spot_detail`/`get_route_detail`/`filter_by_tags`，零幻觉）、`search_knowledge`（复用统一检索入口）、`recommend_routes`（包装 `recommend_agent.recommend`，工具内 `asyncio.run` 安全——langgraph 经线程池执行同步工具）；多轮上下文：**阶段2 T1 起短期记忆持久化**（见下方「短期记忆持久化」专节，进程内 TTL 缓存 + SQLite 双写，重启可恢复）；RAG 来源从返回消息中最后一条 `search_knowledge` 的 ToolMessage 提取透出
- 双轨开关：`DIALOGUE_ENGINE=langchain`（**默认**，异常自动回落 Dify，演示不翻车）`|dify`（强制原链路）；`app/routers/dialogue.py` 的 `/api/v1/dialogue/chat` 先走 `deepagents.chat`（A3 起，原 `chat_agent.chat` 已弃用），任何异常（含 `LLMConfigError`）回落 `_call_dify_chat`（保留原 RAG 预检索逻辑）；回落时经 `has_conversation` 判定，不把生成的进程内会话 ID 透传给 Dify（未知 id 会 400 conversation_not_exist），改传空串让 Dify 新建会话；响应契约不变（answer/conversation_id/rag_context/rag_sources）

## deepagents 装配层（A3 对话主链路，批次3：A1 实测报告 data/sg-deepagents-probe/report.md 装配清单 → A2 试点 → **A3 切换主链路**）

- `app/services/deepagents/` — deepagents 0.7.5 装配层，**A3 起为对话主链路**（路由 `/api/v1/dialogue/chat` 默认引擎 langchain 走本层，异常回落 Dify）：`harness.py`（HarnessProfile 注册 + 装配常量）、`memory_tools.py`（记忆四工具）、`agent.py`（get_agent 缓存 / chat / chat_stream，SSE step/token/done 契约与 ADH 前端一致）；测试 `tests/test_deepagents_{harness,memory_tools,intent,stream,main}.py`
- **A3 会话管理接入**：`chat()`/`chat_stream()` 复用 chat_agent 会话存储层（`_get_or_create_conversation`/`_append_history`，进程内 TTL + SQLite 双写重启可恢复）与记忆钩子（`_schedule_memory_extraction`，对话后自动抽取写入长期记忆）——单一数据源：① 重启后续聊不丢多轮上下文；② 生成的会话 id 登记在 chat_agent 归属集合，路由回落 Dify 的 `has_conversation` 判定零改动；`conversation_id` 参数/返回值/SSE done 事件透出（空串新建 uuid）；历史在 done 事件前落盘（客户端收到 done 即关闭也不丢上下文），记忆钩子在 done 后调度；测试见 `tests/test_deepagents_main.py`（生成/多轮/重启恢复/钩子/游客跳过/归属判定）
- **记忆注入改造（A3）**：对话前「按问题语义检索注入记忆」（chat_agent `_build_memory_context`，仅弃用链路保留）→ 改为「轻量用户上下文注入（登录用户=昵称/记忆条数摘要；游客=游客模式提示 `_GUEST_MODE_NOTE`，如实告知无法保存长期记忆、防口头承诺「记住了」；`_build_light_summary`，每轮注入）+ 记忆工具按需检索（recall_user_memory 等四工具）」，对话后自动抽取写入保留（钩子不动）
- **HarnessProfile（关键坑，A1 报告 §3）**：内置工具只能经 `HarnessProfile.excluded_tools` 排除（FilesystemMiddleware/SubAgentMiddleware 是受保护脚手架，不能经 excluded_middleware 排除）；profile 键为模型 provider（ChatOpenAI → `"openai"`），未注册 profile 时内置工具**不会被自动排除**；排除集 = A1 报告 8 个内置工具（ls/read_file/write_file/edit_file/glob/grep/execute/task）+ **实测补 `delete`（第 9 个文件工具，A1 报告未列）**；另需 `GeneralPurposeSubagentProfile(enabled=False)` 禁用默认通用子代理（task 工具）
- **装配常量**：`_MAX_AGENT_STEPS=34`（create_deep_agent 硬编码 recursion_limit 9999，必须显式传 config 收紧；A4 起与工具调用上限 15 联动，见下方「deepagents 成本/速度调优」专节；语义同 chat_agent 的 25）、`_AGENT_TIMEOUT=120`（A1 建议 ≥120s）、`_AGENT_MAX_TOKENS=2048`（经 llm.py 工厂 extra_body 注入）；**agent 实例缓存必须**（构造耗时 ~24s，`get_agent`/`reset_agent_cache`，llm 注入时独立构建不缓存）
- **user 注入（实测验证）**：自定义 `ScenicDeepAgentState(DeepAgentState)` 追加 `user` 字段；记忆工具参数用 `Annotated[str, InjectedState("user")]`（langgraph.prebuilt）——图内从 state 注入，模型侧 schema 自动隐藏；`agent.ainvoke/astream({"messages": ..., "user": user})`
- **图内模型调用流式行为（mock 坑）**：`agent.ainvoke`（图级）时模型调用非流式 → mock 返回普通 JSON；`agent.astream`（图级流式）时模型调用被流式化 → mock 必须返回 SSE chunk 帧
- **意图分类双模式挂载（船长决策：planning=prompt 指令）**：复用 `intent_classifier`——简单问答不注入规划指令（零开销）；复杂任务注入 `_PLAN_INSTRUCTION`（prompt 级规划，A1 实测回答结构化分节）；轻量用户上下文（登录用户=昵称 + 记忆条数摘要，防"忘记调用"记忆工具；游客=游客模式提示 `_GUEST_MODE_NOTE`，明确无法保存长期记忆、须如实告知不得口头承诺——实机测试问题 5；`_build_light_summary`，DB 查询失败静默降级）每轮注入 system prompt 侧
- **记忆工具化四工具（船长决策，user_id 隔离）**：`recall_user_memory(关键词)`/`save_user_memory(事实)`/`list_user_memories()`/`delete_user_memory(id)`，全部经 InjectedState 注入当前用户（模型看不到注入参数，无法经文本指定他人）；游客存储层读写全部跳过——`save_user_memory` 对游客返回明确失败提示（「游客模式无法保存」），工具描述与系统提示词约束模型如实转达保存结果、严禁口头承诺「记住了」（实机测试问题 5）；`memory_service` 新增公开接口 `list_user_memories`/`delete_user_memory`，Document metadata 增 `memory_id`（uuid，T3 契约 to_dict 键集合不变；旧条目派生 id `legacy:{归一化 content}` 兼容删除），详见 `doc/long-term-memory.md`
- **SSE 契约映射（A1 报告 §5，A3 已验证与 ADH 前端契约一致，前端零改动）**：step 经 AIMessageChunk.tool_call_chunks 按 call id 去重、token 为 AI chunk 内容增量、done 提取 search_knowledge 的 ToolMessage 作 RAG 来源（done 携带 conversation_id + intent 观测字段）；**必须保留 suppress_tokens 语义**（模型在调用工具前会输出正文 token，工具调用消息的内联 content 需抑制，实测必现）
- **对话落库（dialogues 表，2026-08-11 船长调试修复）**：本地对话历史上从不写 dialogues 表（仅 Dify 轮询/回填写入）→ 管理后台大屏/感受度报告永远为空；现由路由层（`routers/dialogue.py` 的 `/api/v1/dialogue/chat`，注入 `Depends(get_db)`）在 deepagents 链路正常完成后调 `chat_agent.record_dialogue` 落库——会话层单一数据源（deepagents 装配层与弃用链路共用）：每轮问答写两行（用户消息 + AI 回复），`visitor_id` 用对话用户标识（登录用户 id / 游客 id / visitor），visitors 记录按需 upsert（**原子 `ON CONFLICT DO NOTHING`**，防同一新 visitor 并发首条消息 IntegrityError 整轮回滚丢库）；`external_message_id` 生成 `local:{uuid}:{user|answer}`（unique 约束可去重，与 Dify 消息 id 前缀不冲突）；写入时机：非流式成功后、流式 done 事件处（yield done 前 await，客户端断开也不丢；与会话存储 `_append_history` 的 done 前落盘同策略）；**主链路异常回落 Dify 时不落库**（数据源在 Dify 侧，由轮询/回填负责）；失败仅告警不阻断对话（双保险 try/except）；评估链路零改动——本地落库的 dialogue 自动进入 `analysis_loop` 的 `batch_evaluate`（LEFT JOIN 未评估 + content 非空）→ Evaluation 写入，大屏/报告数据源恢复；测试 `tests/test_dialogue_persistence.py`（8：游客/登录用户/游客 token 落库、多轮每轮两行且 visitor 不重复、原子 upsert 并发重复、流式 done 落库、落库失败不阻断、回落 Dify 不落库）

## deepagents 成本/速度调优（A4，批次3 收官：组合拳实测定参）

船长决策组合拳（全用 langchain 自带中间件，零新依赖，只改后端；实测与定参依据见 `doc/deepagents-tuning.md`）：

- **ToolCallLimitMiddleware（单轮工具调用上限）**：`_build_agent` 装配 `ToolCallLimitMiddleware(run_limit=AGENT_MAX_TOOL_CALLS=15, exit_behavior="continue")`（create_deep_agent 的 middleware 参数；state schema 自动与 ScenicDeepAgentState 合并——graph channels 出现 `run_tool_call_count`/`thread_tool_call_count`）；**run 级计数**（stateless 图每次 ainvoke 独立，不跨轮累积）；`continue` 优雅降级——超限调用被拦截并以错误 ToolMessage 回给模型，模型基于已有信息作答（不报错、不回落）；定参：A1 实测 avg 9/peak 13 步 → A4 实测（真实 LLM 3 轮复杂任务）avg 11/peak 14，上限 15 全程未触发拦截，保留作病态循环保险
- **工具结果归一化缓存（`deepagents/tool_cache.py`）**：`wrap_tool` 包装高频只读工具（search_spots/search_routes/get_spot_detail/get_route_detail/filter_by_tags/recommend_routes/search_knowledge）——同名/同描述/同 args_schema 仅换执行函数（模型侧 schema、SSE step 按名透出、RAG 来源提取均不受影响）；归一化键（去空白/中英文标点/大小写，"灵山 景区"与"灵山景区"同键）+ kwargs 排序；**TTL 分级**：静态数据工具 3600s（app/data/ 静态 JSON 安全长缓存）/ search_knowledge 300s + **索引重建即时失效**（rag_service `_index_documents_sync`/`_index_knowledge_entries_sync` 全量重建后调 `invalidate_knowledge()` 版本号 +1——知识库 CRUD 同步索引既有机制天然挂钩）；512 条目 LRU + threading.Lock（langgraph 线程池执行同步工具）；`AGENT_TOOL_CACHE_ENABLED=false` 原样返回（回退开关）；记忆四工具不缓存；`get_cache().stats()` 命中统计供观测
- **上下文压缩（`deepagents/compaction.py`，回答后触发，船长决策）**：**不用图内挂中间件**（stateless 调用下每轮重复压缩反而增成本 + 增加首字延迟），改为会话存储层回答后触发：`chat()`/`chat_stream()` 在 `_append_history` 后经 `_schedule_compaction` 以**后台 asyncio 任务**调度 `_maybe_compact_history`（与记忆提取钩子同模式：不阻塞响应/done、done 前已调度客户端断开也不丢，review F1 2026-08-08 修复）——历史 ≥ `AGENT_COMPACT_TRIGGER_MESSAGES=24`（≈12 轮，2 条/轮折算）时调内置 `SummarizationMiddleware.abefore_model`（纯状态函数：阈值判定/安全截断/摘要生成全复用）压缩早期轮次，保留最近 `AGENT_COMPACT_KEEP_MESSAGES=10`（≈5 轮）完整消息，结果写回（进程内 + `memory_store.replace_messages`，重启不丢、不重复压缩）；**自定义中文摘要 prompt 显式要求保留关键约束（带老人/带小孩/预算/拍照等）与已确认事实**（测试断言摘要含"带老人小孩"）；降级：LLM 失败/摘要空或错误文本（中间件把异常吞为 "Error generating summary" 文本需识别）/并发有新轮次追加（快照末条校验）→ 跳过压缩（`_MAX_HISTORY_MESSAGES` 20→30 为截断兜底）；`AGENT_COMPACT_ENABLED=false` 回退
- **实测（真实 LLM 3+3 轮，见 doc/deepagents-tuning.md §4）**：simple 平均 17.7s/8083+693 tok（轮1 纯问答 4.57s/1843+253 与 A1 4.87s/1372+287 持平；轮2 首字 27s 为 opencode-go 延迟波动非调优引入）；complex 平均 37.4s/31713+3369 tok/11 工具调用（含多轮历史累积，口径与 A1 单轮不同）；长会话演示第 12 轮触发压缩（历史 22→11 条），压缩后下一轮 input 20k→4.5k；组合拳边界：3 轮复杂任务 7/14/12 次调用均 ≤15 未被截断；`recursion_limit`（harness `_MAX_AGENT_STEPS`）30→34——恰好 15 次调用 ≈ 31 superstep，34 保证 continue 优雅降级在边界不触发 GraphRecursionError（doc §1）
- **测试**：`tests/test_deepagents_tool_cache.py`（10：归一化/键/TTL/知识版本失效/上限淘汰/开关/包装行为/统计）+ `tests/test_deepagents_tool_limit.py`（5：装配检查/拦截行为/未超限不变/run 级独立）+ `tests/test_deepagents_compaction.py`（12：阈值/质量/失败降级/开关/chat+stream 集成/持久化重启/并发写回保护/replace_messages）；全量 446 绿（基线 419 + 27）；SSE 契约与回落行为不变（上限拦截不抛异常、压缩失败静默）

## GPS/高德（批次8 B3：演示主推「描述位置 → 地理编码 → 路径规划 → 地图展示」）

船长决策：浏览器定位为辅（前端任务）；离线/异常直接报错误码不做离线兜底；坐标全部来自高德 API（结构化零幻觉）。权威契约 `doc/amap-gps-api.md`（错误码/信封/工具用法）。

- `app/services/amap/` — 高德 Web 服务封装（httpx，零新依赖）：`geocode(address, city=None)`（地址 → ≤5 候选含省/市/区县，**多候选消歧=候选列表 + city 参数组合**，实测"黄鹤楼"跨城市同名）、`route(origin, destination, mode)`（walking/driving/transit；transit 走 integrated 接口，**自动 regeo 推导 city/cityd**，transits 归一化为统一 steps 含公交合成指令「乘坐{线路}（上车站 → 下车站）」；**三种模式请求统一带 `extensions=all`（2026-08 实机问题2：公交默认 base 模式会把步行段 instruction 替换为十六进制标记如「步行23米0x02」，须带该参数才返回完整中文动作）**）；**B4 起 steps 透出 `polyline` 字段**（高德坐标串 "lng,lat;lng,lat;..." 原样透出；walking/driving 每步均有，transit 步行段保留、公交段可空，供前端拼接画实际路径折线而非起终点直线，见 doc/amap-gps-api.md）、`regeo`（内部辅助不对 agent 暴露）；`AmapClient(api_key=None, _transport=)` 测试注入 MockTransport（embedding 同模式）；模块级 `geocode/route` 单例供工具调用（key 每次调用实时读 `settings.AMAP_WEB_KEY`，monkeypatch 即模拟无 key）；**统一错误信封** `{ok, error, message}`：amap_not_configured（无 key，不阻断对话）/amap_unavailable（网络/HTTP/业务错误 status≠1/解析失败；httpx 0.28 的 HTTPStatusError 与 RequestError 平级须用超类 HTTPError 捕获）/amap_no_result/amap_invalid_param/amap_route_failed
- `app/services/deepagents/amap_tools.py` — 对话两工具：`geocode_location(description, city)` / `plan_route(origin, destination, mode)`（origin/destination 必须是 geocode 候选的 location 坐标，**工具描述引导先 geocode 后 plan_route**）；信封 JSON 原样透出（意外异常防御性包装 amap_unavailable），模型如实转达不编造；`AMAP_STEP_LABELS`（正在定位…/正在规划路线…）合并进装配层 `_STEP_LABELS`，SSE step 事件透出（ADH 前端零改动）；**B3 起对话响应（ChatResponse）与流式 done 事件透出 `route_data`**（plan_route 信封原样透出、无规划为 null，ADH 渲染路线卡片/文字兜底，契约见 doc/amap-gps-api.md）
- **装配**：deepagents 用户侧 `_select_tools` 始终装配（use_rag 无关）；**管理员 agent（ADMIN_TOOLS）不装**；不参与工具结果缓存（实时外部数据）；系统提示词新增 GPS 使用原则
- 配置：`AMAP_WEB_KEY`（config.py + 根目录/digitalHumanApi 两个 .env.example + docker-compose 透传）；与前端 JS API key（NEXT_PUBLIC_AMAP_JS_KEY/NEXT_PUBLIC_AMAP_SECURITY_CODE，ADH web/.env，任务约定 VITE_AMAP_* 为兼容别名）两套独立凭据
- 冒烟：`python -m scripts.smoke_amap [起点] [终点] [mode]`（真实 key 在部署 .env；无 key 输出 amap_not_configured 等价验证错误码契约）；测试 `tests/test_amap_client.py`（28）+ `tests/test_amap_tools.py`（13：工具行为/装配用户侧有管理员无/不缓存/SSE 标签与流式透出/system prompt 引导）；harness 测试装配断言集合已同步（含 GPS 两工具）

## Dify 依赖面（阶段4 收官：退场依据见 doc/dify-dependency-audit.md）

船长决策 2026-08-06 方案B：阶段4 Dify 退场工作并入阶段3 顺手推进——盘点并清理 Dify 依赖面，**除对话双轨回落保留外其余尽量清理**。阶段4 首项（2026-08-07 方案A）已落地：**量化评估已本地化**。阶段4 收官（2026-08-07）：**模拟 Dify 不可达全链路自检通过**（对话双模式/流式/记忆、推荐问句、分析、量化评估→Evaluation→管理后台报告全部正常；仅保留项明确降级不崩溃，自检无代码缺陷），详见 `doc/phase4-e2e-verification.md`。**Dify 依赖面定稿**——当前 Dify 依赖仅剩两类（推荐问句、分析接口、量化评估均已脱离 Dify）：

1. **对话双轨回落（保留，演示兜底）**：`dialogue.py` 的 `_call_dify_chat`/`_call_dify_chat_stream` + `app/services/dify_stream.py`（SSE 消费 + ThinkFilter）；`DIFY_API_URL`/`DIFY_API_KEY`
2. **历史数据接口（保留，数据源之一）**：`tasks/polling.py`（30s 轮询拉 **Dify 对话**入库；本地对话已由对话链路直接落库，轮询不再是唯一来源）、`tasks/backfill.py`（一次性补拉 CLI）、`services/dify_client.py` 的 `get_conversations`/`get_conversation_messages`；`DIFY_LIGHT4AI_USER`（空则轮询空转）
  - **2026-08-11 历史数据已迁移（fm/dify-7b）**：旧 Dify 服务器（http://111.228.47.45）实测 user=`visitor` 下有 5 conversation/6 message（此前盘点候选均空——对话创建于 2026-08-05 盘点次日，08-11 复探才命中），已用 `python -m app.tasks.backfill --user visitor` 全量迁入本地（dialogues 新增 12 行 + visitors 新增 `visitor` 记录，幂等可重跑）；本机 `.env` 已配置 `DIFY_LIGHT4AI_USER=visitor`（polling 增量生效）；验证 Dashboard total_conversations>0、感受度报告有数据、pytest 739 绿；详见 `doc/dify-data-migration-report.md` §6

已清理面：推荐问句（#30，`DIFY_RECOMMEND_WORKFLOW_API_KEY` 已移除）、运营分析接口（#31，`analytics_service.py` 纯本地 LLM + DB 兜底，`llm_analysis` 降级标记）、推荐 Agent 的 dify_helpers 依赖（#30）、**Dify Workflow 量化评估（阶段4 首项：`evaluate_dialogue` 改本地 LLM——`app/services/evaluation_service.py`，移除 `trigger_workflow`/`dify_helpers.py`/`DIFY_WORKFLOW_API_KEY`（config 校验器/docker-compose/CI/.env.example 同步））**。联调全链路验证与规划路径偶发空响应修复记录见 `doc/phase3-e2e-verification.md`。

## 本地 LLM 量化评估（阶段4 首项：Dify Workflow 评估本地化，方案A）

船长决策 2026-08-06：量化评估脱离 Dify，Evaluation 表继续产出（管理后台 Dashboard/Report 情感/满意度/热点/景点热度数据源**不变**），对话双轨回落与历史数据接口保留不动。

- `app/services/evaluation_service.py` — 本地 LLM 评估（复用 `app/core/llm.py` 工厂 `get_chat_llm`，`LLM_*` 配置 opencode-go；prompt 语义移植自原 Dify 量化工作流，见 `doc/dify配置/quantitative-workflow-量化工作流.md`——现为历史存档）：输出情感（positive/neutral/negative，happy/confused 归一）/满意度（1-5，越界钳制）/关注点/涉及景点/改进建议（suggestions，1-3 条，无建议为空数组）
  - `_parse_evaluation_output` 容错解析：剥 `<think>` 标签/```json``` 围栏/JSON 解码/字段别名兜底（sentiment/concerns/spots）/类型归一/**全空判定**（字段全缺视为解析失败返回 None，不写全 NULL 垃圾行）
  - `evaluate_content(content, llm=None)`（可注入 llm 测试）：LLM 未配置（LLMConfigError）/调用异常/解析失败均返回 None 不抛错，调用方本轮跳过、下轮重试；`asyncio.to_thread` 派发 invoke（事件循环不阻塞）；单条超时预算 120s（opencode-go 延迟高，参考 `doc/phase3-analytics-verification.md` 经验；实测单条 3-7s）
- `app/tasks/analysis.py` — `analysis_loop`（300s）→ `batch_evaluate` **三阶段化**：①查未评估对话（LEFT JOIN）②**并发**本地 LLM 评估（`asyncio.gather` + `EVAL_CONCURRENCY=4` 信号量限流；`evaluate_dialogue` 只读 content 构造脱库 ORM 对象，共享会话安全）③串行去重校验 + 写 Evaluation 并提交；单条失败（None/异常）静默跳过不阻断批次；`evaluate_dialogue`/`batch_evaluate` 符号保留（`/api/v1/analysis/run-now` 与测试兼容）；`MAX_BATCH_SIZE=50`
- 测试：`tests/test_evaluation_service.py`（解析/评估/降级/Evaluation 映射）+ `tests/test_analysis.py`（批量写入幂等/部分失败静默降级/loop 韧性）

## 双模式对话（阶段3 T1：简单问答快速路径 / 复杂任务 LangGraph 规划图，方案B）——**已弃用（A3）**

**A3 起主链路改走 deepagents 装配层**（意图分类双模式挂载 + prompt 级 `_PLAN_INSTRUCTION` 规划指令，见上方专节）；本专节描述的自研双模式（chat_agent 快速路径 + planner_agent 规划图）仅保留为参考实现，`intent_classifier` 词表约束仍适用（装配层复用同一分类器）。设计/测试/冒烟详见 `doc/phase3-dual-mode.md`（含技术坑与真实请求证据）。

- `app/services/intent_classifier.py` — **轻量规则分类**（纯字符串匹配、零延迟；不选 LLM 单次分类——会给简单消息引入额外 LLM 往返，违背「简单问答延迟无显著增加」）：`classify_intent(query)` 返回 `simple`/`complex`，失败/未命中默认 `simple`（安全降级）；**词表设计约束：与既有测试查询兼容 + 船长决策 2026-08-06 方案B 收窄**——「拍照」「推荐」「推荐路线」等单轮偏好表达不判复杂（快速路径 recommend_routes 可处理），**纯人群/导航词（「怎么走」「带老人」「带小孩」「老人小孩」「带娃」「亲子游」「情侣游」「全家游」「家庭游」）不判复杂**（单轮可答的导航/适宜性/单偏好查询走快速路径，避免规划路径延迟增量）；仅明确多步规划词判复杂（规划/安排/行程/攻略/N日游/怎么玩/怎么安排/路线推荐/推荐一条/预算/制定行程/帮我计划/帮我规划），组合规划类查询（如「规划灵山一日游，带老人小孩，喜欢拍照」）仍复杂；同查询复杂信号 ≥ 简单信号时判复杂
- `app/services/planner_agent.py` — **LangGraph 显式图**（langgraph 1.2 自带，零新依赖）：`START → plan（规划）→ execute（循环 ≤6 步）→ summarize（总结）→ END`；`chat_plan`（非流式）/`chat_plan_stream`（SSE，复用 step/token/done 契约）；**规划阶段用 `llm.astream` 累积 + `_parse_plan` 手动解析 JSON**——langgraph 把图内模型调用统一转为流式（stream=True），`with_structured_output` 解析器不兼容流式分块（实测报错），JSON 解析跨 DeepSeek/OpenAI 端点健壮；**偶发空响应重试（阶段3 收官联调发现并修复，2026-08-06）**：opencode-go 思考型模型（deepseek-v4-flash）对长规划 prompt 偶发返回 200 但 content 为空（思考耗尽不报错，实测约 50% 概率与 max_tokens 无关），`plan_node` 对空/无效输出/流式无分块异常重试 1 次（`_PLAN_RETRIES=1`），重试耗尽仍抛 `PlannerError`；执行阶段 `BaseTool.ainvoke` 线程池执行（`recommend_routes` 内含 asyncio.run 安全），单步失败记录错误继续；总结阶段流式逐字输出（`stream_mode=["updates","messages"]`，仅透出 `langgraph_node=="summarize"` 的 token；**token 先于节点完成事件到达 → 总结步骤事件在首个 token 时输出**保序）；RAG 来源从执行结果最后一条 `search_knowledge` 提取透出；异常统一抛 `PlannerError`（规划失败/步骤全非法/总结为空/LLM 故障）
- **chat_agent 分流**：`chat()`/`chat_stream()` 在会话管理 + 长期记忆注入（T3）之后调 `_is_complex_intent(query)`（双层 try/except 防御）——复杂且 `AGENT_DUAL_MODE` 开启走规划路径（`planner_agent.chat_plan` / `chat_plan_stream`，`tools=_select_tools(use_rag)` 传入、记忆注入上下文随 messages 透传）；规划异常静默降级快速路径（流式先发 `step tool=plan_fallback label=正在直接为您解答…`），再由路由层兜底 Dify（双轨不变）；会话/历史/记忆钩子仍由 chat_agent 统一管理
- 配置：`AGENT_DUAL_MODE`（默认 `true`；`false` 全部走快速路径，行为与阶段2 完全一致，回退开关）
- 测试：`tests/test_intent_classifier.py`（38）+ `tests/test_planner_agent.py`（15，含规划偶发空响应重试 2 用例）+ `tests/test_dual_mode.py`（10）；**测试注意：图内模型调用统一 stream=true，mock handler 需按首条 system 消息内容区分规划节点（含「规划引擎」）与总结节点**；存量测试查询（「我喜欢拍照」「推荐路线」等）必须保持 simple（词表约束）

## 短期记忆持久化（阶段2 T1，船长决策：进程内 TTL 缓存 + SQLite，暂不上 Redis）

- `app/core/memory_store.py` — 会话历史存储层：**标准库 sqlite3**（无新增依赖），单表 `conversation_sessions`（conversation_id PK / user / source / messages JSON 消息序列 / created_at / last_active_at / ttl_expires_at），独立文件（默认 `memory_store.db`，不动主库 digital_human.db）；单连接 + 线程锁；`source`（`langchain`=本模块生成 uuid / `client`=客户端传入）持久化归属判定，重启恢复时据此重建 `_generated_conversation_ids`（双轨回落兼容）；TTL 滑动窗口（每次追加刷新，配置 `MEMORY_TTL_DAYS` 默认 7 天）；访问/追加时惰性删除过期行
- 读写策略 = **读走缓存 + 写双写**：`_conversations` 热缓存不变（`_MAX_CONVERSATIONS` LRU、`_MAX_HISTORY_MESSAGES` 上限语义不变），每次追加即时落盘（崩溃无丢失窗口）；缓存未命中时从 SQLite **懒恢复**（重启后同 `conversation_id` 续聊不丢多轮上下文）；LRU 淘汰只出缓存、不出库（`_evict_locked` 注释）；SQLite 写失败仅告警降级进程内记忆，不阻断对话（`_store_call`）
- `app/tasks/memory_cleanup.py` — 后台清理任务（analysis_loop 模式）：启动立即清理一次 + 每 `MEMORY_CLEANUP_INTERVAL_SECONDS`（默认 3600s）定期删 SQLite 过期行 + 驱逐进程内过期缓存（`chat_agent.evict_expired_conversations`）；main.py lifespan 注册
- 配置：`MEMORY_DB_PATH`/`MEMORY_TTL_DAYS`/`MEMORY_CLEANUP_INTERVAL_SECONDS`，见 `.env.example`；测试：`tests/test_memory_store.py`（存储层/清理任务）+ `test_chat_agent.py` 短期记忆持久化节（重启恢复/LRU 不丢库/TTL 过期不恢复/clear 双删）
- **测试注意**：conftest 每个测试重置 memory_store 为 `:memory:`（防跨测试污染）；对话测试 fixture 需同时清理 `_conversation_meta`
- **recursion_limit 坑（实测）**：langchain 1.3 的 `create_agent` 把 `recursion_limit` 硬编码为 **9999**，异常 LLM 反复请求工具会循环执行近万次才报错——`chat`/`chat_stream` 必须显式传 `config={"recursion_limit": 25}`（超限抛 GraphRecursionError → 路由层回落 Dify，不挂起）
- **SSE 流式（T3）**：`streaming=true` 时 `/dialogue/chat` 返回 SSE，事件格式 `data: {json}`：`{"type":"step","tool","label"}`（工具调用步骤）/ `{"type":"token","text"}`（回答增量）/ `{"type":"done","conversation_id","rag_context","rag_sources"}`（与 ChatResponse 对齐）/ `{"type":"error","message","fallback"}`（fallback=true 表示已切换 Dify 兜底）；主链路（`deepagents/agent.py`，A3 起）与弃用链路（chat_agent）均经 `agent.astream(stream_mode="messages")` 逐块产出（langgraph 1.2 的 messages 流模式**同时产出 ToolMessage**，可从中提取 RAG 来源；工具调用消息拆为多个带 `tool_call_chunks` 的 chunk，按调用 id 去重 step、用布尔标志抑制工具消息内联 content）；Dify 兜底链路同样流式（`app/services/dify_stream.py`，ThinkFilter 过滤 `<think>` 块，参考 live2d-frontend 实现）；SSE 前端消费方为 ADH `live2d-frontend/digitalHuman/agent/core/langchainAgent.py`（主前端 2026-08-07 起不再自带 SSE 聊天面板，对话内置在 ADH iframe 内）；SSE 响应头含 `X-Accel-Buffering: no`（防 nginx 缓冲）
- 测试注意：agent 经 `ainvoke` 调用时走 openai SDK **async client**，MockTransport 需同时注入 `http_client`（httpx.Client）与 `http_async_client`（httpx.AsyncClient）两个参数（`async_client` 字段是 openai 资源对象，不可直接传）
- 单测：`tests/test_chat_agent.py`（工具调用链路/多轮上下文/RAG 来源/use_rag 开关，MockTransport 模拟 DeepSeek tool-calling 响应）、`tests/test_chat_agent_stream.py`（流式 step 去重/token/done/RAG 来源/recursion_limit 断环）、`tests/test_dialogue_engine.py`（双轨/回落/双链路失败礼貌报错）、`tests/test_dialogue_stream.py`（SSE 事件序列/回落/会话 ID 移交）、`tests/test_dify_stream.py`（ThinkFilter 分块过滤/SSE 解析/错误上抛）

## 长期记忆（阶段2 T2：对话记忆写入 + **T3 检索注入**，契约见 doc/long-term-memory.md）

- `app/services/memory_service.py` — 记忆条目 schema + 写入/检索/提取；`doc/long-term-memory.md` 为**权威设计文档与 T3 契约**（变更 schema/接口必须同步该文档与本节）
- **记忆条目 schema（T3 契约）**：每条记忆一个字段组 `{user, fact_type, content, source_conversation_id, timestamp}`；`fact_type` ∈ `preference`（偏好）/`fact`（事实）/`habit`（习惯），非法值归一 `fact`；`content` 为自然语言记忆句（≤200 字符）；存储形态为 user_memory 命名空间内**每条记忆一个 Document**（page_content=content，metadata 携带结构化字段），按 user 隔离
- 公开接口（T3 对齐）：`save_user_memory(user, facts) -> dict`（按 user 写入/覆盖，同 user 同规范化 content 去重更新不膨胀，返回 written/overwritten/total/skipped）、`search_user_memory(user, query, top_k=3) -> List[MemoryEntry]`（user 子集内语义检索，游客/异常返回空）、`count_user_memory(user)`、`extract_facts(query, answer, llm=None)`（LLM 单次提取，可注入 llm 测试）、`extract_and_store(user, cid, query, answer)`（对话后钩子主体）；A2 新增 `list_user_memories(user)` / `delete_user_memory(user, memory_id)`（记忆工具化用，见下方「deepagents 装配层」专节）；B5 新增 `memory_id_of`/`find_memory_by_id`/`count_memories_per_user`（管理端用，见下方「记忆管理 API」专节）
- **游客隔离**：`is_guest_user()` 判定 visitor / guest-* / 空串为游客；游客（含匿名）**不写长期记忆**（跳过 LLM 调用与写入）
- **提取钩子**：主链路（deepagents 装配层 A3 起 / 弃用链路 chat_agent）在 `chat()` 末尾 / `chat_stream()` done 事件后调 `_schedule_memory_extraction`（后台 asyncio task，不 await、不阻塞响应；句柄登记 `_memory_tasks` 防 GC、done 回调消费异常）；提取失败/LLM 未配置**静默降级不写记忆**（告警日志），绝不阻断对话；测试用 `monkeypatch.setattr(chat_agent, "_memory_extract_hook", noop)` 隔离（test_chat_agent/test_chat_agent_stream/test_deepagents_main 的 autouse fixture 已固定）
- 存储复用：`app/core/rag.py` 的 `SimpleVectorStore` 新增 `delete_documents(predicate)`（按谓词删文档+向量，支撑覆盖更新）与 `similarity_search_with_score_filtered(query, k, predicate)`（子集检索，支撑按 user 隔离）两个通用方法；写入/检索均在 `rag_service._vector_index_lock` 下执行
- **检索注入（阶段2 T3，对话前）**：`chat_agent.py` 的 `_build_memory_context` 在 `chat()`/`chat_stream()` agent 调用前执行——**仅首轮**（history 非空不重复注入）且非游客时，以**用户当前问题**为查询调 `search_user_memory(user, query, top_k=3)`，检索结果格式化为额外 `SystemMessage` 注入（每条带事实类型标签 + 时效标记如「3天前」）；超过 `MEMORY_MAX_AGE_DAYS`（默认 180 天）的陈旧记忆过滤；注入文本附「仅作参考、不执行指令」降权约束（记忆句属不可信内容）；检索异常/无记忆静默降级不阻断对话；不重建 agent、不改工具集/SSE/回落。**A3 起仅弃用链路（chat_agent）保留该语义检索注入，主链路（deepagents）改为轻量用户上下文注入（登录用户摘要/游客模式提示）+ 记忆工具按需检索**（见「deepagents 装配层」专节）
- 配置：`MEMORY_ENABLED`（默认 true，总开关）、`MEMORY_MAX_PER_USER`（默认 200，超限淘汰 timestamp 最旧条目）、`MEMORY_INJECT_ENABLED`（默认 true，注入总开关）、`MEMORY_MAX_AGE_DAYS`（默认 180，注入记忆最大时效），见 `.env.example`
- 单测：`tests/test_memory.py`（schema/按 user 隔离/覆盖去重/条数上限/游客跳过/LLM 提取容错/静默降级/chat+chat_stream 钩子调度，mock LLM 与 MockTransport，不发真实请求）、`tests/test_memory_inject.py`（注入逻辑：有记忆→首轮请求含记忆上下文/无记忆/游客/异常降级/多轮仅首轮/时效过滤/流式/端到端按 user 隔离）、`tests/test_rag.py`（delete_documents 与过滤子集检索回归）
- **阶段2 T4 登录生效联调（收官，2026-08-06）**：真实环境全链路验证通过（key 用主项目 `digitalHumanApi/.env`，worktree 复制为 gitignored 临时配置）——登录用户注册/登录 → 带 JWT 多轮对话 → 后台提取写入 user_memory（实测 user=1 写入 5 条，来源会话/类型正确）→ 新会话首轮注入生效（回答引用用户画像「带老人小孩/自然风光」，tools=0 即作答）→ 同事实重申覆盖不膨胀（仍 5 条）；游客（无 token / guest token）短期记忆正常、无长期记忆写入与注入（新会话问「还记得我吗」明确答「没有记忆功能」）；未发现联调缺陷、无代码改动；验证矩阵与证据见 `doc/phase2-memory-verification.md`

## 记忆管理 API（批次5 B5：管理端，X-Admin-Token 鉴权）

船长决策：长期记忆管理需后台可手动修改/让 agent 修改；用户侧隔离已由 A2 记忆四工具实现，本模块补**管理端**能力（管理员例外：可查看/新增/修改/删除任意注册用户记忆；游客无记忆——查询空、新增 400）。

- `app/routers/memory_admin.py`（前缀 `/api/admin/memories`，契约 `doc/memory-admin-api.md`）：`GET /users`（注册用户列表 id/username/nickname/**memory_count**/last_login_at，keyword/page/page_size）、`GET ?user={id}`（按用户列记忆，含 memory_id/fact_type/content/source_conversation_id/timestamp）、`POST`（新增，user 须为注册用户，游客 400/未注册 404）、`PUT /{memory_id}`（修改=**删除后重建**：保留 user/source_conversation_id，timestamp 刷新、memory_id 更新为新建 uuid，content/fact_type 部分更新至少其一）、`DELETE /{memory_id}`（跨用户按 id 删除，uuid 与 legacy 派生 id 均可）
- **审计（日志先行）**：删除/修改/新增记 `app.routers.memory_admin` logger（`operator=admin` + 目标 user/memory_id/内容摘要截断 50 字符）；DB 审计表未引入（需 Alembic 迁移，当前频次低日志可追溯）
- **输入校验（schema 层先 strip）**：`content`/`user` 纯空白 422、首尾空白自动归一化（`" 1 "` → `"1"`，查询参数同规）；保证修改「先删后建」在任何删除前完成校验（防空白内容先删后失败的数据丢失与带空白 user 孤儿条目）
- **memory_service 新增公开接口**（复用不重实现）：`memory_id_of(entry)`（对外 memory_id 单一派生规则：uuid，旧条目 `legacy:{归一化 content}`，deepagents `_entry_to_json` 同规则）、`find_memory_by_id(memory_id)`（跨用户查找，含 legacy 兼容）、`count_memories_per_user()`（单次持锁批量统计）；`_save_user_memory_sync` 透传传入 memory_id（管理端预生成 uuid 落库，既有调用方行为不变）；`_delete_user_memory_sync` 匹配改为 `_doc_matches_memory_id` 统一走 memory_id_of（原 legacy 匹配逻辑等价）
- 测试：`tests/test_memory_admin.py`（21：鉴权 401 全端点/用户列表含 memory_count/按用户列记忆/游客空/legacy 派生 id 删除/新增校验 422/游客 400/未注册 404/空白内容 422 拒绝（含修改先删后建不丢数据）/user 空白归一化/修改部分更新/修改后旧 id 失效/跨用户删除/404/审计日志）
- **前端（批次5 B5 前端任务）**：`digitalHumanVue/src/api/memory.ts`（API 封装：fetchMemoryUsers/fetchUserMemories/createMemory/updateMemory/deleteMemory，鉴权头走 `adminHeaders.ts` 双凭据）+ `src/types/memory.ts`（AdminUserItem/AdminMemoryItem 等）+ `src/views/admin/MemoryView.vue`（路由 `/admin/memories`，菜单「记忆管理」）：双栏布局——左栏用户列表（keyword 搜索防抖 + 分页，memory_count 徽标），右栏选中用户记忆列表（fact_type 徽标/content/时间/来源会话，编辑/删除确认）；新增/编辑共用弹窗（fact_type 下拉 + content textarea，maxlength 200 + 前端校验）；空态「该用户暂无记忆（游客/匿名用户无长期记忆）」；保存后刷新记忆列表与用户条数统计

## 管理员 agent（批次6 B4：deepagents 装配模式管理端变体，X-Admin-Token 鉴权）

船长决策：独立管理员 agent，可对项目数据库直接调整、发布公告等；删除类操作二次确认 + 审计日志；用户侧隔离不适用（跨用户例外）。性能前提：后台页打开才活跃、不常驻。

- `app/services/admin_agent/`：`agent.py`（装配 + chat/chat_stream）、`tools.py`（管理工具集 + 两阶段二次确认执行器 + 审计）、`confirm.py`（待确认项存储）；路由 `app/routers/admin_agent.py`（`POST /api/admin/agent/chat` 非流式 + `/chat/stream` SSE，X-Admin-Token 鉴权，LLM 未配置 503/error 事件，无 Dify 兜底）
- **装配复用 A2/A3 模式**：`ensure_harness_profile`/`_MAX_AGENT_STEPS`/`_AGENT_TIMEOUT`/`_AGENT_MAX_TOKENS` 直接从 deepagents.harness 复用（同一 provider 注册、排除内置 9 工具）；ToolCallLimitMiddleware 同配置；agent 缓存（构造 ~24s）；SSE 契约与用户侧一致（step/token/done，suppress_tokens + 按 call id 去重，管理端步骤可视化零适配）；模型同 opencode-go（llm.py 工厂）
- **管理工具集**（全部复用既有 service，工具调用前经 `InjectedState("is_admin")` 校验 admin 上下文，非管理员直接抛错拒绝）：知识库查询/增/改/删（knowledge_service）、记忆按用户查询/删（memory_service，管理员跨用户例外）、公告增删改查/发布下架（announcement_service，创建默认草稿、发布后才在首页/访客端可见；删除为两阶段二次确认）、运营统计（admin_service 聚合 + DB 直查，只读）、RAG 重建索引（rag_service documents+knowledge 双命名空间）、首页数据（home_service，只读）
- **两阶段删除契约（confirm.py）**：删除类工具首次调用只登记待确认项（`confirm.create_pending` 生成 operation_id + 目标摘要，进程内短 TTL 默认 120s，`ADMIN_AGENT_CONFIRM_TTL_SECONDS` 可配），不执行删除；模型需再次调用 `confirm_operation(operation_id)`（`confirm.consume` 取出即删防重放）经 `_CONFIRM_EXECUTORS` 派发实际执行（执行前重新定位目标，已被他人删除如实报告）
- **审计**：全部写操作（增/删/改/发布下架/重建索引）经 `tools._audit_log` 记 logger（`operator=admin-agent` + 工具名 + 目标 + action + 结果），与 B5 风格一致
- **无会话持久化**：每请求独立（不落短期/长期记忆，不污染用户侧数据）；conversation_id 仅透传回显（空串生成新 uuid）
- 测试：`tests/test_admin_agent_confirm.py`（7）+ `test_admin_agent_tools.py`（29，含两阶段全流程/越权拒绝/审计/公告增删改）+ `test_admin_agent.py`（8，装配/SSE 契约/缓存）+ `test_admin_agent_api.py`（7，鉴权 401/非流式/SSE）；新增 43 用例，全量 529 绿（pytest 收集 529：497 测试函数 + 34 参数化用例）
- **前端（批次6 B4 前端任务，2026-08-09）**：`digitalHumanVue/src/views/admin/AdminAgentView.vue`（路由 `/admin/agent`，菜单「管理员 Agent」）+ `src/api/adminAgent.ts`（SSE 流解析：fetch ReadableStream + TextDecoder 增量解码，统一剔 \r 后按 `\n\n` 分帧、按行取 `data:` 前缀解析 JSON，兼容 `\r\n`；非流式 `chatWithAdminAgent` 备用）+ `src/types/adminAgent.ts`；页面：token 逐字渲染（marked+DOMPurify，同知识库页）+ 步骤气泡（🔍 样式与 ADH 一致）+ done 收尾；**二次确认前端闭环**——`extractPending` 用非 hex 字符边界正则匹配 32 位 hex 提取 operation_id（联调实测 agent 以反引号包裹，如「操作 ID：`cb64...`」，勿用标点白名单），气泡内「确认执行/取消」按钮：确认注入文本消息 `确认执行 operation_id: xxx` 自动发送（后端无会话持久化，新请求内 agent 调 confirm_operation 闭环；取消仅本地标记，待确认项 120s 自动过期）；**确认回执防误判**：上一条用户消息为确认注入时，本条回答（agent 常回显 operation_id）不再解析待确认项，避免虚假二次确认框（消息模型 `isConfirm` 标记）；conversation_id 存页面状态跨轮透传；Enter 发送/Shift+Enter 换行（`e.isComposing` 防中文输入法误触发）；**约束（实测）**：公告新建默认草稿（create_announcement 支持 status=published 一步发布上首页）；知识库演示建议「新增讲解词 → 删除（二次确认）」组合

## 安全加固（批次6 C3：认证限流 + CORS 白名单 + 管理端 RBAC）

船长决策的企业级改造项（演示环境 192.168.16.132 本机宿主机访问，加固兼顾演示便利不锁死本机）：

- **认证端点限流（防爆破）**：`app/core/rate_limiter.py` — 进程内 `AuthRateLimiter`（零新依赖，threading.Lock 保护），**仅作用于 `/api/v1/auth/login|register|guest`**（auth.py 路由内接入，不误伤对话等正常接口）；按 **IP + 账号双维度**（key=`{ip}|{account}`，guest 无账号退化为纯 IP）；滑动窗口失败计数（默认 5 次/60s）达阈值进入冷却（默认 900s），冷却期内 429（detail 含剩余秒数 + `Retry-After` 头，前端可展示）；**登录/注册成功自动清零**该维度计数（防误伤正常用户）；冷却期内重复失败不延长冷却；guest 端点无失败场景 → 纯 IP 固定窗口频率限制（默认 30 次/分钟，防 token 刷取），429 剩余秒数随窗口位置精确计算（非固定阈值，前端可准确倒计时）；**XFF 取末位**（`client_ip` 取 `X-Forwarded-For` 最后一个值，防伪造前置代理头）；**状态有界**（max_keys 默认 10000，超限先清扫过期再逐出最旧，防 key 轮换内存膨胀）；时钟可注入（测试可控）；`AUTH_RATE_LIMIT_ENABLED=false` 整体关闭；测试替换 auth 模块 `_limiter` 为小参数实例控制阈值
- **CORS 白名单**：`CORS_ORIGINS`（逗号分隔，`app/core/config.py::_parse_cors_origins`）；**默认值覆盖演示环境**——localhost/127.0.0.1/192.168.16.132 × 5173/8001/3000（Vue dev / ADH 前端 / ADH 后端跨端口），生产显式收紧；**credentials 与 `*` 互斥（实现细节坑）**：Starlette 在 `allow_credentials=True` 且 origins 含 `*` 时会把 `*` 退化为回显任意 origin = 全放开，故 Settings validator 启动即报错拒绝（`CORS_ALLOW_CREDENTIALS=false` 才允许 `*`）；credentials 头在 allow_credentials=True 时**无条件发送**（Starlette 行为），非白名单 origin 判定看的是 **Access-Control-Allow-Origin 缺失**（浏览器据此拒绝跨域读取），测试断言不要依赖 credentials 头
- **管理端 RBAC 角色模型**：`app/routers/deps.py` — 角色常量 `ROLE_ADMIN`/`ROLE_OPERATOR`（层级 operator < admin）；`verify_admin_token` **保留兼容**（返回角色字符串替代原样回显 token，既有调用方 `_: str = Depends(...)` 忽略返回值零影响）：`ADMIN_API_TOKEN` 恒为 admin（现有部署行为不变），`ADMIN_TOKENS`（新配置，`"token1=admin,token2=operator"` 逗号分隔，裸 token 视为 admin，非法角色忽略告警）命中优先；`require_role("admin")` 依赖工厂（先校验 token 再比角色，权限不足 **403** 与未授权 401 区分，detail 提示所需角色）；**敏感度分级落地**：知识库删除（`knowledge.py DELETE /{kb_id}`）与记忆删除（`memory_admin.py DELETE /{memory_id}`）要求 admin，查询/公告发布/知识库增改维持现状（operator 可做），admin_agent 维持现状（两阶段确认 + 审计已兜底）；前端暂不改（仍传同一 token）
- 测试：`tests/test_rate_limit.py`（16：触发/冷却/恢复/滑动窗口/成功清零/维度隔离/guest 频率/不延长/XFF 取末位/状态有界/guest 剩余秒数精确/429 集成/注册计数/误伤/开关）+ `tests/test_cors.py`（9：默认白名单/解析/白名单放行/非白名单拒绝/预检/主应用冒烟/credentials 互斥）+ `tests/test_rbac.py`（12：解析/兼容/401/operator 查询放行/删除 403/公告发布/双角色）；全量 566 绿（基线 529 + 37）；conftest autouse fixture `_reset_auth_rate_limiter` 每测试重置限流器（防既有认证测试被 429 误伤）
- 配置见 `.env.example`（CORS_ORIGINS/CORS_ALLOW_CREDENTIALS/ADMIN_TOKENS/AUTH_RATE_LIMIT_*/AUTH_GUEST_MAX_PER_MINUTE）；docker-compose 已透传

## AI 运营分析（LangChain + OpenAI，可回退）

- `app/services/analytics_service.py` + `app/routers/analysis.py` — `GET /api/v1/analysis/overview`（`?days=1-365`）、`GET /trends`、`POST /run-now`
- **overview 结果缓存（2026-08 实机问题 9 提速）**：`get_ai_overview` 进程内 TTL 缓存（`OVERVIEW_CACHE_TTL_SECONDS=600`，键含 days）+ 同键并发 in-flight 合并（asyncio 任务共享，防刷新堆积并发 LLM 分析）；命中返回深拷贝快照并置 `cached=True`（响应新增 `cached` 字段；`analysis_timestamp` 保持实际分析时间）；**空数据结果（total_dialogues==0）不缓存**（空库查询毫秒级，防新数据入库后 TTL 内被旧空结果遮蔽）；TTL 过期自然失效；单进程部署内存缓存不共享可接受；测试隔离 `analytics_service.reset_ai_overview_cache()`（conftest autouse fixture）；前端刷新按钮已有 `:disabled="aiLoading"` 防重复点击，另加「已等待 Ns」计时提示（`aiElapsed`）
- 使用 `langchain_openai.ChatOpenAI` 生成分析；**LLM 失败/未配置/输出不可解析时自动回退纯 DB 统计**（确定性摘要：满意度方向由趋势前后半段均分对比判定、热门问题来自 concern_tags、高峰时段 DB 精确统计、运营建议由热点/高峰推导），任务与接口均不因 LLM 故障中断
- **降级标记**：`AIOverviewResponse.llm_analysis`（true=LLM 产出；false=DB 统计兜底），`ai_summary` 尾部附「（数据库统计模式，LLM 分析不可用）」供前端/管理后台识别
- **超时参数（实测 2026-08-06，opencode-go + deepseek-v4-flash）**：分析 prompt 单次调用约 55-90s（端点延迟高且波动大），`LLM_REQUEST_TIMEOUT=180`（httpx）/ `LLM_ANALYSIS_TIMEOUT=240`（asyncio.wait_for 总上限）——原 60s/90s 会稳定超时误降级；详见 `doc/phase3-analytics-verification.md`；**部署拓扑约束**：docker 部署下 nginx `/api/` 的 `proxy_read_timeout` 必须 ≥ 分析超时预算（`nginx.conf` 已配 300s），否则管理后台 overview 在代理层被 504 掐断（直连 8000 不受影响）
- 配置：`OPENAI_API_KEY`/`OPENAI_API_BASE`/`OPENAI_MODEL`（**当前部署单用 opencode-go 端点**，与 `LLM_*` 同一 key，见 `digitalHumanApi/.env`）；未配置 key 时跳过 LLM 直接 DB 统计
- 定时任务 `app/tasks/analysis.py::analysis_loop`（每 300s）做**本地 LLM 量化评估**（`batch_evaluate` → `evaluation_service`，Evaluation 表写入，管理后台数据源；Dify Workflow 评估已阶段4 移除），任务级 try/except 保证故障持续运行；LLM 分析由接口按需触发（无独立调度）
- 测试：`tests/test_analysis.py`（LLM 成功/失败/无 key 三种降级路径 + 任务韧性）；conftest 固定 `OPENAI_API_KEY=""`（同 CI，防本地 worktree .env 真实 key 污染测试触发慢速网络调用）

## LLM token 记账（批次7 C5：统一埋点 + 后台批量写库 + 报表 API）

船长决策：token 记账放后台、分模型用量、可查询值详细一点（GitHub 用量报表风格：趋势图 + 明细表）；opencode-go 订阅制可只记 token 不记钱，但保留 cost 列与单价映射能力。权威契约：`doc/usage-tracking.md`。

- **统一埋点**：`app/services/usage_tracker.py` 的 `UsageTrackingChatOpenAI`（ChatOpenAI 子类）在模型调用层拦截（_generate/_agenerate/_stream/_astream 四入口）；`llm.py` 工厂在 `USAGE_TRACKING_ENABLED`（默认 true）时构造该子类——**所有经 get_chat_llm/create_chat_llm 的链路自动记账**（analytics_service 的 OPENAI_* 独立构造不在范围）；非流式取 llm_output.token_usage，流式经 `stream_options.include_usage`（`USAGE_STREAM_USAGE` 开关，opencode-go/DeepSeek 实测支持）取最后一个带 usage_metadata 的 chunk；异常/无 usage 不记账（宁缺毋滥）；不改变调用行为
- **链路/用户上下文**：`usage_context(chain, user)` contextmanager（contextvars，to_thread/langgraph 图内子任务自动继承）由调用点标注——对话（deepagents chat/chat_stream，弃用 chat_agent 同）、推荐、评估、压缩（compaction）、管理员agent（user=admin）、记忆提取（extract_facts）、规划（弃用 planner）；意图分类是规则匹配无 LLM 不需埋点
- **批量 flush**：record_usage 仅入队（queue.Queue 零阻塞）；lifespan 注册 flush_loop 定时批量写库（`USAGE_FLUSH_INTERVAL_SECONDS`=5）；队列满（`USAGE_QUEUE_MAX_SIZE`=2000）兜底 run_coroutine_threadsafe 立即 flush + 丢最旧；关闭时 shutdown() 最后 flush；写库失败告警丢弃（failed 计数）；开关关闭零开销
- **llm_usage 表**：model/input/output/total_tokens（本地求和）/duration_ms/chain/user/cost/created_at；迁移 `c5f1a2b3d4e5`（升级建表+索引/回退删表）；`MODEL_PRICES` 单价表（"模型:输入价,输出价" 元/百万 tokens，分号分隔多项；留空 cost=NULL）
- **报表 API**（`app/routers/usage.py`，前缀 /api/admin/usage，X-Admin-Token，operator 可查）：`GET /overview`（start/end ISO + granularity=day|week|month → totals + 趋势 series + by_model/by_chain，cost 未配置单价为 null）、`GET /details`（分页 + model/chain/user 筛选，created_at 倒序）、`GET /export`（CSV 导出，UTF-8 BOM 兼容 Excel）；时间统一 UTC naive；参数非法 422
- 测试：`tests/test_usage_tracker.py`（19：队列/上下文/flush/开关/队列满兜底/关闭/单价表）+ `tests/test_llm_usage.py`（14：MockTransport 非流式/流式埋点、无 usage 不记、请求体不变、stream_options 开关、deepagents 端到端对话/流式记账）+ `tests/test_usage_api.py`（17：聚合正确性/分页筛选/CSV/鉴权 401/operator 放行/422）；conftest autouse `_reset_usage_tracker` 每测试重置队列；全量 618 绿（基线 566 + 52）
- **前端（批次7 C5 前端任务，2026-08-09）**：`digitalHumanVue/src/views/admin/UsageView.vue`（路由 `/admin/usage`，菜单「成本仪表盘」）+ `src/api/usage.ts`（fetchUsageOverview/fetchUsageDetails/exportUsageCsv，鉴权头走 `adminHeaders.ts` 双凭据；导出用原生 fetch 取 Blob + 隐藏 `<a>` 触发下载，不走 JSON 封装）+ `src/types/usage.ts`；页面：时间范围预设（今天/近7天/近30天/自定义日期输入）+ ECharts 单 Y 轴趋势（Token 用量柱状，日/周/月粒度，复用 registerECharts 零新依赖；船长 2026-08-11 指示移除调用次数折线）+ KPI 卡片（次数/Token 总量含输入输出拆分/平均耗时/费用，cost=null 显示—）+ 按模型/按链路聚合表（占比条形）+ 分页明细（模型/链路筛选下拉由 overview 聚合派生）+ CSV 导出（文件名带日期范围）；**时间语义**：请求参数带本地时区偏移 ISO datetime（`toLocalISO`），预设取「明日 00:00」为 end 覆盖当天全天（后端 end 开区间）；created_at 为 UTC naive，展示前补 `Z` 转本地；空数据态优雅（各区块空态文案，图表区显示「暂无趋势数据」）；联调实测（真实后端+真实浏览器）：6-11 条记录全链路可用、CSV 下载 BOM 正常、空态不崩

## 数据库迁移（Alembic，批次2 地基 C1）

- `alembic.ini` + `migrations/`（env.py 为 **async 模板**：项目是 SQLAlchemy 2.0 async engine（create_async_engine + aiosqlite），在线迁移经 `asyncio.run` 包装）；初始迁移 `04436fda61c5` 为全量基线（DDL 与 ORM create_all 逐字节一致，见 `tests/test_migrations.py::test_upgraded_schema_matches_orm_metadata`）
- **启动自动迁移**：`app/core/migrations.py::run_migrations()`（main.py lifespan Phase 1 替代 create_all；独立 CLI backfill.py 同用）三态分流——空库 `upgrade head` 建齐 / **遗留库（有业务表无版本表）先幂等 create_all 补建缺失业务表再 `stamp head` 基线化（不丢数据）** / 版本化库 `upgrade head`；迁移失败回退 create_all（幂等）不阻断启动；Alembic 命令为同步 API 且 env.py 内部 asyncio.run，必须 `asyncio.to_thread` 执行
- URL 解析优先级：`alembic -x db_url=...` > ini `sqlalchemy.url`（留空；测试 Config.set_main_option 覆盖）> `settings.DATABASE_URL`；env.py 已显式把 digitalHumanApi/ 加入 sys.path
- 开发流程：改 models → `alembic revision --autogenerate -m "..."`（草稿必须 review，补 downgrade）→ `alembic upgrade head` 验证 → `pytest -q` → 合并；SQLite 改列用 `op.batch_alter_table`（已开 `render_as_batch=True`），禁止手写裸 ALTER TABLE
- 权威文档：`doc/alembic-migrations.md`；测试：`tests/test_migrations.py`（9 用例：空库建齐/DDL 等价/遗留库 stamp 不丢数据/版本化库幂等/run_migrations 三态/遗留库缺表补建/公告表升级回退）

## 多环境配置 + 数据库备份（批次8 C7）

- **多环境三档（dev/staging/prod）**：config.py 新增 `APP_ENV` 字段（默认 dev，非法值启动即报错 `_validate_app_env`），启动日志与 `GET /api/v1/health`（HealthResponse.app_env）透出；`.env.example` 按功能分区（核心/对话 LLM/Embedding/运营分析/Dify/高德/记忆/用量/安全/SSH）并给每个变量标注【适用：dev/staging/prod/全部】；权威文档 `doc/deployment-environments.md`（三档差异表：DEBUG/CORS 白名单/限流/JWT_SECRET/凭据必填项 + 切换指引）；docker-compose.yml 已透传 `APP_ENV`（根目录 .env.example 同步）；测试 `tests/test_env_config.py`（默认值/三档合法/非法拒绝/健康检查透出）
- **数据库备份**：`digitalHumanApi/scripts/backup_db.sh`（bash + sqlite3 CLI `.backup` 在线安全备份——快照语义不中断业务，区别于裸 cp 可能拷到写一半的库；时间戳命名 `<库>_backup_YYYYmmdd_HHMMSS.db` + 同秒冲突自动 -2/-3 后缀 + 保留最近 N 份默认 7 + 备份后自动校验 integrity_check 与表数量，失败删备份退出非零；位置参数 > 环境变量 > 默认值；shellcheck 干净）；权威文档 `doc/database-backup.md`（.backup vs 文件复制对比、恢复验证步骤：拷贝备份→integrity_check→表计数→抽查数据；Docker volume 路径：`./digitalHumanApi:/app` 下备份脚本/目录路径即容器内路径 `/app/...`，可宿主机直接调度）；测试 `tests/test_backup_script.py`（5 用例：建库→备份→恢复验证/保留份数清理/缺失库/非法 KEEP/无临时文件残留；未装 sqlite3 CLI 自动 skip）
- 新增配置变量需同步三处：digitalHumanApi/.env.example + 根目录 .env.example + docker-compose.yml environment 透传（见 AGENTS.md「环境配置」坑位）

# Commands

| 命令 | 位置 | 用途 |
|------|------|------|
| `uvicorn app.main:app --reload` | `digitalHumanApi/` | 启动 API 开发服务器 (端口 8000) |
| `pytest -v` | `digitalHumanApi/` | 运行测试套件 |
| `alembic upgrade head` | `digitalHumanApi/` | 升级数据库 schema 到最新（启动时自动执行，见 doc/alembic-migrations.md） |
| `alembic revision --autogenerate -m "描述"` | `digitalHumanApi/` | 按模型变更生成迁移草稿（review 后提交） |
| `npm run dev` | `digitalHumanVue/` | 启动 Vue 开发服务器 (端口 5173) |
| `npm run build` | `digitalHumanVue/` | 生产构建 |
| `docker-compose up -d` | 根目录 | Docker Compose 部署 (api + web + nginx) |

# Business Context

- **Visitor 端**: 访客通过数字人 iframe 对话（deepagents 本地链路，对话直接落库 dialogues 表）；Dify 历史对话由轮询抓取（数据源之一）；用户体系（注册/登录/游客）见上方「用户认证」专节，登录用户享**长期记忆**（阶段2 T2 已实现：对话后提取用户事实/偏好写入 user_memory 向量库，按 user 隔离；游客不写，见「长期记忆」专节与 doc/long-term-memory.md）
- **Admin 端**: 管理后台展示情感趋势、满意度、热点关注和景点热度（数据来自本地 LLM 量化评估——Evaluation 表，阶段4 起不再依赖 Dify Workflow），以及知识库管理（讲解词/FAQ/资料 CRUD + 批量导入）
- **数据隔离**: 量化评估覆盖**所有未评估对话**（`batch_evaluate` LEFT JOIN 无 visitor 过滤，本地链路落库的游客/登录用户对话均入评估），无多租户、无按用户的数据隔离（JWT 认证仅标记对话 user，不隔离数据，见上方「用户体系」）

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
- 凭证（Dify Key、OpenAI Key、`ADMIN_API_TOKEN`、腾讯云/MiniMax/Qwen（DashScope）凭证）只从 `.env` / 云平台控制台获取，绝不写入代码库；仓库内 `.env.example` 仅含占位符
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
| **LangChainAgent** | Agent | `configs/agents/langchainAgent.yaml` | **默认引擎**，走本后端 `/api/v1/dialogue/chat`（deepagents 装配层主链路，A3 起；后端侧异常自动回落 Dify）；SSE 流式正常；工具调用步骤可视化（见下） |
| **DifyAgent** | Agent | `configs/agents/difyAgent.yaml` | 保留可切换（fallback 不丢），直连 Dify Chat Agent |
| **QwenAPI** | TTS | `configs/engines/tts/qwenTTS.yaml` | **默认引擎（2026-08-07 从 MiniMax 迁移，DashScope WebSocket）**：dashscope SDK `SpeechSynthesizer`，模型 `qwen-audio-3.0-tts-flash`、音色 `qwen-audio-3.0-tts-flash-longtongxuxian`（龙彤旭弦，活泼灵动女声），mp3→wav 输出；key 优先读环境变量 `QWEN_API_KEY`（yaml 留空占位不入库）；SDK 初始化要求先设全局 `dashscope.api_key`（实测）；真实调用与 mock 单测均通过（`test/test_qwen_tts.py` 17 用例）。**「说话说一半就终止」修复（2026-08-11）**：改走 SDK 回调流式路径（ResultCallback）自研等待循环——`STALL_TIMEOUT`（默认 20s，无音频帧停滞即快速失败，半开连接不再无限阻塞）+ `TIMEOUT_MS`（默认 60s）总超时兜底 + task-failed 部分音频整体丢弃 + `RETRIES`（默认 1）重试 + finally 兜底 close；前端 `chatbot.tsx` 按序音频队列同步改为失败/超时句记 null 出队跳过（不再卡死后续句子）。**「大量句子被跳过」修复（#70-5b，2026-08-11）**：根因=前端按标点断句后多句并发请求合成 → DashScope `Throttling.RateQuota` 限流 + WebSocket 连接风暴（SDK 高频报「connection is not established」）；修复=模块级 `asyncio.Lock`（`_TTS_SYNTH_LOCK`）串行化合成（同刻最多一个在途连接，**勿用 threading.Lock——阻塞 acquire 卡死事件循环死锁**）+ 重试退避 `RETRY_BACKOFF`（默认 0.5s）；前端 `chatbot.tsx` TTS 改 promise 链串行化（前一句完成再发下一句，单句失败 catch 记 null 不连跳）+ 段级超时 30s→45s（长于后端停滞×重试+退避）。yaml 可调优 |
| **MiniMaxAPI** | TTS | `configs/engines/tts/minimaxTTS.yaml` | 保留可切换（`Chinese_sweet_girl_nv1` 音色，替代 difyTTSWorkflow） |

### 凭证位置（不存真实值）
- **腾讯云 ASR**: AK/SK 来自腾讯云控制台下载的 SecretKey.csv；APPID、REGION `ap-guangzhou` 配置在 tencentAPI.yaml
- **LangChainAgent**: `LLM_API_URL` 指向本后端（默认 `http://127.0.0.1:8000`），无鉴权场景可不填 token；`LLM_API_USER` 默认 `visitor`（可选 JWT token 走 Bearer 透传；**嵌入模式 URL `auth_token` 参数由 web 端 `app/lib/embedAuth.ts` 自动注入 `LLM_API_TOKEN`**，未登录不注入维持 visitor 兜底，见下方 SSE 契约与「ADH 注意事项」嵌入身份透传条目）
- **Dify Agent**: URL `http://<dify-server-ip>/v1` + `app-` 开头的 Key + User，由 Dify 部署方提供（切换回 DifyAgent 时填写）
- **QwenAPI TTS**: API Key 从环境变量 `QWEN_API_KEY` 读取（部署时 export；yaml 的 API_KEY 留空占位）；如改用配置文件则填 DashScope API Key（`sk-` 开头）
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
- `configs/engines/asr/tencentAPI.yaml`、`configs/engines/tts/qwenTTS.yaml`（新建，默认 TTS）、`configs/engines/tts/minimaxTTS.yaml`、`configs/agents/difyAgent.yaml`、`configs/agents/langchainAgent.yaml`（新建）— 引擎凭证/参数
- `digitalHuman/engine/tts/qwenTTS.py`（新建，2026-08-07 Qwen/DashScope TTS 引擎，注册名 QwenAPI）+ `digitalHuman/engine/tts/minimaxTTS.py`（新建）+ `digitalHuman/engine/tts/__init__.py` — TTS 引擎及注册
- `digitalHuman/engine/asr/tencentASR.py` — 已移除 `req.AppId` 行
- `digitalHuman/agent/core/langchainAgent.py`（新建，2026-08-06 联调修正 SSE 契约；2026-08-07 步骤透出；2026-08-09 路线透出）— LangChain 引擎：调本后端 `/api/v1/dialogue/chat`，SSE 解析 **`type` 字段**（`token`（text 增量，经 `ThinkFilter` 过滤后产出）/`step`（**透出为 `StepMessage`（tool/label）混入 run() 产出流**，供 agentApi 编码步骤帧、前端渲染，文本流不变）/`done`（携带 conversation_id 作流结束；**2026-08-09 B3 起携带 route_data（plan_route 工具结果信封）透出为 `RouteMessage`**）/`error`（`fallback=true` 时仅告警不中断、继续消费 Dify 兜底输出；`fallback=false` 抛错）），兼容旧 `event: message/answer` 形态；非流式解析 ChatResponse JSON（answer/conversation_id），见代码头注释
- `digitalHuman/utils/protocol.py` — 新增 `StepMessage`（tool/label 字段，与 TextMessage/AudioMessage 同体系）+ `RouteMessage`（data=路线信封，B3）
- `digitalHuman/server/agentApi.py` — `/v0/infer` 将产出流中的 `StepMessage` 编码为步骤帧（`\x02{json}\x03`，json 含 tool/label）、`RouteMessage` 编码为**路线帧**（`{"type": "route", "data": ...}`，B3）透出给前端；回答文本原样透出
- `digitalHuman/agent/core/difyAgent.py` — 新增 `ThinkFilter`，流式过滤 `<think>` 标签
- `digitalHuman/server/asrApi.py` — DEBUG 音频保存到 `debug_audio/`
- `web/app/lib/comm.ts` — `streamingChat` 新增步骤帧解析（缓冲拼接 `\x02{json}\x03` 帧，兼容 fetch 分块切碎）与 `callbackStep` 回调；**B3 路线帧**（json 含 `type: "route"`）解析与 `callbackRoute` 回调；**#70-5b 修复**：try/catch/finally 保证 `callbackEnd` 在流中断/异常时也触发（不再吞错后永久卡死输入），`releaseLock` 兜底；文本流/索引语义不变（TTS 断句与音频队列依赖）；**嵌入身份透传（2026-08-12）**：`streamingChat` 入口对 settings 幂等注入 `LLM_API_TOKEN`（`getEmbedAuth`，防其它调用路径遗漏，未登录不注入，见 embedAuth.ts）
- `web/app/lib/amap.ts`（新建，2026-08-09 B3）— 高德 JS API 按需加载器：首次需要地图时注入 script（`webapi.amap.com/maps?v=2.0`，key + `window._AMapSecurityConfig`），幂等 Promise、失败可重试；`formatDistance`/`formatDuration`/`parseLocation` 工具函数
- `web/app/lib/embedAuth.ts`（新建，2026-08-12 问题4 修复：长期记忆根因）— 嵌入身份透传：`parseEmbedAuth`（纯函数解析 URL `auth_token`/`visitor_id`，零依赖可单测）+ `getEmbedAuth`（会话级惰性缓存，SSR 安全）；供 chatbot.tsx 构建 settings 与 comm.ts 幂等兜底注入 `LLM_API_TOKEN`；单测 `web/tests/embedAuth.test.ts`（`npm run test:embed-auth`，tsc 编译 + node assert，零测试框架）
- `web/app/lib/newChat.ts`（新建，2026-08-13 实机测试问题3：对话页「新开会话」按钮）— 新开会话交互纯函数 `nextNewChatAction`（零依赖可单测）：进行中（isProcessing/micRecording）→ ignore 不响应；空闲首次点击 → arm 进入轻量确认态（防误触）；确认态再次点击 → confirm 执行；单测 `web/tests/newChat.test.ts`（`npm run test:new-chat`，同 embedAuth 模式）
- `web/app/ui/home/routeCard.tsx`（新建，2026-08-09 B3）— 路线卡片：成功信封渲染 AMap 地图（起点/终点 Marker + 路径 Polyline + 距离/时长摘要 + 步骤列表）；**地图加载失败（网络/白名单/key 未配置）或坐标非法自动降级文字路线**（步骤列表 + 距离时长照常展示，不阻断对话）；失败信封（amap_* 错误码）如实展示
- `web/app/ui/home/chatbot.tsx` — 录音改用原生 `MediaRecorder` + 客户端 webm→WAV（16kHz 重采样）；**实时分段渲染（2026-08-10 船长反馈，替代同日独立气泡方案）**：`store.ts` ChatMessage 增 `type` 字段（text/step/route，缺省 text 兼容既有用法）；token 回调把当前文本片段累积进 `streamingText` 实时逐字渲染；step/route 回调到达时先固化当前片段（append 为 text 消息）再 append 该 step/route 消息，流完成时固化最后片段——**文本按 step 边界切成多个独立气泡、步骤小气泡与路线卡片均按发生顺序固化进 chatRecord 多轮保留（不再清空）**；渲染按 type 分支：step=🔍 小灰气泡（保留既有样式，圆点静态不脉冲）、route=RouteCard、text=Markdown 气泡；连续重复步骤去重（`lastStep` 闭包变量，文本固化后重置）；TTS 断句（基于流式 data 累积）不变；**📍 定位按钮（可选）**：`navigator.geolocation` 填入坐标文本，仅 secure context（HTTPS/localhost）显示（`geoSupported` 探测，局域网 IP 演示环境自动隐藏）；**「工具调用后文字气泡不显示」修复（#70-5b，2026-08-11）**：`comm.ts` `streamingChat` 改 try/catch/finally 保证 `callbackEnd` 在流中断/异常时也触发（旧 catch 吞错后直接返回 → isProcessing 永久 true 输入卡死、已流文本不固化）；实测模型叙述被工具调用在句中截断属后端 suppress 语义泄漏（文本仍完整、按序渲染即正常，**勿在 step 回调做句末感知/暂存步骤——会打乱 text/step 相对顺序，曾致回答后仍渲染步骤**）；前端 TTS 改 promise 链串行化（见 QwenAPI 行）；**新开会话按钮（2026-08-13 实机测试问题3）**：消息区上方右上角「新开会话」入口（嵌入 hide_menu=1 与独立模式一致）——两段式确认（首次点击变「再次点击确认新开」，3s 自动复位）；进行中（流式 isProcessing/录音 micRecording）与取新 id 在途（`newChatPending`）时禁用；执行=清空 chatRecord/streamingText + 重新 `getConversionId`（后端每个 id 独立会话，短期记忆不延续）+ 旧会话 TTS 作废（模块级 `ttsSession` 代际计数——chatWithAI 的 audioAutoStop 分支与新开会话时递增，enqueueTTS 入队前校验代际丢弃旧链音频，勿删，防跨会话播放旧语音）；取 id 在途期间禁输入/发送/麦克风防会话分裂（后端对空 conversation_id 每次新建会话）；**嵌入身份透传（2026-08-12 问题4 修复）**：构建 settings 时以 URL `auth_token` 覆盖 yaml 默认空 token 注入 `LLM_API_TOKEN`（未登录不注入，visitor 兜底无回归，见 embedAuth.ts）
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
- **工具调用步骤可视化（2026-08-07）**: 主后端 SSE step 事件 → ADH `langchainAgent.py` 透出 `StepMessage` → `agentApi.py` 编码 `\x02{json}\x03` 步骤帧 → `comm.ts` 解析 → `chatbot.tsx` 消息区渲染（🔍 步骤小气泡按序追加）；真实环境实测（真实主后端 + 真实 LLM）触发规划类对话产出 6 个步骤帧（plan→search_knowledge→search_spots→filter_by_tags→recommend_routes→summarize），文本流不受影响；步骤帧协议见 `agentApi.py` 与 `comm.ts` 注释
- **路线卡片（2026-08-09 B3）**: 主后端 done 事件 `route_data`（plan_route 工具结果信封，契约见主仓库 doc/amap-gps-api.md）→ ADH 透出 `RouteMessage` → 路线帧（`{"type":"route","data":...}`）→ `comm.ts` callbackRoute → `chatbot.tsx` 渲染 RouteCard；高德 JS key 环境变量按 Next.js 约定命名 `NEXT_PUBLIC_AMAP_JS_KEY`/`NEXT_PUBLIC_AMAP_SECURITY_CODE`（对应任务约定的 VITE_AMAP_* 名，写入 web/.env 不入库）；**无 key/加载失败/失败信封均不崩**——降级文字路线或错误提示；联调已验证（Playwright 冒烟：步骤气泡+路线卡片+文字兜底+AMap 脚本按需注入，无 key 不发起外网请求）
- **嵌入身份透传（2026-08-12 问题4 修复，长期记忆根因）**: 主站 5173 ChatView iframe URL 带 `auth_token`（登录用户/游客 JWT）与 `visitor_id`；web 端 `app/lib/embedAuth.ts` 会话级解析并缓存，chatbot.tsx 构建 settings 时注入 `LLM_API_TOKEN`（comm.ts 幂等兜底）→ ADH 后端 `langchainAgent._build_headers` 加 `Bearer ` 前缀 → 主后端 `resolve_dialogue_user` 解析真实用户（对话落库 visitor_id=用户 id、长期记忆 save/extract/recall 按 user 隔离生效）；未登录（URL 无 auth_token）不注入，行为与现状一致（visitor 兜底，无回归）；单测 `web/tests/embedAuth.test.ts`（`npm run test:embed-auth`）
- **引擎池容错（2026-08-13 实机测试问题1 修复）**: `digitalHuman/engine/enginePool.py` 的 `setup()` 单引擎初始化失败不再拖垮整个池——默认引擎（`DEFAULT` 字段）失败仍显式抛错（服务核心不可用），**非默认引擎**失败降级：ERROR 日志（含引擎名/异常/堆栈）后跳过、池继续初始化其余引擎；因此 `config_template.yaml` 默认 SUPPORT_LIST 含 `baiduAPI.yaml`（百度不可达时 baiduLLM.setup() 取 token 失败）也能以 openaiAPI + langchainAgent 正常启动——gitignored `configs/config.yaml` 移除 baiduAPI.yaml 的临时规避已可撤掉；懒加载未采用（保持默认引擎预加载与 getEngine 语义）；测试 `test/test_engine_pool.py`（4 用例：非默认失败跳过/默认失败抛错/中间失败不影响前后/未知引擎返回 None）；既有 API 测试对 gitignored config.yaml 的引擎（RepeaterAgent/GoogleAPI/EdgeAPI）与音频文件的依赖已改为配置缺失时 skip（`_SUPPORTED_*` 集合判断），真实环境有 config.yaml 行为不变
- **重启后端**: 改 yaml 或 Python 代码后必须重启后端；前端代码由 Next HMR 自动热更
</important>

## CI (GitHub Actions)

- Workflow: `.github/workflows/ci.yml` — 三个并行 job：backend `pytest`（Python 3.12，**带覆盖率门禁**，见下）+ frontend（Node 22：`npm ci` → `npm run lint` → `npm run build`，C8 起含 lint）+ frontend E2E（Playwright 冒烟），on push to master / PR。

## 前端工程化（批次8 C8：TS strict/ESLint/组件规范，已就绪）

- **现状已达标**（C8 核对结论，避免重复改造）：TS 严格模式已开（`tsconfig.app.json` 继承 `@vue/tsconfig`，`strict: true` + `noUncheckedIndexedAccess`），type-check 已入 build（`npm run build` = `vue-tsc --build` + vite build 并行）；ESLint 已配置（`eslint.config.ts`：vue flat/essential + `vueTsConfigs.recommended` + oxlint + prettier），`npm run lint`（eslint+oxlint 均带 `--fix`）存量 0 error；CI frontend job 已含 lint 步骤
- **权威规范文档**：`doc/frontend-conventions.md`（命名/组件组织/scoped css + CSS token 体系（design-tokens 权威源 + variables 别名层）/Pinia setup store + localStorage 持久化/`fetchWithCodeEnvelope` API 封装模式——统一超时 15s + FastAPI detail 三形态解析 + 端点常量集中于 `api/index.ts` 禁止组件硬编码 URL/三态数据获取/CI 门禁）；与 `digitalHumanVue/src/CLAUDE.md` 子层指南互补
- **技术栈锁定**（禁止新重依赖）：Vue 3.5 + TS 6 strict + Vite 8 + Pinia 3 + vue-router 5 + vue-echarts；**不用** axios（原生 fetch）、Sass/Tailwind（scoped css）、第三方 mock 库（E2E 用 Playwright `page.route`）
- **访客端视觉重设计 V1 + 管理后台视觉打磨（2026-08-09 两阶段）**：设计体系 token 权威源 `src/styles/design-tokens.css`（墨/宣纸/青玉/朱砂/山岚/黛 命名色板 + 字阶/圆角/阴影/动效，水墨山水卷轴 × 数字人主题，签名元素=朱砂印章）；`variables.css` 降级为旧 `--color-*` 兼容别名层（存量组件零改动换肤）；权威设计文档 `doc/redesign-v1.md`（改 token 需同步）；访客端页面（Home/Chat/Login/Register/VisitorLayout）与管理后台（AdminLayout + 全部 7 管理页）均已迁移到新 token；管理端约定：按钮 primary=jade / danger=cinnabar（含 `--cinnabar-strong`）、卡片素白底 + `--shadow-card` + `--radius-md`、表头 `--jade-soft` 底、表单聚焦青玉描边 + 淡青玉光环、ECharts 主题色对齐 jade/cinnabar 色板（满意度 1-5 分 = 朱砂→青玉渐变段）；嵌入容器 token `--embed-*`（圆角/边框/底色/阴影/最小尺寸，访客对话页 ChatView 已按此落地，管理后台不嵌入数字人）；`variables.css` 兼容层保留（勿删，已无存量依赖，作兼容保险）；新增页面必须用新 token 名

## 测试质量门（批次7 C6：覆盖率门禁 + 前端 E2E）

- **后端覆盖率门禁**：`pytest-cov`（requirements-dev.txt）；CI Backend job 跑 `pytest -q --cov=app --cov-report=term:skip-covered --cov-fail-under=75`——阈值 75% 基于当前基线实测（84.31%，2026-08 批次7 收官时）留余量设定，低于阈值 CI 失败；本地同样命令可复现（约 3 分钟）。
- **前端 Playwright E2E 冒烟**（`digitalHumanVue/`，dev 依赖 `@playwright/test`）：`npm run e2e`（`npx playwright install --with-deps chromium` 装浏览器；webServer 自动拉起 vite dev server 端口 5199）——11 用例覆盖核心路径：首页（hero/活数据卡片/公告区 + 「浏览推荐」滚动）、对话页（游客兜底 + 推荐问句浮层展开/收起/重开）、管理端（登录流程/守卫 + 管理员登录页返回首页入口 + 知识库/公告/用量页基础渲染）。**稳定可重复的关键：后端接口全部在浏览器层 `page.route('http://localhost:5199/api/**')` mock**（`e2e/mocks.ts`，响应形状对齐后端契约）——不依赖真实后端；**坑：路由模式必须带 origin，不能写 `**/api/**`**（会误伤源码模块 `/src/api/*.ts` 导致 404）；E2E 文件不在 tsconfig.app.json include 内，`vue-tsc` 仅检查 `playwright.config.*`（tsconfig.node.json 已含）。

## Testing (pytest)

- Tests use in-memory SQLite (`tests/conftest.py` fixtures), no DATABASE_URL / .env needed.
- Gotcha: `app/core/config.py` instantiates `Settings()` at import with a validator that rejects empty Dify env vars — running pytest without `.env` or placeholder `DIFY_API_URL`/`DIFY_API_KEY` raises ImportError. CI injects dummy values (tests never call real APIs). 推荐问句（阶段3 T3）与量化评估（阶段4 首项）已脱离 Dify，不再需要 `DIFY_RECOMMEND_WORKFLOW_API_KEY`/`DIFY_WORKFLOW_API_KEY`。

## 结构化日志（批次2 C2：统一格式 + 请求日志）

- `app/core/logging_config.py` — 标准 logging 统一日志体系（无新依赖）：`configure_logging()`（main.py lifespan Phase 0 调用，替代就地 basicConfig；DEBUG 开关行为不变）+ `RequestLoggingMiddleware`（main.py 最后 add_middleware 最外层，只处理 http scope）
- 统一格式：时间(含毫秒)/级别/logger名/消息（`%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s`）；**uvicorn.access 已抑制**（其固定格式会原样记录 query 串含 token，与红线冲突，请求日志统一由中间件承担）
- 请求日志：每个 HTTP 请求一条 `app.request` logger 的 key=value 行（method/path/status/duration_ms/client/ip）；**异常优先**（异常或 5xx 记 ERROR 带异常堆栈，含 SSE 流中途抛错）；健康检查/文档等噪音路径降级 DEBUG；client 从 Authorization Bearer JWT 解析（用户 id/guest-xxx），X-Admin-Token 匹配配置标记 admin，解析不到为 `-`（不报错）；path 不含 query，不读请求体，token/key/密码绝不入日志；注意既有 catch-all 处理器把路由异常转 HTTP 200，排查不能只按 status=5xx 过滤（全局异常处理器仍会 ERROR 落盘）
- 测试：`tests/test_logging.py`（13 用例）；格式与查询方式见 `doc/logging-request-logs.md`

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
