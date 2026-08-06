# 景区导览 AI 数字人系统

景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台提供对话抓取与情感分析，前端提供访客端（数字人对话）和管理后台（数据大屏 + 报告）。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.9+ · FastAPI · SQLAlchemy 2.0 (async) · SQLite (aiosqlite) · httpx · LangChain |
| 前端 | Vue 3 · TypeScript · Vite 8 · Pinia · Vue Router · ECharts |
| 部署 | Docker Compose · Nginx |
| 外部服务 | Dify AI 平台（Chat Agent 对话兜底 + Workflow 量化分析）、LangChain agent（对话主链路）+ OpenAI（AI 运营分析）、对话 LLM（opencode-go DeepSeek，见 `doc/llm-adapter-smoke-test.md`）、本地 Live2D 数字人（live2d-frontend） |

## 本地数字人依赖

本项目数字人对话功能由 [awesome-digital-human-live2d](https://github.com/Kedreamix/awesome-digital-human-live2d) 提供，
源码位于 `live2d-frontend/`（已纳入本仓库版本控制）。

| 服务 | 端口 | 说明 |
|------|------|------|
| ADH 后端 | 8001 | FastAPI（ASR/TTS/Agent 引擎注册与调用，默认 8000，因业务 FastAPI 占用改为 8001） |
| ADH 前端 | 3000 | Next.js（Live2D 数字人渲染 + 对话界面） |

**引擎配置**:
- **ASR**: 腾讯云实时语音识别 (TencentAPI)
- **TTS**: MiniMax API (`speech-2.8-hd` + `Chinese_sweet_girl_nv1` 音色)
- **Agent**: LangChainAgent（默认，走本后端 `POST /api/v1/dialogue/chat` LangChain 主链路，后端侧异常自动回落 Dify）；DifyAgent（直连 Dify Chat Agent）保留可切换

启动步骤见上方「**4. 启动本地数字人服务**」。详细部署步骤与故障排查见 `AGENTS.md`（ADH 专节）或 `doc/local-digital-human-deploy-plan.md`。

## 目录结构

```
digitalHuman/
├── digitalHumanApi/          # FastAPI 后端
│   ├── app/
│   │   ├── main.py           # 应用入口（lifespan、CORS、全局异常处理）
│   │   ├── core/             # 配置 (config.py) + 数据库引擎 (database.py)
│   │   ├── models/           # SQLAlchemy ORM 实体
│   │   ├── schemas/          # Pydantic v2 请求/响应模型
│   │   ├── data/             # 本地 JSON 数据源（景点、路线）
│   │   ├── services/         # 业务逻辑 + Dify API 客户端
│   │   ├── routers/          # FastAPI HTTP 端点
│   │   └── tasks/            # 后台 asyncio 循环（polling、analysis）
│   ├── tests/                # pytest 测试套件
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env                  # 后端环境变量（需自行创建）
├── digitalHumanVue/          # Vue 3 前端
│   ├── src/
│   │   ├── api/index.ts      # 集中式端点常量
│   │   ├── views/            # 页面组件
│   │   ├── layouts/          # 布局壳组件（访客端 / 管理后台）
│   │   ├── stores/           # Pinia 状态管理
│   │   ├── composables/      # 可复用组合式函数
│   │   ├── utils/            # 工具函数（fetch 封装、超时处理）
│   │   └── types/            # TypeScript 类型声明
│   ├── public/               # 静态资源
│   ├── vite.config.ts        # 含 /api 代理到后端
│   ├── tsconfig.json / tsconfig.app.json / tsconfig.node.json
│   ├── .editorconfig / .prettierrc.json / .oxlintrc.json / eslint.config.ts
│   └── .env                  # 前端环境变量（需自行创建）
├── AGENTS.md                  # 项目记忆（唯一权威源；CLAUDE.md 为指向它的 symlink）
├── .pi/                       # pi 编码助手配置与扩展
├── doc/                       # 设计文档目录（Dify 工作流、部署计划等）；景区知识库/ 为 RAG 索引文档源
├── live2d-frontend/           # 本地 Live2D 数字人（awesome-digital-human-live2d）
├── docker-compose.yml
├── Dockerfile.api             # 后端容器镜像构建
├── Dockerfile.web             # 前端（Nginx）容器镜像构建
├── nginx.conf
└── .env.example              # Docker Compose 部署用环境变量示例
```

## 前置系统依赖

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| Python | 主后端 ≥ 3.9（建议 3.10+）；**ADH 要求 3.11 或更低** | 主后端 FastAPI；ADH 数字人后端 |
| ffmpeg / ffprobe | 系统包（`sudo apt install ffmpeg`） | ADH 数字人 TTS 链路必需 |
| Node.js | ^22.18.0 \|\| ≥ 24.12.0 | Vue 前端（package.json engines）；ADH Next.js 前端 v14 |
| npm | ≥ 10 | 前端包管理 |

**详细说明**（基于 2026-08-01 实际部署验证）：

- **ffmpeg / ffprobe**：ADH 数字人 TTS 链路必需——MiniMax 返回 MP3，`pydub` 转 WAV 依赖它。缺失时 TTS 静默失败，后端日志报 `No such file or directory: 'ffprobe'`。安装：`sudo apt install ffmpeg`。
- **Python 版本**：主后端 FastAPI 用 Python 3.10+（最低要求 3.9，因 `numpy>=1.26.0` 不再支持 Python 3.8）；**ADH（live2d-frontend）要求 Python 3.11 或更低**——其 `numpy==1.24.4` 的预编译包最高支持到 3.11，在 Python 3.12 下 pip 源码编译失败。若系统默认 Python 3.12，需用 `virtualenv` 创建 3.10 虚拟环境（Ubuntu 的 `python3.10-venv` 包可能缺失，`virtualenv` 是可靠替代）：

  ```bash
  cd live2d-frontend
  virtualenv -p python3.10 venv
  ./venv/bin/pip install -r requirements.txt
  ```

- **Node.js**：数字人 Vue 前端要求 `^22.18.0 || >= 24.12.0`（见 `digitalHumanVue/package.json` 的 `engines`）；ADH Next.js 前端为 v14。

## 本地开发启动

### 1. 配置环境变量

**后端** — 复制并填写 `digitalHumanApi/.env`：

```bash
cd digitalHumanApi
cp .env.example .env
```

需配置的关键变量：

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | SQLite 路径，默认 `sqlite+aiosqlite:///./digital_human.db` |
| `CORS_ORIGINS` | 开发环境填前端地址 `http://localhost:5173`（多个用英文逗号分隔） |
| `DIFY_API_URL` | Dify API 基础地址，如 `https://your-dify.example.com/v1` |
| `DIFY_API_KEY` | Chat Agent 应用 Key（拉取对话） |
| `DIFY_WORKFLOW_API_KEY` | Workflow 应用 Key（触发量化评估） |
| `DIFY_LIGHT4AI_USER` | light4ai 调 Dify 时固定的 user 字段 |
| `OPENAI_API_KEY` | OpenAI API Key（用于 LangChain AI 运营分析，不配置时回退到纯 DB 统计；**当前部署单用 opencode-go 端点**，与 `LLM_*` 同一 key，见 `digitalHumanApi/.env`） |
| `OPENAI_API_BASE` | OpenAI API 基础地址（可选；当前部署 `https://opencode.ai/zen/go/v1`） |
| `OPENAI_MODEL` | OpenAI 模型名（默认 `gpt-4o-mini`；当前部署 `deepseek-v4-flash`） |
| `LLM_API_KEY` | 对话 LLM Key（主端点 opencode-go，阶段1 接入层；与 `OPENAI_*`/`EMBEDDING_*` 完全独立，见 `app/core/llm.py` 与 `doc/llm-adapter-smoke-test.md`） |
| `LLM_API_BASE` | 对话 LLM 主端点（默认 `https://opencode.ai/zen/go/v1`） |
| `LLM_MODEL` | 对话 LLM 主模型（默认 `deepseek-v4-flash`） |
| `LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_API_BASE` / `LLM_FALLBACK_MODEL` | 对话 LLM 备用端点（**船长决策 2026-08-06：备用同主端点 opencode-go**——opencode 服务本质转发 DeepSeek 官方 key，无需另配官方备用；主端点未配置 key 时自动切换） |
| `LLM_TEMPERATURE` | 对话 LLM 默认采样温度（默认 `0.7`） |
| `DIALOGUE_ENGINE` | 对话引擎开关（默认 `langchain`：LangChain agent 主链路，异常自动回落 Dify；`dify`：强制走 Dify Chat Agent），见 `app/services/chat_agent.py` |
| `EMBEDDING_API_KEY` | Minimax Embedding API Key（RAG 向量化，与 OPENAI_*/LLM_* 独立；不配置时自动降级本地 TF-IDF，详见 AGENTS.md「本地 RAG 知识库」） |
| `EMBEDDING_API_BASE` | Minimax Embedding 端点（默认 `https://api.minimaxi.com/v1`，**原生 API 格式**：请求 `texts`+`type`（query/db）、响应 `vectors`，实测模型 `embo-01`，见 `doc/phase1-e2e-verification.md`） |
| `EMBEDDING_MODEL` | Embedding 模型名（默认 `embo-01`） |
| `MEMORY_DB_PATH` | 短期记忆 SQLite 存储路径（默认 `memory_store.db`，独立于主库 digital_human.db，标准库 sqlite3，无额外依赖，见 AGENTS.md「短期记忆持久化」） |
| `MEMORY_TTL_DAYS` | 会话 TTL（天，默认 7）：滑动窗口，每次对话刷新过期时间，到期会话由后台任务清理 |
| `MEMORY_CLEANUP_INTERVAL_SECONDS` | 过期会话清理周期（秒，默认 3600）：启动时立即清理一次 + 定期清理 |
| `SSH_HOST` / `SSH_PORT` / `SSH_USER` / `SSH_PASSWORD` | 部署 Dify 的远程服务器（可选） |

**前端** — 复制并填写 `digitalHumanVue/.env`：

```bash
cd digitalHumanVue
cp .env.example .env
```

| 变量 | 说明 |
|------|------|
| `VITE_LIGHT4AI_URL` | light4ai 数字人 iframe 服务地址（如 `http://localhost:3000`） |
| `VITE_API_BASE` | 后端 API 基础地址，开发默认 `http://localhost:8000`（Vite 代理目标） |
| `VITE_LIVE2D_MODEL` | Live2D 模型名称（可选，默认 `hiyori`） |
| `VITE_ADH_IFRAME_ORIGIN` | 数字人 iframe 的 origin，用于 postMessage 安全校验（可选，默认 `*`） |

> **用户认证**（前端）：登录/注册页为顶层路由 `/login`、`/register`，访客端与管理后台均可直达；游客模式免登录直接使用。token 持久化于浏览器 `localStorage`（`auth_token`/`auth_user`），对话页自动携带 token（iframe `auth_token` 参数透传 ADH）。**无需新增前端环境变量**；后端 JWT 相关配置（`JWT_SECRET`、`JWT_EXPIRES_MINUTES`、`GUEST_TOKEN_EXPIRES_MINUTES`）见 `digitalHumanApi/.env.example`。

### 2. 快速启动（一键后台）

以下命令在 PowerShell 中执行，将所有服务以后台窗口启动：

```powershell
# 主后端 (8000)
Start-Process -WindowStyle Hidden -WorkingDirectory "$PWD/digitalHumanApi" -FilePath python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--reload'

# 主前端 (5173)
Start-Process -WindowStyle Hidden -WorkingDirectory "$PWD/digitalHumanVue" -FilePath npm -ArgumentList 'run', 'dev'

# 数字人后端 (8001) — 注意端口已从默认 8000 改为 8001，避免冲突
Start-Process -WindowStyle Hidden -WorkingDirectory "$PWD/live2d-frontend" -FilePath python -ArgumentList 'main.py'

# 数字人前端 (3000) — 若 pnpm run dev 启动失败，可降级为直接调用
Start-Process -WindowStyle Hidden -WorkingDirectory "$PWD/live2d-frontend/web" -FilePath node -ArgumentList 'node_modules/next/dist/bin/next', 'dev', '-p', '3000'
```

> **注意**：
> - 命令中的 `$PWD` 需替换为项目根目录绝对路径（如 `E:/project_file/digitalHuman`），或将终端 cd 到项目根目录后执行。
> - 数字人后端端口已在 `config_template.yaml` 中改为 **8001**（原 8000 被主后端占用）。
> - 数字人前端需在 `web/.env` 中设置 `NEXT_PUBLIC_ADH_SERVER_PORT=8001`，否则心跳连错端口导致 "Soul is death!"。
> - 存在两个配置文件：`config_template.yaml`（端口 8001，使用腾讯云 ASR + LangChainAgent（默认，走本后端对话链路）+ MiniMax TTS）和 `config_all_in_dify.yaml`（端口 8000，引擎全部走 Dify API）。默认使用 `config_template.yaml`，切换配置时注意端口变化。

### 3. 启动后端（手动）

```bash
cd digitalHumanApi
pip install -r requirements.txt          # 首次运行安装依赖
python -m uvicorn app.main:app --reload   # 启动开发服务器，端口 8000
```

启动成功标志：
- 控制台输出 `Application startup complete.`
- 访问 http://localhost:8000/api/v1/health 返回 `{"status":"ok"}`
- API 文档 http://localhost:8000/docs 可打开
- 后台轮询任务自动运行，连接 Dify 拉取对话

### 4. 启动前端

```bash
cd digitalHumanVue
npm install                              # 首次运行安装依赖
npm run dev                              # 启动开发服务器，端口 5173
```

启动成功标志：
- 控制台显示 `VITE ready` 与 `Local: http://localhost:5173/`
- 浏览器访问 http://localhost:5173/ 可正常加载页面

### 5. 启动本地数字人服务（live2d-frontend）

数字人对话依赖 [awesome-digital-human-live2d](https://github.com/Kedreamix/awesome-digital-human-live2d)，需独立启动其后端和前端。

```powershell
# 数字人后端（FastAPI，端口 8001，注意与主项目 8000 区分）
cd live2d-frontend
python -m venv venv                          # 创建虚拟环境（首次运行）
venv\Scripts\pip install -r requirements.txt # 安装依赖（首次运行）
venv\Scripts\python.exe main.py

# 数字人前端（Next.js，端口 3000，若 pnpm run dev 启动失败，可降级为直接调用）
cd live2d-frontend\web
pnpm run dev    # 或 node node_modules\next\dist\bin\next dev -p 3000
```

> **两个命令需分别在各自的终端窗口中运行**（不要关掉）。首次启动前端可能需要清 localStorage `character-storage` 键以加载默认 Hiyori 模型。
>
> 引擎配置详见 `AGENTS.md`（ADH 专节）或 `doc/local-digital-human-deploy-plan.md`。当前已配置：腾讯云 ASR + LangChainAgent（默认，走本后端 LangChain 主链路，异常自动回落 Dify）+ MiniMax TTS（`Chinese_sweet_girl_nv1` 音色）。

### 6. 访问应用

| 入口 | 地址 |
|------|------|
| 访客端首页 | http://localhost:5173/visitor |
| 访客端数字人对话 | http://localhost:5173/visitor/chat |
| 管理后台数据大屏 | http://localhost:5173/admin |
| 管理后台报告 | http://localhost:5173/admin/report |
| 管理后台知识库 | http://localhost:5173/admin/knowledge |
| 数字人前端（调试） | http://localhost:3000 |
| 后端 API 文档 | http://localhost:8000/docs |

> 前端 `/api/*` 请求由 Vite 代理转发至 `http://localhost:8000`（见 `vite.config.ts`），开发时无需关心跨域。
>
> 访客端数字人对话页面通过 iframe 嵌入数字人前端（`localhost:3000`），需确保两个服务同时运行。

## 本地部署指南（Linux / 局域网）

适用于在 Linux 服务器（或宿主机）上以开发模式本地部署、供局域网/远程访问的场景（部署经验基于 2026-08-01 实测）。生产环境请使用下方 Docker Compose 部署。

### 1. 端口规划

| 服务 | 端口 | 配置位置 |
|------|------|---------|
| FastAPI 主后端 | 8000 | `digitalHumanApi/`（`uvicorn app.main:app`） |
| ADH 数字人后端 | 8001 | `live2d-frontend/configs/config_template.yaml` 的 `SERVER.PORT`（默认 8000，因主后端占用改为 8001） |
| ADH 数字人前端（Next.js） | 3000 | `live2d-frontend/web/` |
| Vue 管理/访客前端（Vite） | 5173 | `digitalHumanVue/` |

### 2. 环境变量配置

**Vue 前端** — `digitalHumanVue/.env`（复制 `.env.example`）：

| 变量 | 说明 |
|------|------|
| `VITE_LIGHT4AI_URL` | 数字人 iframe 地址。**局域网/远程访问必须填 `http://<服务器IP>:3000`，不能填 `localhost`**——否则访客浏览器会指向自己机器上的 3000 端口 |
| `VITE_API_BASE` | 后端 API 地址，默认 `http://localhost:8000`（开发时由 Vite 代理转发，可不改） |

**ADH 前端** — `live2d-frontend/web/.env`（复制 `.env.example`）：

| 变量 | 说明 |
|------|------|
| `NEXT_PUBLIC_ADH_SERVER_PORT` | **必须配置为 `8001`**。不配置则前端请求打到 8000 主后端，报 CORS/400，数字人显示 "Soul is death!" |

**主后端** — `digitalHumanApi/.env`：按上方「1. 配置环境变量」填写 `DIFY_API_URL`、`DIFY_API_KEY`、`DIFY_WORKFLOW_API_KEY` 等（启动时校验非空，缺失会启动失败）。

### 3. 启动步骤（开发模式）

```bash
# 1) 主后端 (8000)
cd digitalHumanApi
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) Vue 前端 (5173) —— 必须 --host 0.0.0.0，否则宿主机/局域网无法访问
cd digitalHumanVue
npm run dev -- --host 0.0.0.0

# 3) ADH 数字人后端 (8001) —— venv 为 Python 3.10，依赖见 requirements.txt
cd live2d-frontend
./venv/bin/python main.py

# 4) ADH 数字人前端 (3000)
cd live2d-frontend/web
npm run dev
```

四个服务需分别在各自终端中运行。若 ADH 前端 `npm run dev` 启动失败，可降级为 `node node_modules/next/dist/bin/next dev -p 3000`。

### 4. 防火墙放行

```bash
sudo ufw allow 8000/tcp   # 主后端
sudo ufw allow 5173/tcp   # Vue 前端
sudo ufw allow 3000/tcp   # ADH 前端
sudo ufw allow 8001/tcp   # ADH 后端
```

> 局域网内其他机器访问入口：访客端 `http://<服务器IP>:5173/visitor`；数字人 iframe 会按 `VITE_LIGHT4AI_URL` 加载 `http://<服务器IP>:3000`。

## Docker Compose 部署

用于生产或一体化部署（api + web + nginx）：

```bash
# 在项目根目录，先填写根目录 .env（参考 .env.example）
cp .env.example .env   # 填入 DIFY_API_URL / DIFY_API_KEY 等

docker-compose up -d --build
```

部署后：
- 前端经 Nginx 提供：http://localhost/ （80 端口）
- Nginx 将 `/api/` 反向代理到后端容器 `api:8000`
- 后端健康检查端点：`/api/v1/health`

> **如果 Dify 部署在宿主机而非 Docker 网络中**，容器内无法通过 `localhost` 访问宿主机 Dify。需在 `docker-compose.yml` 的 `api` 服务下添加 `extra_hosts`：
> ```yaml
> api:
>   extra_hosts:
>     - "host.docker.internal:host-gateway"
> ```
> 然后将 `.env` 中的 `DIFY_API_URL` 设为 `http://host.docker.internal/v1`。

## API 端点概览

所有端点前缀 `/api/v1`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/auth/register` | 用户注册（用户名唯一，密码至少 6 位） |
| POST | `/auth/login` | 登录（返回 JWT + 用户信息） |
| GET | `/auth/me` | 当前登录用户（Bearer token，无效 token 401） |
| POST | `/auth/guest` | 签发短期游客 token（匿名，30 分钟） |
| GET | `/visitor/recommended-questions` | 获取动态推荐问句（后台每 5 分钟由本地 LLM 生成刷新，阶段3 T3 已脱离 Dify） |
| POST | `/visitor/register` | 游客注册 |
| POST | `/visitor/refresh-recommend` | 手动刷新推荐问句缓存 |
| POST | `/recommend` | 个性化路线推荐（接收偏好描述，返回匹配路线） |
| POST | `/dialogue/chat` | 对话（**双轨**：默认 LangChain agent 主链路，结构化工具直查景点/路线 + 知识检索 + 规则推荐，多轮会话**短期记忆持久化**（SQLite 落盘 + TTL，重启可恢复，见下方「短期记忆持久化」）；异常自动回落 Dify；`DIALOGUE_ENGINE=dify` 强制走 Dify，响应字段兼容 `answer/conversation_id/rag_context/rag_sources`；`streaming=true` 时返回 SSE 流式事件，见下方「SSE 流式对话」） |
| POST | `/dialogue/sync-now` | 手动触发对话抓取 |
| POST | `/analysis/run-now` | 手动触发量化分析 |
| GET | `/analysis/overview` | AI 运营分析概览（支持 `?days=7/30/90`） |
| GET | `/analysis/trends` | 趋势数据（每日对话量 + 满意度均分） |
| GET | `/admin/dashboard` | 数据大屏聚合数据 |
| GET | `/admin/report` | 分析报告数据 |

管理接口（前缀 `/api/admin/knowledge`，需 `X-Admin-Token` 认证）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/knowledge` | 知识库列表（支持分类/关键词筛选、分页） |
| POST | `/api/admin/knowledge` | 创建知识库条目 |
| PUT | `/api/admin/knowledge/{id}` | 编辑知识库条目 |
| DELETE | `/api/admin/knowledge/{id}` | 删除知识库条目 |
| POST | `/api/admin/knowledge/import` | 批量导入（JSON/Markdown 文件） |

> **知识库与 RAG 联动**：管理后台知识库增/删/改/导入后自动同步重建 `knowledge` 命名空间向量索引，后台检索立即可见；结构化景点/路线数据不走向量，由推荐 Agent 结构化工具直查（零幻觉原则）。

RAG 接口（`/api/rag/*`，统一检索入口，对话 RAG、推荐 Agent 与后台共用）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/rag/search` | 语义检索；`namespace` 逗号分隔限定（knowledge/documents/user_memory），默认 knowledge+documents；响应含 `embedding_backend`/`degraded` 标记 |
| POST | `/api/rag/index` | 全量重新索引；默认 documents 命名空间，文档源限定 `doc/景区知识库/`（三份景区知识 md），不索引架构/设计文档 |
| GET | `/api/rag/status` | 各命名空间状态（文档数、embedding 后端、降级标记） |

## SSE 流式对话（阶段1 T3）

`POST /api/v1/dialogue/chat` 携带 `streaming: true` 时返回 SSE（`text/event-stream`），前端可实时展示 agent 思考过程（工具调用步骤）与逐字回答。非流式（默认）行为不变。**无新增配置项**：流式/非流式共用 `DIALOGUE_ENGINE` 双轨开关与 `LLM_*`/`DIFY_*` 配置。

### 事件格式（`data: {json}` 帧）

| type | 字段 | 说明 |
|------|------|------|
| `step` | `tool` / `label` | agent 工具调用步骤（正在检索知识库 / 查询景点 / 推荐路线…，按调用 id 去重） |
| `token` | `text` | 最终回答增量（逐字输出；DeepSeek 思考过程由 langchain-openai 剥离，不会外泄） |
| `done` | `conversation_id` / `rag_context` / `rag_sources` | 流式完成（字段与 `ChatResponse` 对齐，`conversation_id` 供多轮续接） |
| `error` | `message` / `fallback` | 链路错误；`fallback=true` 表示主链路异常已切换 Dify 兜底（清空部分回答继续输出），`false` 表示双链路均失败 |

事件序列：LangChain 链路 `step → token → done`；故障回落时先发 `error(fallback=true)` 再发 Dify 流式事件；双链路均失败时以 `error(fallback=false)` 礼貌报错结束。

```json
{"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"}
{"type": "token", "text": "景区 8:00 开放"}
{"type": "done", "conversation_id": "abc", "rag_context": "", "rag_sources": []}
```

Dify 兜底链路同样流式（`response_mode=streaming`），`<think>...</think>` 块经 `ThinkFilter` 实时过滤（参考 live2d-frontend 实现，见 `app/services/dify_stream.py`）。

### 前端消费

`digitalHumanVue/src/composables/useDialogueStream.ts` 以 fetch + ReadableStream 逐帧解析 SSE（无额外依赖），`ChatView.vue` 右侧面板提供文本对话：步骤 chips 可视化思考过程、回答逐字渲染（含光标）、推荐问句一键发送、主链路回落提示。

## 双模式对话（阶段3 T1：简单问答 / 复杂规划）

船长决策 2026-08-06 方案B：对话入口先做**意图分类**（`app/services/intent_classifier.py`，轻量规则、零延迟），按任务复杂度分流：

| 分类 | 判定 | 路径 | 特征 |
|------|------|------|------|
| 简单 | 单工具可答（开放时间 / 在哪 / 怎么去…） | 快速路径：现有 `create_agent` 流程 | 零延迟增加，行为与阶段2 一致 |
| 复杂 | 多步骤意图（规划 / 安排 / 一日游 / 带老人小孩 / 路线推荐…） | 规划路径：LangGraph 显式图（`app/services/planner_agent.py`） | 规划 → 多步执行 → 总结，SSE 步骤可视化思考过程 |

**规划路径**（LangGraph 状态图，零新依赖）：

- **规划**：LLM 按用户需求生成步骤序列（≤6 步，每步一个工具调用，JSON 输出经 `llm.astream` 累积后解析——图内模型调用被 langgraph 统一转为流式，`with_structured_output` 不兼容流式分块）；工具集复用快速路径的 6+1 工具（结构化直查 + 知识检索 + 规则推荐）
- **执行**：逐步骤确定性调用工具（`BaseTool.ainvoke` 线程池执行，`recommend_routes` 内含 `asyncio.run` 安全）；单步失败记录错误继续，不中断
- **总结**：LLM 聚合全部工具结果生成结构化最终回答（流式逐字输出，经 `stream_mode="messages"` 按 `langgraph_node=="summarize"` 区分）

**SSE 事件序列（复用现有 step/token/done 契约，前端零改动）**：`step(正在为您规划行程…) → step(每个工具执行) → step(正在生成行程安排…) → token(总结增量) → done`。长期记忆注入（T3）在规划路径同样生效（记忆上下文随 messages 进入规划与总结阶段）。

**安全降级（双轨回落不变）**：分类失败默认简单（快速路径）；规划路径任何异常（规划生成失败 / 步骤全非法 / 总结为空 / LLM 故障）静默降级快速路径，再由路由层兜底 Dify——对话永不中断。安全边界不变：工具集不含预订/账号/支付类操作，prompt 引导人工客服。

**回退开关**：`AGENT_DUAL_MODE=false`（`digitalHumanApi/.env`）全部走快速路径，行为与阶段2 完全一致。详见 [`doc/phase3-dual-mode.md`](doc/phase3-dual-mode.md)。

## 短期记忆持久化（阶段2 T1）

多轮会话上下文已从**进程内 dict（重启即失）**升级为**SQLite 落盘 + 进程内 TTL 缓存**（登录用户与游客均享短期记忆，会话级、按 `conversation_id`）：

- **重启恢复**：重启后同 `conversation_id` 续聊不丢多轮上下文（SQLite 懒恢复；LRU 淘汰只出缓存、不出库）
- **TTL 滑动窗口**：默认 7 天（`MEMORY_TTL_DAYS`），每次对话刷新过期时间；过期会话由后台任务定期清理（启动立即执行一次 + 每 `MEMORY_CLEANUP_INTERVAL_SECONDS` 秒，默认 1 小时）＋ 访问时惰性清理双保险
- **双轨回落兼容**：会话归属判定（`has_conversation`）随 `source` 字段持久化——LangChain 生成的 id 对 Dify 依旧未知（回落传空串新建会话），Dify 自己的 id 原样透传延续上下文
- **降级不阻断**：SQLite 写失败仅告警，记忆降级为进程内，对话不中断
- 配置项：`MEMORY_DB_PATH` / `MEMORY_TTL_DAYS` / `MEMORY_CLEANUP_INTERVAL_SECONDS`，见 `digitalHumanApi/.env.example`

存储层与读写策略等实现细节见 AGENTS.md「短期记忆持久化」。

## 记忆系统分层（登录用户 vs 游客）

记忆分两层，按用户身份差异化生效（船长决策 2026-08-05/06 确认，阶段2 收官任务全链路联调验证，详见 [`doc/phase2-memory-verification.md`](doc/phase2-memory-verification.md)）：

| 维度 | 短期记忆（T1） | 长期记忆（T2 写入 + T3 注入） |
|------|----------------|-------------------------------|
| 存储 | 会话级 SQLite（`memory_store.db`），按 `conversation_id` | 跨会话 user_memory 向量库（`rag_data/user_memory_docs.json`），按 user 隔离 |
| 登录用户 | ✅ 同会话多轮上下文 | ✅ 对话后提取事实/偏好写入；新会话首轮检索注入（个性化回答） |
| 游客（visitor / guest-*） | ✅ 同会话多轮上下文 | ❌ 不写不读（不调用提取 LLM、不注入） |
| 生效时机 | 每次对话追加，TTL 7 天滑动窗口 | 对话完成后台异步提取；新会话首轮注入（仅首轮、top_k=3、时效 ≤180 天） |
| 重启 | SQLite 懒恢复，同 id 续聊不丢 | 文件持久化，重启后按 user 可检索 |

> 行为速记：**登录用户 = 短期 + 长期；游客 = 仅短期**。真实环境验证：登录用户新会话问「还记得我吗」→ 回答引用用户画像（带老人小孩/自然风光）；游客新会话同问 → 明确回答「没有记忆功能」；游客多轮会话上下文承接正常；同事实重复表达覆盖不膨胀。

## 用户体系（注册/登录/JWT）

后端提供最小可用用户体系，游客免登录可继续使用对话功能：

- **注册/登录**：`POST /api/v1/auth/register`、`POST /api/v1/auth/login`（凭据校验失败返回 401，登录成功签发 JWT，有效期默认 24 小时）
- **游客身份**：`POST /api/v1/auth/guest` 签发短期 token（默认 30 分钟，匿名、无持久化记录）
- **对话接口用户标识**：`POST /api/v1/dialogue/chat` 优先从 `Authorization: Bearer <token>` 解析用户（登录用户 → 用户 id；游客 token → 游客 id）；无 token 时向后兼容回退请求体 `user` 字段（默认 `visitor`）；token 无效时降级为 `visitor` 并写告警日志（不阻断对话）。`/auth/me` 等认证接口对无效 token 严格返回 401
- **安全**：密码使用 pwdlib[bcrypt] 哈希存储，绝不落明文；日志不打印 token/密码
- **配置**：`JWT_SECRET`（生产必须设置，留空时启动生成随机临时密钥、重启后 token 失效）、`JWT_EXPIRES_MINUTES`（默认 1440）、`GUEST_TOKEN_EXPIRES_MINUTES`（默认 30），见 `digitalHumanApi/.env.example`

## 长期记忆（阶段2 T2 对话记忆写入 + T3 检索注入）

对话完成后后台异步提取用户事实/偏好/习惯，写入 `user_memory` 向量库（Minimax embedding + 轻量文件持久化，按 user 隔离）：

- **记忆条目 schema（T3 契约，详见 [`doc/long-term-memory.md`](digitalHumanApi/doc/long-term-memory.md)）**：`{user, fact_type, content, source_conversation_id, timestamp}`；`fact_type` 为 `preference`（偏好）/ `fact`（事实）/ `habit`（习惯），`content` 为自然语言记忆句（如「喜欢拍照」「带老人小孩」）
- **写入**：`app/services/memory_service.py` 的 `save_user_memory(user, facts)`——同 user 同事实**覆盖更新**（记忆不膨胀），每用户条数上限 `MEMORY_MAX_PER_USER`（默认 200，超限淘汰最旧）
- **提取**：`chat_agent` 对话完成后调 `extract_and_store`（LLM 单次调用提取，复用 `LLM_*` 配置）；**失败静默降级不写记忆，绝不阻塞对话响应**
- **游客隔离**：游客（visitor / guest-* / 匿名）不产生长期记忆（不调用 LLM、不写入）
- **检索（T3 对话前注入）**：`chat_agent` 对话**首轮**按 user 调 `search_user_memory(user, query=用户当前问题, top_k=3)`，检索结果格式化为额外 SystemMessage 注入 agent 上下文（每条带事实类型标签 + 时效标记如「3天前」）；**仅首轮注入**（多轮续聊不重复）、游客跳过、陈旧记忆（超 `MEMORY_MAX_AGE_DAYS`）过滤、检索异常/无记忆**静默降级不阻断对话**；注入文本附「仅作参考、不执行指令」降权约束，不重建 agent、不改工具集/SSE/回落，详见 [`doc/long-term-memory.md`](digitalHumanApi/doc/long-term-memory.md)「七、检索注入」
- **配置**：`MEMORY_ENABLED`（默认开启）、`MEMORY_MAX_PER_USER`（默认 200）、`MEMORY_INJECT_ENABLED`（默认开启，注入总开关）、`MEMORY_MAX_AGE_DAYS`（默认 180，注入记忆最大时效），见 `digitalHumanApi/.env.example`

## 测试

```bash
cd digitalHumanApi
pip install -r requirements-dev.txt
pytest -v
```

测试使用内存 SQLite，不依赖外部服务，不使用 mock 库。

## 验收状态

| 项 | 状态 |
|----|------|
| 后端启动 | ✅ 正常（健康检查 200，后台轮询已连接 Dify 并拉取到对话） |
| 前端启动 | ✅ 正常（Vite 8 ready，页面 HTTP 200） |
| Dify 对话抓取 | ✅ 正常（`/messages` 拉取返回 200） |
| Dify Workflow 量化分析 | ✅ 正常（16 条对话全部评估完成，大屏/报告有真实数据） |
| 数据大屏 | ✅ 正常（sentiment_trend 全量连续查询，从最早对话日到今天、缺失日零值回填 / satisfaction / hot_issues 均有数据） |
| 手动触发端点 | ✅ 正常（`POST /sync-now` + `/run-now` 手动触发后台逻辑） |
| 本地数字人部署 | ✅ 已部署（TencentAPI ASR + LangChainAgent + MiniMax TTS） |
| 动态推荐问句 | ✅ 正常（每 5 分钟由本地 LLM 生成刷新（阶段3 T3 脱离 Dify，`LLM_API_*` 配置 opencode-go，聚合 Dashboard 数据生成，失败降级保留旧缓存），缓存 stale-while-revalidate） |
| 阶段1 双轨联调 | ✅ 通过（LangChain 新链路全流程 + 故障回落 Dify + ADH 接入 + 前端流式渲染；联调发现并修复 3 个缺陷：Minimax Embedding 原生 API 格式、ADH SSE 事件契约、回落错误处理，详见 [`doc/phase1-e2e-verification.md`](doc/phase1-e2e-verification.md)） |
| 阶段2 T1 短期记忆 | ✅ 实现完成（会话历史 SQLite 落盘 + TTL 滑动窗口过期清理 + 重启恢复，存储层/清理任务/集成单测覆盖，测试全绿） |
| 阶段2 T2 长期记忆写入 | ✅ 实现完成（对话后后台 LLM 提取用户事实/偏好写入 user_memory 向量库，按 user 隔离、同事实覆盖、条数上限，游客不写，失败静默降级） |
| 阶段2 T3 长期记忆检索注入 | ✅ 实现完成（对话首轮按 user 检索记忆注入 agent 上下文，仅首轮注入/游客跳过/时效过滤/异常静默降级，注入逻辑与端到端单测覆盖，全量 248 测试全绿） |
| 阶段2 T4 登录生效联调（收官） | ✅ 通过（真实环境：登录用户注册/登录→多轮对话→长期记忆写入 user_memory（5 条）→新会话检索注入（回答引用用户画像）；游客无 token / guest token 降级正确（短期正常、无长期记忆、无注入）；同事实覆盖去重；无联调缺陷，详见 [`doc/phase2-memory-verification.md`](doc/phase2-memory-verification.md)） |
| 阶段3 T1 双模式对话 | ✅ 实现完成（意图分类层分流：简单问答→快速路径零延迟、复杂任务→LangGraph 规划图（规划→多步执行→总结）SSE 步骤可视化；分类失败/规划异常静默降级快速路径→Dify；`AGENT_DUAL_MODE=false` 可整体回退；分类/规划/双模式集成单测 + 全量 304 测试全绿，真实环境冒烟验证见 [`doc/phase3-dual-mode.md`](doc/phase3-dual-mode.md)） |
| Docker Compose | 已配置，未在本次验收中实际构建 |

## 常见问题

**Q: 数字人页面显示 "Soul is death!"？**
A: 这是 ADH 前端 WebSocket 心跳连不上后端的提示。检查：
1. 数字人后端是否启动（`curl http://localhost:8001/adh/agent/v0/list` 应返回 200）
2. 数字人前端 `web/.env` 中是否设置了 `NEXT_PUBLIC_ADH_SERVER_PORT=8001`（默认 8000 会连到主后端）
3. 改完 `.env` 后需重启数字人前端才能生效

**Q: 后端启动后日志出现 `Dify trigger_workflow failed`？**
A: Workflow 量化分析触发失败的可能原因：
1. `DIFY_WORKFLOW_API_KEY` 未配置或指向错误的 Workflow 应用——需在 Dify 平台确认该 Workflow 的入参变量名为 `input`、输出变量名为 `text`，且输出为结构化 JSON（详见 doc/dify配置/workflow-spec.md）。
2. Dify 服务器（`DIFY_API_URL`）网络不通或临时不可达——连接失败会自动重试，恢复后自动续上。
3. 对话内容过短（如 "你好"）——Workflow 仍会正常返回 `neutral/3/空标签`，属预期行为。

**Q: 大屏数据为空？**
A: 大屏数据依赖 Workflow 量化分析结果。若 `evaluations` 表为空，说明尚未有对话被评估。可手动触发一次：`curl -X POST http://localhost:8000/api/v1/analysis/run-now`。后台分析任务每 5 分钟自动运行一次。

**Q: 前端 `/api` 请求 404 或跨域报错？**
A: 确认后端已启动在 8000 端口，且 `digitalHumanVue/.env` 的 `VITE_API_BASE=http://localhost:8000` 配置正确。开发环境由 Vite 代理处理，无需后端 CORS 放行（生产由 Nginx 同源处理）。

**Q: 数字人对话框空白？**
A: 检查 `digitalHumanVue/.env` 的 `VITE_LIGHT4AI_URL` 是否指向可访问的 light4ai 服务地址。

**Q: ADH 前端 `npm install` 报 nextui peer 依赖冲突？**
A: 用 `npm install --legacy-peer-deps` 安装。若编译报 `'alert' is not exported from '@nextui-org/theme'`，升级 `@nextui-org/react`、`@nextui-org/system`、`@nextui-org/theme` 到 latest 后重启（清掉 `.next` 缓存）。

**Q: Vue 前端 vite 报 `Cannot find native binding`（rolldown）？**
A: 依赖安装不完整所致，执行 `npm ci` 重装依赖。

**Q: 数字人 TTS 无声音？**
A: 检查 ffmpeg 是否安装——后端日志报 `No such file or directory: 'ffprobe'` 即为缺失，执行 `sudo apt install ffmpeg` 后重启 ADH 后端。

**Q: 页面空白 + 控制台报 `crypto.randomUUID is not a function`？**
A: `crypto.randomUUID()` 仅在安全上下文（HTTPS 或 localhost）可用，通过局域网 IP + 明文 HTTP 访问时浏览器无此 API。项目已在 `digitalHumanVue/src/utils/uuid.ts` 实现 fallback（降级为 `Math.random` + `Date.now` 生成 UUID v4）；旧版本需要同样处理。

**Q: 语音输入不可用（麦克风点击无响应、控制台报 `navigator.mediaDevices is undefined`、麦克风报 error 1）？**
A: 语音功能需要**安全上下文**（HTTPS 或 localhost）。ADH 数字人的语音输入（`navigator.mediaDevices.getUserMedia`）与部分 Web API（如 `crypto.randomUUID`）仅限安全上下文可用；通过**局域网 IP + 明文 HTTP**（如 `http://<服务器IP>:3000`）访问时浏览器会直接禁用，表现即控制台报 `navigator.mediaDevices is undefined`、麦克风报 error 1、点击无响应。这是浏览器硬性安全策略，代码无法绕过。配置方法：
1. **临时调试**：SSH 端口转发 `ssh -L 3000:127.0.0.1:3000 <user>@<服务器IP>` 后访问 `http://localhost:3000`
2. **正式使用**：部署 HTTPS（nginx/caddy 反代 + 证书）

管理后台等非语音功能不受此限制。本项目已内置 `crypto.randomUUID` 的 fallback（见 `digitalHumanVue/src/utils/uuid.ts`），因此访客端非语音页面可在明文 HTTP 下正常运行，仅语音输入受限。

**Q: ADH 数字人凭据在哪里配置？**
A: 凭据不写入代码库，配置在 ADH 引擎配置文件中：`live2d-frontend/configs/engines/tts/minimaxTTS.yaml`（MiniMax TTS）、`live2d-frontend/configs/engines/asr/tencentAPI.yaml`（腾讯云 ASR）、`live2d-frontend/configs/agents/langchainAgent.yaml`（默认引擎，`LLM_API_URL` 指向本后端，可选 Bearer token）、`live2d-frontend/configs/agents/difyAgent.yaml`（Dify Agent，保留可切换直连）。修改后需重启 ADH 后端生效。

## Dify Workflow 量化分析规格

Workflow 的入参、输出格式、节点编排、后端解析容错等完整规格请查阅：
[`doc/dify配置/workflow-spec.md`](doc/dify配置/workflow-spec.md)。
