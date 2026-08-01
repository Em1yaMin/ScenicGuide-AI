# Project agent memory

景区导览 AI 数字人系统的项目记忆文件（唯一权威记忆源；CLAUDE.md 是指向本文件的 symlink）。语言约定：与用户交流及代码注释默认使用中文。

# Project Overview

景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台提供对话抓取与情感分析，另含纯本地推荐引擎、本地 RAG 知识库和 AI 运营分析。前端提供访客端（数字人对话）和管理后台（数据大屏 + 报告 + 知识库管理）。

# Architecture

```
digitalHumanApi/          # Python FastAPI 后端
  app/
    main.py               # 应用入口（lifespan、CORS、全局异常处理、router 注册）
    core/                 # config.py (Settings) + database.py + rag.py (纯 Python TF-IDF)
    data/                 # 本地 JSON 数据源（景点、路线）
    models/               # SQLAlchemy 2.0 ORM 实体 → models/CLAUDE.md
    schemas/              # Pydantic v2 请求/响应模型 → schemas/CLAUDE.md
    services/             # 业务逻辑、外部 API 客户端 → services/CLAUDE.md
    routers/              # FastAPI HTTP 端点 → routers/CLAUDE.md
    tasks/                # 后台 asyncio 循环（polling、analysis）
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

- `app/core/rag.py` — 纯 Python TF-IDF 向量化 + 文件持久化，兼容 langchain_core `Document`/`Embeddings` 接口；向量持久化到 `rag_data/`（已 gitignore），文档源为根目录 `doc/`
- `app/services/rag_service.py` — 文档加载、分块、向量化、检索；`RAG_MAX_CONTEXT_LEN`（默认 2000）控制注入上下文长度
- `app/routers/rag.py` — `POST /api/rag/search`、`POST /api/rag/index`、`GET /api/rag/status`
- `app/routers/dialogue.py` — `POST /api/v1/dialogue/chat` 支持 RAG 增强（`use_rag`、`rag_top_k` 参数；RAG 无结果时回退正常 Dify 调用）；另有 `/api/v1/dialogue/rag-context` 供 ADH 调用

## 知识库管理（X-Admin-Token 认证）

- `app/models/knowledge.py` + `app/services/knowledge_service.py` + `app/routers/knowledge.py` — `/api/admin/knowledge*` CRUD + `/import`
- 认证：`X-Admin-Token` 请求头，token 来自 `ADMIN_API_TOKEN` 环境变量（`app/core/config.py`）
- 前端：`digitalHumanVue/src/views/admin/KnowledgeView.vue`，token 存 `localStorage` 的 `admin_api_token`
- 导入支持 `.json` 数组和按 `# ` 标题切分的 `.md` 文件

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

- **Visitor 端**: 访客通过数字人 iframe 对话（Dify Chat Agent），系统后台轮询抓取对话内容
- **Admin 端**: 管理后台展示情感趋势、满意度、热点关注和景点热度（数据来自 Dify Workflow 量化分析），以及知识库管理（讲解词/FAQ/资料 CRUD + 批量导入）
- **数据隔离**: 当前仅支持单一虚拟访客（`DIFY_LIGHT4AI_USER`），无多租户/认证

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
- 后端: pytest + 内存 SQLite（见 tests/CLAUDE.md），不使用 mock 库
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
- **Python 环境**: 使用 ADH 自带 `venv/`（Python 3.8.6），不污染主项目环境
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
