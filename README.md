# 景区导览 AI 数字人系统

> 第十五届中国软件杯 A5 赛题 · 景区导览服务 AI 数字人

一套开箱即用的**景区智能导览数字人系统**：以 Live2D 数字人为交互载体，集成自然语言对话、语音识别（ASR）、语音合成（TTS）、个性化路线推荐、长短期记忆、本地 RAG 知识库、GPS 路径规划（高德）、运营数据分析与管理员 AI Agent，配套访客端网页与数据大屏管理后台。

**核心亮点**：
- **纯本地对话主链路**：deepagents 装配层（langgraph + langchain 1.x），Dify 仅作演示兜底（双轨回落）
- **AI 记忆系统**：短期会话记忆（SQLite 落盘）+ 长期用户画像（向量库提取/检索/工具化四工具），登录用户个性化回答
- **本地 RAG 知识库**：Qwen Embedding 向量化 + 向量落盘复用，无外部向量库依赖，管理后台 CRUD 自动同步索引
- **管理员 AI Agent**：后台自然语言运维——知识库/公告/记忆/统计/账号管理，删除类操作两阶段二次确认 + 审计日志
- **账号管理 + 操作审计**：账号全生命周期（创建/禁用/重置密码/删除，敏感操作要求 admin 角色），管理员敏感操作审计日志落库可查
- **可观测、可运营**：token 成本仪表盘、AI 运营分析、本地 LLM 量化评估、多环境部署与数据库备份

---

## 目录

- [功能清单](#功能清单)
- [架构总览](#架构总览)
- [目录结构](#目录结构)
- [快速启动](#快速启动)
- [部署](#部署)
- [核心能力详解](#核心能力详解)
- [API 概览](#api-概览)
- [文档索引](#文档索引)
- [测试与质量门](#测试与质量门)
- [常见问题](#常见问题)

## 功能清单

### 对话与推荐（访客端）

| 能力 | 说明 |
|------|------|
| **AI 数字人对话** | Live2D 数字人（ADH）内置对话界面，支持语音输入（腾讯云 ASR）+ 语音输出（Qwen TTS），SSE 流式逐字回答 + 工具调用步骤可视化 |
| **deepagents 主链路** | 对话主链路为 deepagents 装配层（A3 起）：LangGraph 状态图 + 结构化本地工具（景点/路线直查、知识检索、规则推荐、记忆四工具、GPS 两工具），零幻觉原则 |
| **意图分类双模式挂载** | 轻量规则分类器：简单问答零开销直答；复杂任务（规划/行程/预算）注入 prompt 级规划指令，回答结构化分节 |
| **Dify 双轨回落** | 主链路任何异常自动回落 Dify Chat Agent（演示兜底不翻车）；`DIALOGUE_ENGINE=dify` 可强制走 Dify |
| **个性化路线推荐** | 纯本地规则引擎 + 工具调用：输入偏好描述（"带老人小孩、喜欢拍照"）推理推荐路线，不依赖外部 API |
| **推荐问句** | 首页数字人引导区动态问句：聚合 7 天运营数据由本地 LLM 生成，每 5 分钟后台刷新（已脱离 Dify） |
| **GPS/高德路线规划** | "描述位置 → 地理编码 → 路径规划 → 地图展示"：`geocode_location`/`plan_route` 工具 + 对话内路线卡片（AMap 地图/文字兜底） |
| **SSE 流式契约** | `step`（工具步骤）/`token`（逐字回答）/`done`（会话 ID + RAG 来源 + 路线数据）/`error`（回落标记）事件，ADH 前端零适配消费 |

### 记忆系统

| 能力 | 说明 |
|------|------|
| **短期会话记忆** | 多轮上下文 SQLite 落盘 + 进程内 TTL 缓存，重启后同会话续聊不丢；TTL 滑动窗口（默认 7 天）自动清理 |
| **长期用户记忆** | 对话后后台 LLM 提取用户事实/偏好/习惯写入向量库（按 user 隔离）；新会话轻量摘要注入 + 记忆工具按需检索 |
| **记忆工具化四工具** | `recall_user_memory`/`save_user_memory`/`list_user_memories`/`delete_user_memory`，经 LangGraph state 注入当前用户，模型无法操作他人记忆 |
| **管理端记忆管理** | 后台查看/新增/修改/删除任意注册用户记忆（X-Admin-Token 鉴权，删除类要求 admin 角色） |
| **游客隔离** | 游客（匿名/guest token）享短期记忆，不写不读长期记忆 |

### 知识库与内容运营

| 能力 | 说明 |
|------|------|
| **本地 RAG 知识库** | 三命名空间（knowledge/documents/user_memory）向量存储，统一检索入口（对话/后台/推荐 Agent 共用）；embedding 未配置时自动降级本地 TF-IDF |
| **Qwen Embedding** | DashScope compatible-mode（`qwen3.7-text-embedding` 1024 维）；兼容旧 Minimax 原生格式（`EMBEDDING_API_STYLE` 切换） |
| **向量落盘复用** | 文档侧 sidecar 缓存向量（绑定 embedding 配置），启动跳过全量重算；embedding 配置变更自动失效重建 |
| **知识库管理后台** | 讲解词/FAQ/资料 CRUD + 批量导入（JSON/Markdown）；变更后自动重建向量索引，后台检索即时可见 |
| **公告模块** | 公告发布/下架/置顶 + 访客端展示（仅已发布、置顶优先）；首个真实 Alembic 迁移 |
| **首页活数据** | 热门景点/推荐路线卡片实时读取本地数据源，接口失败静态兜底 |

### 管理与可观测

| 能力 | 说明 |
|------|------|
| **管理员 AI Agent** | 后台自然语言运维：知识库/用户记忆/公告/账号管理/数字人形象/运营统计/RAG 重建索引/首页数据；删除类两阶段二次确认（TTL 过期自动失效）+ 写操作审计日志；SSE 事件契约与用户侧一致 |
| **账号管理 + 操作审计** | 账号全生命周期（创建/禁用/重置密码/删除，敏感操作要求 admin 角色，删除为单目标级联）；管理员敏感操作审计日志落库 `admin_audit_logs` 表，后台可查询 |
| **数字人形象管理** | 13 个 Live2D 形象（与 ADH `public/characters/` 一致，默认 Hiyori）持久化于 `app_settings` 表；公开/管理端 API + 管理员 Agent 工具（list_character_models / set_character_model，写操作审计，非法形象名 422） |
| **AI 运营分析** | 数据大屏 + 报告：情感趋势/满意度/热点关注/景点热度；LLM 分析失败自动回退纯 DB 统计（`llm_analysis` 降级标记） |
| **本地量化评估** | 对话内容本地 LLM 量化评估（情感/满意度/关注点/涉及景点/改进建议）写入 Evaluation 表，驱动管理后台数据（已脱离 Dify Workflow） |
| **token 成本仪表盘** | 统一埋点（对话/推荐/评估/压缩/管理 agent 全链路）+ 批量写库 + 报表 API（趋势/维度聚合/明细/CSV 导出）+ 前端成本仪表盘页（ECharts 柱状趋势图） |
| **用户认证** | 注册/登录/游客三种身份，JWT + bcrypt；对话接口自动识别 Bearer token 标记用户 |
| **安全加固** | 认证端点限流（防爆破，429 + Retry-After）、CORS 白名单（默认覆盖演示环境）、管理端 RBAC（admin/operator 角色分级） |
| **多环境 + 备份** | dev/staging/prod 三档配置（`APP_ENV` 健康检查透出）；SQLite 在线备份脚本（`.backup` 快照 + 完整性校验 + 保留 N 份） |

### 工程与性能

| 能力 | 说明 |
|------|------|
| **A4 成本/速度调优** | 工具调用上限（15 次防病态循环）+ 工具结果归一化缓存（LRU + TTL 分级 + 索引重建即时失效）+ 长会话上下文压缩（回答后触发，历史 ≥24 条压缩早期轮次） |
| **数据库迁移** | Alembic（async 模板），启动自动迁移三态分流（空库建齐/遗留库幂等补建 + 基线化/版本化库升级），开发流程 autogenerate |
| **结构化日志** | 统一日志格式 + 每请求一条日志（方法/路径/状态/耗时/用户），token/密码绝不入日志 |
| **前端工程化** | Vue 3 + TS strict + ESLint + 统一组件规范（scoped css + 集中式 API 封装） |

## 架构总览

### 四服务结构

```
┌─────────────────────────────────────────────────────────────────┐
│  digitalHumanVue (Vue 3, :5173)                                 │
│  访客端（首页/对话页）  +  管理后台（数据大屏/报告/知识库/公告/   │
│  记忆管理/管理员Agent/成本仪表盘/账号管理）                       │
└───────────────┬─────────────────────────────────────────────────┘
                │ fetch(/api/*) → Vite proxy
┌───────────────▼─────────────────────────────────────────────────┐
│  digitalHumanApi (FastAPI, :8000)                               │
│  对话主链路(deepagents) · 记忆 · RAG · 推荐 · 公告 · 分析 ·      │
│  用量统计 · 管理员Agent · 认证/安全 · Alembic/SQLite             │
└───────┬──────────────────────────┬──────────────────────────────┘
        │ SSE /api/v1/dialogue/chat │ 双轨回落(异常时)
┌───────▼───────────────┐   ┌───────▼───────────────┐
│ live2d-frontend (ADH) │   │ Dify AI 平台（仅两类保留 │
│ 后端 :8001 + 前端 :3000│   │ 依赖：对话回落 + 历史    │
│ Live2D 数字人          │   │ 数据接口）              │
└────────────────────────┘   └────────────────────────┘
```

- **访客端**：首页（公告 + 热门景点 + 推荐问句）+ 数字人对话页（iframe 嵌入 ADH，内置完整对话/语音功能）
- **管理后台**：数据大屏、分析报告、知识库管理、公告管理、记忆管理、管理员 Agent、成本仪表盘、账号管理（含操作审计）、数字人形象管理
- **ADH 本地数字人**：基于 awesome-digital-human-live2d 定制，引擎化架构（Registry + Factory + Pool），LangChainAgent 默认引擎直连本后端对话接口

### 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · SQLite (aiosqlite) · Alembic · Pydantic v2 · httpx |
| Agent 层 | langchain 1.x · langgraph · **deepagents 0.7.5**（装配层：HarnessProfile + 内置工具排除 + 中间件）· ToolCallLimit/Summarization 中间件 |
| 向量/RAG | numpy 矩阵余弦相似度 · 本地 JSON 持久化（无外部向量库）· Embedding 工厂（Minimax 原生 / Qwen-DashScope OpenAI 兼容 / TF-IDF 降级） |
| 前端 | Vue 3.5 · TypeScript (strict) · Vite 8 · Pinia 3 · vue-router 5 · vue-echarts (ECharts) · marked + DOMPurify |
| 数字人 | ADH（Python FastAPI + Next.js 14）：腾讯云 ASR · Qwen TTS（DashScope WebSocket）· LangChainAgent（默认，走本后端） |
| 外部服务 | opencode-go（对话/分析/评估 LLM 主端点，`deepseek-v4-flash`）· Dify（仅对话回落 + 历史数据抓取）· 高德 Web 服务（GPS）· DashScope（Embedding/TTS） |
| 部署 | Docker Compose（api + web/nginx）· Nginx 反代 · 备份脚本（sqlite3 `.backup`） |

### 数据流

```
请求流：Vue → fetch(/api/*) → Vite proxy → FastAPI → SQLite
对话流：ADH → SSE(/api/v1/dialogue/chat) → deepagents 装配层
        （意图分类 → 工具调用(本地 JSON/RAG/记忆/GPS) → LLM 总结）→ SSE 回 ADH
        → 本地对话直接落库 dialogues 表（record_dialogue，每轮两行）
数据流：对话链路 → dialogues 表 → analysis task(300s)
        → 本地 LLM 量化评估 → Evaluation 表 → 管理后台大屏/报告
        （Dify 历史对话 ← polling task(30s)，数据源之一）
记忆流：对话 done 后 → 记忆提取钩子(后台任务) → user_memory 向量库
        → 新会话轻量摘要注入 + 记忆四工具按需检索
```

## 目录结构

```
├── digitalHumanApi/            # FastAPI 后端（:8000）
│   ├── app/
│   │   ├── main.py             # 应用入口（lifespan、CORS、全局异常、router 注册）
│   │   ├── core/               # config.py / database.py / llm.py（对话 LLM 工厂）
│   │   │                       # embedding.py（Embedding 工厂）/ rag.py（命名空间向量存储）
│   │   │                       # memory_store.py（短期记忆）/ migrations.py（启动自动迁移）
│   │   │                       # security.py / rate_limiter.py / logging_config.py
│   │   ├── models/             # SQLAlchemy ORM 实体（dialogue/evaluation/stat/user/visitor/
│   │   │                       #   knowledge/announcement/usage/admin_audit/app_setting）
│   │   ├── schemas/            # Pydantic v2 请求/响应模型
│   │   ├── data/               # 本地 JSON 数据源（景点、路线）
│   │   ├── services/           # 业务逻辑
│   │   │   ├── deepagents/     # ★ 对话主链路装配层（agent/harness/memory_tools/
│   │   │   │                   #   amap_tools/tool_cache/compaction）
│   │   │   ├── admin_agent/    # 管理员 Agent（agent/tools/confirm 两阶段确认）
│   │   │   ├── amap/           # 高德 Web 服务封装（geocode/route）
│   │   │   ├── chat_agent.py   # 会话存储层 + 记忆钩子（弃用链路保留参考实现）
│   │   │   ├── recommend_agent.py / recommend_service.py   # 推荐引擎 / 推荐问句
│   │   │   ├── rag_service.py / memory_service.py / knowledge_service.py
│   │   │   ├── analytics_service.py / evaluation_service.py  # 运营分析 / 量化评估
│   │   │   └── usage_tracker.py / usage_report.py            # token 记账 / 报表
│   │   ├── routers/            # FastAPI 端点（auth/dialogue/recommend/analysis/admin/
│   │   │                       #   knowledge/announcement/memory_admin/admin_agent/user_admin/
│   │   │                       #   scenic/dh_character/rag/usage/home/visitor）
│   │   └── tasks/              # 后台循环（polling/analysis/recommend_questions/
│   │                           #   memory_cleanup）+ backfill CLI
│   ├── migrations/             # Alembic（versions/：初始基线 + 公告表 + llm_usage 表 + 审计表 + users 加列 + app_settings 表）
│   ├── tests/                  # pytest 测试套件（844 用例）
│   ├── scripts/                # smoke_llm / smoke_amap / backup_db.sh
│   └── doc/                    # 后端权威契约文档（见文档索引）
├── digitalHumanVue/            # Vue 3 前端（:5173）
│   ├── src/
│   │   ├── api/                # 集中式端点常量 + API 封装（index.ts/auth.ts/usage.ts/…）
│   │   ├── views/              # 页面组件（Home/Chat/Login/Register + admin/*）
│   │   ├── layouts/ stores/ composables/ styles/
│   ├── e2e/                    # Playwright 冒烟测试（浏览器层 mock 后端）
│   └── vite.config.ts          # /api 代理 → :8000
├── live2d-frontend/            # ADH 本地 Live2D 数字人（独立 git 仓库）
│   ├── main.py                 # 数字人后端（:8001，ASR/TTS/Agent 引擎）
│   ├── configs/                # 引擎配置（tencentAPI/qwenTTS/minimaxTTS/langchainAgent/…）
│   └── web/                    # Next.js 前端（:3000，Live2D 渲染 + 对话界面）
├── doc/                        # 设计/验证/契约文档（景区知识库/ 为 RAG 文档源）
├── docker-compose.yml          # 一体化部署（api + web/nginx）
├── nginx.conf                  # /api/ 反代到后端（proxy_read_timeout 300s）
├── Dockerfile.api / Dockerfile.web
└── .env.example                # Docker Compose 部署环境变量模板
```

## 快速启动

### 1. 前置系统依赖

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| Python | 主后端 ≥ 3.11（deepagents==0.7.5 要求）；**ADH 要求 ≤ 3.11**（numpy 1.24.4 预编译包上限，3.12 需源码编译易失败） | 后端 / 数字人后端 |
| Node.js | ^22.18.0 \|\| ≥ 24.12.0 | Vue 前端 + ADH Next.js 前端 |
| ffmpeg / ffprobe | 系统包（`sudo apt install ffmpeg`） | ADH TTS 链路 MP3→WAV 必需（缺失时 TTS 静默失败） |
| sqlite3 CLI | 可选 | 仅备份脚本 `backup_db.sh` 需要 |

### 2. 配置环境变量

**后端**（`digitalHumanApi/.env`，从 `.env.example` 复制）：

| 变量 | 说明 |
|------|------|
| `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` | **对话 LLM**（主链路/推荐/评估共用，默认 opencode-go `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`）；未配置时对话走 Dify 回落 |
| `LLM_FALLBACK_*` | 备用端点（部署默认同主端点 opencode-go；代码兜底默认官方 DeepSeek API） |
| `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_API_STYLE` / `EMBEDDING_BATCH_SIZE` | **RAG 向量化**（推荐 Qwen/DashScope：`EMBEDDING_API_STYLE=openai` + `qwen3.7-text-embedding`，端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`；默认兼容旧 Minimax 配置 `embo-01`）；未配置自动降级本地 TF-IDF；批量上限默认 20（DashScope 单请求 input 硬上限，可覆盖） |
| `OPENAI_API_KEY` / `OPENAI_API_BASE` / `OPENAI_MODEL` | **运营分析** LLM（当前部署与 `LLM_*` 同 opencode-go 端点）；未配置回退纯 DB 统计 |
| `DIFY_API_URL` / `DIFY_API_KEY` / `DIFY_LIGHT4AI_USER` | **Dify 双轨回落 + 历史数据抓取**（config 校验器要求非空，dev 可填占位）；量化评估/推荐问句已本地化，不再需要 Workflow Key |
| `AMAP_WEB_KEY` | **高德 Web 服务 Key**（GPS 地理编码/路径规划）；未配置时对话工具返回 `amap_not_configured` 错误码不阻断对话 |
| `JWT_SECRET` | JWT 签名密钥（生产必填随机长串；留空则启动生成临时密钥、重启后 token 失效） |
| `ADMIN_API_TOKEN` / `ADMIN_TOKENS` | 管理端认证（X-Admin-Token）；`ADMIN_TOKENS="token1=admin,token2=operator"` 支持 RBAC 角色分级 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | **管理后台账号密码登录**（批次9，替代 X-Admin-Token 输入形式）；默认演示值 `admin` / `admin123456`（见下方「访问入口」），生产必须修改 |
| `CORS_ORIGINS` | 跨域白名单（默认覆盖 localhost/127.0.0.1/192.168.16.132 × 5173/8001/3000 演示环境，生产显式收紧；credentials 开启时不能含 `*`） |
| `DIALOGUE_ENGINE` | `langchain`（默认，deepagents 主链路 + Dify 回落）/ `dify`（强制 Dify） |

其余（记忆 TTL、A4 调优参数、限流、token 记账、RAG 分块等）均带合理默认值，见 `.env.example` 分区注释与 [`doc/deployment-environments.md`](doc/deployment-environments.md)。

**前端**（`digitalHumanVue/.env`）：

| 变量 | 说明 |
|------|------|
| `VITE_LIGHT4AI_URL` | 数字人 iframe 地址（局域网部署必填 `http://<服务器IP>:3000`，不能填 localhost） |
| `VITE_API_BASE` | 后端地址（默认 `http://localhost:8000`，Vite 代理目标） |
| `VITE_LIVE2D_MODEL` | Live2D 模型名（默认 `hiyori`） |

**ADH 前端**（`live2d-frontend/web/.env`）：

| 变量 | 说明 |
|------|------|
| `NEXT_PUBLIC_ADH_SERVER_PORT` | **必须设为 `8001`**（默认 8000 会连到主后端导致 "Soul is death!"） |
| `NEXT_PUBLIC_AMAP_JS_KEY` / `NEXT_PUBLIC_AMAP_SECURITY_CODE` | 高德 JS API 凭据（对话路线卡片地图渲染，与后端 `AMAP_WEB_KEY` 两套独立凭据；未配置降级文字路线） |

> 数据库 schema 由 **Alembic 启动自动迁移**（`app/core/migrations.py` 三态分流：空库建齐 / 遗留库幂等补建 + 基线化 / 版本化库升级），无需手动执行；本地开发也可 `alembic upgrade head`。

### 3. 启动四服务

> 一键起停/健康检查/.env 校验可用仓库根 `scripts/` 运维脚本（`start_all.sh` / `stop_all.sh` / `health_check.sh` / `check_env.sh`，用法与集成坑位见 [`scripts/README.md`](scripts/README.md)）；以下为手动首次部署命令。

```bash
# ① 主后端（:8000）—— 后台任务自动启动（轮询/分析/推荐问句/记忆清理/用量 flush）
cd digitalHumanApi
pip install -r requirements.txt
uvicorn app.main:app --reload

# ② 主前端（:5173）
cd digitalHumanVue
npm ci && npm run dev

# ③ ADH 数字人后端（:8001，Python ≤ 3.11）
cd live2d-frontend
virtualenv -p python3.10 venv && ./venv/bin/pip install -r requirements.txt   # 首次（已有 venv/ 则直接下一步）
./venv/bin/python main.py

# ④ ADH 数字人前端（:3000）—— 勿用 pnpm dev（触发依赖检查失败）
cd live2d-frontend/web
npm install --legacy-peer-deps   # 首次运行安装依赖（nextui peer 冲突需 legacy 模式）
node node_modules/next/dist/bin/next dev -p 3000
```

### 4. 访问入口

| 入口 | 地址 |
|------|------|
| 访客端首页 | http://localhost:5173/visitor |
| 数字人对话页 | http://localhost:5173/visitor/chat（iframe 嵌入 ADH） |
| 管理后台（数据大屏） | http://localhost:5173/admin |
| 管理后台（报告/知识库/公告/记忆/管理员Agent/成本仪表盘/账号管理） | http://localhost:5173/admin/report · /admin/knowledge · /admin/announcements · /admin/memories · /admin/agent · /admin/usage · /admin/users |
| ADH 数字人前端（调试） | http://localhost:3000 |
| 后端 API 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/api/v1/health（含 `app_env` 字段） |

> **管理员账号（演示）**：未登录访问 `/admin` 会跳转到管理员登录页（`/admin/login`），主页 Hero 区也有「进入管理后台」入口。默认演示账号为 **`admin` / `admin123456`**，来源于环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（`digitalHumanApi/.env`，见 `.env.example` 注释与上方配置表；docker-compose 部署则在根目录 `.env` 配置）。登录成功后签发管理员 JWT，管理端接口同时接受 `Authorization: Bearer` 与旧 `X-Admin-Token`（向后兼容）。**生产环境务必修改为强密码并配置 `JWT_SECRET`。**

## 部署

### 方式一：Docker Compose（一体化）

```bash
cp .env.example .env   # 填写 DIFY_API_URL/DIFY_API_KEY/LLM_*/AMAP_WEB_KEY 等
docker-compose up -d --build
```

- 前端经 Nginx 提供：http://localhost/（80 端口），`/api/` 反代到后端容器 `api:8000`（`proxy_read_timeout 300s`，与运营分析超时预算对齐）
- 后端 volume 挂载 `./digitalHumanApi:/app`（库文件/记忆库/备份目录即宿主机路径）
- 容器内访问宿主机服务（如 Dify）需在 `docker-compose.yml` 的 `api` 服务加 `extra_hosts: ["host.docker.internal:host-gateway"]` 并将 `DIFY_API_URL` 指向 `http://host.docker.internal/v1`
- ADH 数字人不在 Compose 内（依赖音频/GPU 与真实浏览器），按本机四服务方式部署

### 方式二：本机四服务（开发/局域网演示）

见上方「快速启动」；局域网访问注意：Vue 前端 `npm run dev -- --host 0.0.0.0`，`VITE_LIGHT4AI_URL` 填服务器 IP，防火墙放行 8000/5173/3000/8001 端口。

### 数据库备份

`digitalHumanApi/scripts/backup_db.sh`：sqlite3 `.backup` 在线安全备份（快照语义，不中断业务）+ 完整性校验 + 保留最近 N 份（默认 7）。Docker 场景宿主机直接调度（volume 路径即容器内 `/app/...`）。恢复验证步骤见 [`doc/database-backup.md`](doc/database-backup.md)。

### 多环境（dev / staging / prod）

`APP_ENV` 三档标识（非法值启动即报错），差异：DEBUG、CORS 白名单、限流参数、JWT_SECRET、凭据必填项。切换指引见 [`doc/deployment-environments.md`](doc/deployment-environments.md)。

## 核心能力详解

> 详细设计以 **`AGENTS.md`（项目记忆，唯一权威源）** 与下方「文档索引」所列契约为准，本节为导读。

| 主题 | 关键实现 | 权威文档 |
|------|---------|---------|
| 对话主链路 | `app/services/deepagents/`：HarnessProfile 装配（排除 9 个内置文件/子代理工具）、ScenicDeepAgentState（user 注入）、意图分类 + prompt 级规划指令、SSE 契约（suppress_tokens + 按 call id 去重）、`get_agent` 实例缓存（构造 ~24s） | [`digitalHumanApi/doc/deepagents-tuning.md`](digitalHumanApi/doc/deepagents-tuning.md)、[`doc/phase3-dual-mode.md`](doc/phase3-dual-mode.md)（历史） |
| 成本/速度调优 | 工具调用上限 15（ToolCallLimitMiddleware，超限优雅作答不回落）、工具结果归一化缓存（LRU + TTL 分级 + 索引重建即时失效）、长会话上下文压缩（≥24 条消息触发，保留最近 10 条，后台任务不阻塞响应） | [`digitalHumanApi/doc/deepagents-tuning.md`](digitalHumanApi/doc/deepagents-tuning.md) |
| 短期记忆 | `app/core/memory_store.py`：标准库 sqlite3 双写 + 进程内 TTL 缓存，`source` 归属判定（双轨回落兼容），清理任务定期删过期行 | `AGENTS.md`「短期记忆持久化」 |
| 长期记忆 | `app/services/memory_service.py`：记忆条目 schema `{user, fact_type, content, source_conversation_id, timestamp}`，同事实覆盖去重、每用户上限 200、游客跳过；对话后后台提取 + 轻量摘要注入 + 记忆四工具 | [`digitalHumanApi/doc/long-term-memory.md`](digitalHumanApi/doc/long-term-memory.md) |
| 记忆管理 API | `/api/admin/memories*`（X-Admin-Token，删除类要求 admin）：用户列表（含条数）、按用户查、增/改（删后重建）/删，审计日志先行 | [`digitalHumanApi/doc/memory-admin-api.md`](digitalHumanApi/doc/memory-admin-api.md) |
| 本地 RAG | `app/core/embedding.py` + `rag.py` + `app/services/rag_service.py`：三命名空间向量存储 + sidecar 向量落盘（启动复用跳过重算）、统一检索入口、CRUD 后自动重建索引 | `AGENTS.md`「本地 RAG 知识库」 |
| 推荐引擎 | `app/services/recommend_agent.py`：LangChain Tool Calling + 规则引擎，工具直查 `app/data/` 本地 JSON（零幻觉） | `AGENTS.md`「个性化推荐」 |
| 公告/首页 | `announcements` 表 + 管理端 CRUD + 访客公开接口；`/api/v1/home/highlights` 活数据 | [`digitalHumanApi/doc/announcement-api.md`](digitalHumanApi/doc/announcement-api.md) |
| 管理员 Agent | `app/services/admin_agent/`：管理工具集（全部复用既有 service）、删除两阶段确认（`confirm.py`，TTL 默认 120s）、`_audit_log` 审计 | `AGENTS.md`「管理员 agent」 |
| 运营分析/评估 | `analytics_service.py`（LLM + DB 兜底 + 降级标记）+ `evaluation_service.py`（本地 LLM 量化评估 → Evaluation 表）；超时预算 `LLM_ANALYSIS_TIMEOUT=240`（opencode-go 延迟高） | [`doc/phase3-analytics-verification.md`](doc/phase3-analytics-verification.md) |
| token 记账 | `usage_tracker.py`（UsageTrackingChatOpenAI 四入口埋点 + contextvars 链路标注 + 队列批量 flush）→ `llm_usage` 表 → `/api/admin/usage/*` 报表 + CSV 导出 | [`digitalHumanApi/doc/usage-tracking.md`](digitalHumanApi/doc/usage-tracking.md) |
| GPS/高德 | `app/services/amap/`：geocode/route 封装 + 统一错误信封（`amap_*` 错误码）+ 对话两工具 + `route_data` 透出路线卡片 | [`doc/amap-gps-api.md`](doc/amap-gps-api.md) |
| 安全 | 认证限流（IP+账号双维度滑动窗口）、CORS 白名单（credentials 与 `*` 互斥校验）、RBAC（`verify_admin_token` + `require_role`） | `AGENTS.md`「安全加固」 |
| 日志 | `logging_config.py`：统一格式 + 每请求一条日志（key=value），token/key 绝不入日志 | [`doc/logging-request-logs.md`](doc/logging-request-logs.md) |
| 数据库迁移 | Alembic async 模板，启动自动迁移，开发流程 autogenerate（改 models → revision → upgrade → 测试） | [`digitalHumanApi/doc/alembic-migrations.md`](digitalHumanApi/doc/alembic-migrations.md) |
| 前端规范 | TS strict + ESLint + 集中式 API 封装 + 三态数据获取 | [`doc/frontend-conventions.md`](doc/frontend-conventions.md) |

## API 概览

> 访客/对话接口前缀 `/api/v1`；管理接口前缀 `/api/admin/*`（X-Admin-Token 认证）。完整契约见对应 doc。

| 接口 | 说明 |
|------|------|
| `POST /api/v1/auth/register` / `login` / `guest` / `GET /me` | 用户注册/登录/游客 token/JWT 校验（认证端点限流保护） |
| `POST /api/v1/admin/login` | 管理员账号密码登录（`ADMIN_USERNAME`/`ADMIN_PASSWORD`，批次9；签发 `typ=admin` 的管理员 JWT，错误凭据 401） |
| `POST /api/v1/dialogue/chat` | 对话（非流式 + `streaming=true` SSE：step/token/done/error 事件；deepagents 主链路 → Dify 回落） |
| `POST /api/v1/recommend` | 个性化路线推荐 |
| `GET /api/v1/visitor/recommended-questions` / `POST /refresh-recommend` | 推荐问句（读缓存 / 手动刷新） |
| `GET /api/v1/analysis/overview` / `trends` / `POST /run-now` | AI 运营分析 / 趋势 / 手动触发评估 |
| `GET /api/v1/home/highlights` / `GET /api/v1/announcements` | 首页活数据 / 访客公告（公开） |
| `GET /api/v1/health` | 健康检查（含 `app_env`） |
| `/api/admin/knowledge*` | 知识库 CRUD + 导入（删除要求 admin 角色） |
| `/api/admin/announcements*` | 公告管理（CRUD + 发布状态切换 + 置顶） |
| `/api/admin/memories*` | 长期记忆管理（用户列表/按用户查/增改删，删除要求 admin） |
| `/api/admin/users*` | 账号管理（列表/详情/创建/禁用/重置密码/删除 + 审计日志查询；敏感操作要求 admin 角色，删除单目标级联） |
| `/api/admin/agent/chat`（+ `/chat/stream`） | 管理员 Agent 对话（两阶段删除确认 + 审计） |
| `/api/admin/usage/overview` / `details` / `export` | token 用量报表（趋势/明细/CSV 导出） |
| `GET /api/v1/dh-character` / `GET·PUT /api/admin/dh-character` | 数字人形象（公开读取 / 管理端读取+设置；写操作审计，非法形象名 422） |
| `POST /api/rag/search` / `index` / `GET /api/rag/status` | RAG 统一检索 / 重建索引 / 状态（含 embedding 后端与降级标记） |

## 文档索引

### 权威契约（当前状态，以这些为准）

| 文档 | 位置 | 内容 |
|------|------|------|
| 项目记忆（唯一权威源） | `AGENTS.md`（`CLAUDE.md` 为 symlink） | 全量架构/实现/坑位/配置，任何新会话先读它 |
| 高德 GPS 接口契约 | [`doc/amap-gps-api.md`](doc/amap-gps-api.md) | 错误码信封/工具用法/凭据 |
| 公告模块契约 | [`digitalHumanApi/doc/announcement-api.md`](digitalHumanApi/doc/announcement-api.md) | 路径/请求响应 schema/鉴权/排序约定 |
| 记忆管理 API 契约 | [`digitalHumanApi/doc/memory-admin-api.md`](digitalHumanApi/doc/memory-admin-api.md) | 管理端记忆增删改查契约 |
| 账号管理 API 契约 | [`digitalHumanApi/doc/account-admin-api.md`](digitalHumanApi/doc/account-admin-api.md) | 账号 CRUD/禁用/重置密码/删除级联/审计表契约 |
| token 记账契约 | [`digitalHumanApi/doc/usage-tracking.md`](digitalHumanApi/doc/usage-tracking.md) | 埋点/表/报表 API/单价映射 |
| 长期记忆设计 | [`digitalHumanApi/doc/long-term-memory.md`](digitalHumanApi/doc/long-term-memory.md) | 记忆 schema/接口/注入契约 |
| Alembic 迁移 | [`digitalHumanApi/doc/alembic-migrations.md`](digitalHumanApi/doc/alembic-migrations.md) | 迁移流程/坑位/权威约定 |
| deepagents 调优 | [`digitalHumanApi/doc/deepagents-tuning.md`](digitalHumanApi/doc/deepagents-tuning.md) | A4 组合拳实测定参 |
| 多环境部署 | [`doc/deployment-environments.md`](doc/deployment-environments.md) | dev/staging/prod 三档差异与切换 |
| 数据库备份 | [`doc/database-backup.md`](doc/database-backup.md) | `.backup` 对比/恢复验证/Docker 路径 |
| 结构化日志 | [`doc/logging-request-logs.md`](doc/logging-request-logs.md) | 格式/请求日志/查询方式 |
| 前端工程化规范 | [`doc/frontend-conventions.md`](doc/frontend-conventions.md) | TS strict/ESLint/组件规范 |
| LLM 接入层冒烟 | [`doc/llm-adapter-smoke-test.md`](doc/llm-adapter-smoke-test.md) | DeepSeek 思考格式兼容性/备用端点差异 |
| Dify 依赖审计 | [`doc/dify-dependency-audit.md`](doc/dify-dependency-audit.md) | Dify 依赖面定稿（仅对话回落 + 历史接口） |

### 历史文档（保留存档价值，不代表现状）

| 文档 | 标注 |
|------|------|
| [`doc/phase1-e2e-verification.md`](doc/phase1-e2e-verification.md) / [`doc/phase2-memory-verification.md`](doc/phase2-memory-verification.md) / [`doc/phase3-e2e-verification.md`](doc/phase3-e2e-verification.md) / [`doc/phase4-e2e-verification.md`](doc/phase4-e2e-verification.md) | 各阶段全链路联调验证报告（结论当时有效；最新实现以 AGENTS.md 为准） |
| [`doc/phase3-analytics-verification.md`](doc/phase3-analytics-verification.md) | 运营分析超时实测（结论仍被配置引用：`LLM_ANALYSIS_TIMEOUT` 依据） |
| [`doc/phase3-dual-mode.md`](doc/phase3-dual-mode.md) | 自研双模式设计（**已弃用**，文档头部有弃用注记；主链路为 deepagents 装配层） |
| [`doc/real-env-verification.md`](doc/real-env-verification.md) / [`doc/dify-data-migration-report.md`](doc/dify-data-migration-report.md) | 阶段0~2 真实环境验证 / Dify 数据迁移盘点（阶段0 历史） |
| [`doc/project-plan.md`](doc/project-plan.md) / [`doc/next-steps-plan.md`](doc/next-steps-plan.md) / [`doc/status-review.md`](doc/status-review.md) / [`doc/architecture-analysis.md`](doc/architecture-analysis.md) / [`doc/decision-questions.md`](doc/decision-questions.md) / [`doc/phase3-workflow-review.md`](doc/phase3-workflow-review.md) | 早期规划/架构咨询/审视记录 |
| [`doc/local-digital-human-deploy-plan.md`](doc/local-digital-human-deploy-plan.md) | ADH 部署任务清单（头部有 2026-08-05 更新注记，最新状态以 AGENTS.md ADH 专节为准） |
| [`doc/dify配置/`](doc/dify配置/)、[`doc/腾讯云语音识别/`](doc/腾讯云语音识别/)、[`doc/推荐工作流/`](doc/推荐工作流/) | Dify/腾讯云配置存档（量化评估/推荐问句已本地化，仅存档） |

## 测试与质量门

### 后端（pytest + 覆盖率门禁）

```bash
cd digitalHumanApi
pip install -r requirements-dev.txt
pytest -v                                   # 全量 844 用例（内存 SQLite，无外部依赖）
pytest -q --cov=app --cov-report=term:skip-covered --cov-fail-under=75   # 覆盖率门禁（阈值 75%，基线 84%+）
```

测试使用内存 SQLite 与 httpx.MockTransport 模拟外部 API，不发真实网络请求；不使用第三方 mock 库。CI Backend job 以 `--cov-fail-under=75` 门禁执行。

### 前端（Playwright E2E 冒烟）

```bash
cd digitalHumanVue
npm ci
npx playwright install --with-deps chromium    # 首次
npm run e2e                                    # 自动起 vite dev server（:5199），后端接口浏览器层 mock
```

11 用例覆盖核心路径：首页（hero/活数据卡片/公告区）、对话页（游客兜底 + 推荐问句浮层）、管理端（登录流程/守卫 + 返回首页入口 + 知识库/公告/用量页渲染）。后端接口全部 `page.route` mock（响应形状对齐后端契约），稳定可重复、不依赖真实后端。

### CI（GitHub Actions）

`.github/workflows/ci.yml` 三 job 并行：backend pytest（覆盖率门禁）+ frontend（lint + build，含 vue-tsc type-check）+ frontend E2E（Playwright）。

## 常见问题

**Q: 数字人页面显示 "Soul is death!"？**
A: ADH 前端 WebSocket 心跳连不上数字人后端。检查：① 数字人后端是否启动（`curl http://localhost:8001/adh/agent/v0/list`）；② `live2d-frontend/web/.env` 是否设置 `NEXT_PUBLIC_ADH_SERVER_PORT=8001`；③ 改后重启数字人前端。

**Q: 对话一直走 Dify 回落（无工具调用）？**
A: 主链路依赖 `LLM_API_KEY`（opencode-go）。检查 `digitalHumanApi/.env` 中 `LLM_API_*` 是否配置且网络可达（冒烟：`cd digitalHumanApi && LLM_API_KEY=... python -m scripts.smoke_llm`）。

**Q: 数字人 TTS 无声音？**
A: 检查 ffmpeg 是否安装（后端日志 `No such file or directory: 'ffprobe'` 即缺失，`sudo apt install ffmpeg`）；Qwen TTS key 经环境变量 `QWEN_API_KEY` 提供。

**Q: 大屏数据为空？**
A: 大屏依赖量化评估结果（Evaluation 表，由本地 LLM 评估产出）。手动触发：`curl -X POST http://localhost:8000/api/v1/analysis/run-now`；后台分析任务每 5 分钟自动运行。

**Q: 语音输入不可用（麦克风无响应、`navigator.mediaDevices is undefined`）？**
A: 语音功能需要安全上下文（HTTPS 或 localhost）。局域网 IP + 明文 HTTP 访问时浏览器禁用麦克风，属硬性安全策略。临时调试用 SSH 端口转发（`ssh -L 3000:127.0.0.1:3000 ...`），正式使用部署 HTTPS。

**Q: 后端日志报量化评估/分析失败？**
A: 阶段4 起评估与分析均为本地 LLM（`LLM_*`/`OPENAI_*` 配置 opencode-go）。失败会静默降级（评估跳过本轮、分析回退 DB 统计），不阻断任务；持续失败检查 key 与端点网络（opencode-go 延迟高，单条评估预算 120s）。

**Q: 认证接口突然返回 429？**
A: 认证限流（批次6 C3）防爆破：默认 5 次失败/60s 后进入 15 分钟冷却，响应带 `Retry-After` 头。正常登录成功后自动清零，不误伤；`AUTH_RATE_LIMIT_ENABLED=false` 可关闭。

**Q: 局域网部署时前端跨域/CORS 报错？**
A: 开发环境由 Vite 代理转发（无跨域）；直接跨端口访问需确认后端 `CORS_ORIGINS` 白名单包含访问源（默认含 192.168.16.132 演示环境）。生产用 Nginx 同源反代。

---

更多实现细节与坑位见 **`AGENTS.md`**（项目记忆，唯一权威源，随项目持续更新）。
