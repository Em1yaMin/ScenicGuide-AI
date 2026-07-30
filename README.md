# 景区导览 AI 数字人系统

景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台提供对话抓取与情感分析，前端提供访客端（数字人对话）和管理后台（数据大屏 + 报告）。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.8+ · FastAPI · SQLAlchemy 2.0 (async) · SQLite (aiosqlite) · httpx · LangChain |
| 前端 | Vue 3 · TypeScript · Vite 8 · Pinia · Vue Router · ECharts |
| 部署 | Docker Compose · Nginx |
| 外部服务 | Dify AI 平台（Chat Agent 对话 + Workflow 量化分析）、本地 Live2D 数字人（live2d-frontend） |

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
- **Agent**: Dify Chat Agent（与主项目共用 Dify 实例 `YOUR_DIFY_SERVER_IP`）

启动步骤见上方「**4. 启动本地数字人服务**」。详细部署步骤与故障排查见 `claude.md` 或 `doc/local-digital-human-deploy-plan.md`。

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
├── claude.md                  # AI 辅助开发指南与上下文记忆
├── .pi/                       # pi 编码助手配置与扩展
├── doc/                       # 设计文档目录（Dify 工作流、部署计划等）
├── live2d-frontend/           # 本地 Live2D 数字人（awesome-digital-human-live2d）
├── docker-compose.yml
├── Dockerfile.api             # 后端容器镜像构建
├── Dockerfile.web             # 前端（Nginx）容器镜像构建
├── nginx.conf
└── .env.example              # Docker Compose 部署用环境变量示例
```

## 环境要求

- **Python** ≥ 3.8
- **Node.js** ^22.18.0 || ≥ 24.12.0
- **npm** ≥ 10

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
| `DIFY_RECOMMEND_WORKFLOW_API_KEY` | 推荐问句 Workflow 应用 Key（需在 Dify 平台单独创建） |
| `DIFY_LIGHT4AI_USER` | light4ai 调 Dify 时固定的 user 字段 |
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
> - 存在两个配置文件：`config_template.yaml`（端口 8001，使用腾讯云 ASR + Dify Agent + MiniMax TTS）和 `config_all_in_dify.yaml`（端口 8000，引擎全部走 Dify API）。默认使用 `config_template.yaml`，切换配置时注意端口变化。

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
> 引擎配置详见 `claude.md` 或 `doc/local-digital-human-deploy-plan.md`。当前已配置：腾讯云 ASR + Dify Agent + MiniMax TTS（`Chinese_sweet_girl_nv1` 音色）。

### 6. 访问应用

| 入口 | 地址 |
|------|------|
| 访客端首页 | http://localhost:5173/visitor |
| 访客端数字人对话 | http://localhost:5173/visitor/chat |
| 管理后台数据大屏 | http://localhost:5173/admin |
| 管理后台报告 | http://localhost:5173/admin/report |
| 数字人前端（调试） | http://localhost:3000 |
| 后端 API 文档 | http://localhost:8000/docs |

> 前端 `/api/*` 请求由 Vite 代理转发至 `http://localhost:8000`（见 `vite.config.ts`），开发时无需关心跨域。
>
> 访客端数字人对话页面通过 iframe 嵌入数字人前端（`localhost:3000`），需确保两个服务同时运行。

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
| GET | `/visitor/recommended-questions` | 获取动态推荐问句（后台每 5 分钟从 Dify 刷新） |
| POST | `/visitor/register` | 游客注册 |
| POST | `/visitor/refresh-recommend` | 手动刷新推荐问句缓存 |
| POST | `/recommend` | 个性化路线推荐（接收偏好描述，返回匹配路线） |
| POST | `/dialogue/sync-now` | 手动触发对话抓取 |
| POST | `/analysis/run-now` | 手动触发量化分析 |
| GET | `/admin/dashboard` | 数据大屏聚合数据 |
| GET | `/admin/report` | 分析报告数据 |

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
| 数据大屏 | ✅ 正常（sentiment_trend 7 日连续查询 / satisfaction / hot_issues 均有数据） |
| 手动触发端点 | ✅ 正常（`POST /sync-now` + `/run-now` 手动触发后台逻辑） |
| 本地数字人部署 | ✅ 已部署（TencentAPI ASR + DifyAgent + MiniMax TTS） |
| 动态推荐问句 | ✅ 正常（每 5 分钟从 Dify 刷新，缓存 stale-while-revalidate） |
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

## Dify Workflow 量化分析规格

Workflow 的入参、输出格式、节点编排、后端解析容错等完整规格请查阅：
[`doc/dify配置/workflow-spec.md`](doc/dify配置/workflow-spec.md)。
