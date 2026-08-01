## 始终使用中文回复用户

发出提问或是面向用户指定使用中文，思考不做限制

# Project Overview
景区导览 AI 数字人系统 — Python FastAPI 后端 + Vue 3 前端单体应用，通过 Docker Compose 部署。后端集成 Dify AI 平台提供对话抓取与情感分析，前端提供访客端（数字人对话）和管理后台（数据大屏+报告）。

# Architecture

```
digitalHumanApi/          # Python FastAPI 后端
  app/
    main.py               # 应用入口（lifespan、CORS、全局异常处理）
    core/                 # 配置 (config.py) + 数据库引擎 (database.py)
    data/                 # 本地 JSON 数据源（景点、路线）
    models/               # SQLAlchemy 2.0 ORM 实体 → models/CLAUDE.md
    schemas/              # Pydantic v2 请求/响应模型 → schemas/CLAUDE.md
    services/             # 业务逻辑，外部 API 客户端 → services/CLAUDE.md
    routers/              # FastAPI HTTP 端点 → routers/CLAUDE.md
    tasks/                # 后台 asyncio 循环（polling、analysis）
  tests/                  # pytest 测试套件 → tests/CLAUDE.md

digitalHumanVue/          # Vue 3 + TypeScript + Vite 8 前端
  src/
    api/index.ts          # 集中式端点常量
    views/                # 页面组件 → views/CLAUDE.md
    layouts/              # 布局壳组件 → layouts/CLAUDE.md
    stores/               # Pinia 状态管理
    composables/          # 可复用组合式函数
    styles/               # CSS 自定义属性设计系统

请求流: Vue → fetch(/api/*) → Vite proxy → FastAPI → SQLite
数据流: Dify API ← polling task → SQLite → analysis task → Dify Workflow → SQLite
```

# Commands

| 命令 | 位置 | 用途 |
|------|------|------|
| `uvicorn app.main:app --reload` | `digitalHumanApi/` | 启动 API 开发服务器 (端口 8000) |
| `pytest -v` | `digitalHumanApi/` | 运行测试套件 |
| `npm run dev` | `digitalHumanVue/` | 启动 Vue 开发服务器 (端口 5173) |
| `npm run build` | `digitalHumanVue/` | 生产构建 |
| `docker-compose up -d` | 根目录 | Docker Compose 部署 (api + web + nginx) |

# 本地数字人 (awesome-digital-human-live2d)

## 概述
`live2d-frontend/`（下称 ADH）提供 Live2D 数字人前端 + Python 后端，替代 cloud light4ai 实现本地数字人对话。

**引擎架构**: Registry + Factory + Pool 三层，引擎通过 `@Register("Name")` 装饰器自动注册（详见 `.rpiv/artifacts/research/2026-07-13_19-05-29_local-digital-human-deploy.md`）。

## 当前配置状态（2026-07-15）

| 引擎 | 类型 | 配置 | 状态 |
|------|------|------|------|
| **TencentAPI** | ASR | `configs/engines/asr/tencentAPI.yaml` | ⚠️ 引擎通，麦克风录音调试中 |
| **DifyAgent** | Agent | `configs/agents/difyAgent.yaml` | ✅ 流式对话正常（Dify 1.15.0） |
| **MiniMaxAPI** | TTS | `configs/engines/tts/minimaxTTS.yaml` | ✅ `Chinese_sweet_girl_nv1` 音色 |

### 凭证位置
- **腾讯云 ASR**: AK/SK 来自从腾讯云控制台下载的 SecretKey.csv，APPID `<your-tencent-appid>`，REGION `ap-guangzhou`
- **Dify Agent**: URL `http://<dify-server-ip>/v1`，Key `app-xxxxxxxxxxxxxxxx`，User `<your-dify-user>`
- **MiniMax TTS**: API Key `sk-api-...`，Group ID `<your-minimax-group-id>`，Model `speech-2.8-hd`

### 启动命令
```bash
# 后端 (端口 8000)
cd live2d-frontend/
nohup ./venv/Scripts/python.exe main.py > backend.log 2>&1 &

# 前端 (端口 3000) — 必须绕过 pnpm dev bug
cd live2d-frontend/web/
nohup node node_modules/next/dist/bin/next dev -p 3000 > ../frontend.log 2>&1 &
```

### 关键修改文件（相对于 ADH 根目录）
- `configs/config_template.yaml` — SUPPORT_LIST 精简为仅目标引擎
- `configs/engines/asr/tencentAPI.yaml` — 腾讯云 ASR 凭证
- `configs/engines/tts/minimaxTTS.yaml` — MiniMax TTS 凭证（**新建**，替代 difyTTSWorkflow）
- `configs/agents/difyAgent.yaml` — Dify Agent 凭证
- `digitalHuman/engine/tts/minimaxTTS.py` — MiniMax TTS 引擎（**新建**）
- `digitalHuman/engine/tts/__init__.py` — 注册 MiniMaxAPI
- `digitalHuman/engine/asr/tencentASR.py:44` — **已移除** `req.AppId` 行（SDK 3.1.x 不支持该字段）
- `digitalHuman/agent/core/difyAgent.py` — 新增 `ThinkFilter` 类，流式过滤 `<think>` 标签
- `digitalHuman/server/asrApi.py` — 新增 DEBUG 音频保存到 `debug_audio/` 目录
- `web/app/ui/home/chatbot.tsx` — 录音方案从 `js-audio-recorder` 替换为原生 `MediaRecorder` + 客户端 webm→WAV 转换（16kHz 重采样）

### ASR 已知问题
- `tencentcloud-sdk-python-asr` v3.1.130 中 `SentenceRecognitionRequest` 无 `AppId` 字段，设置会序列化为 `PpId` 导致 `UnknownParameter` 错误 → 已移除该行
- 浏览器麦克风录音链路待验证：旧方案 `js-audio-recorder`（ScriptProcessorNode）在新 Chrome 录音全部为静音；新方案（MediaRecorder）已替换但麦克风输入设备选择仍需调试
- 验证通过的测试：`test/src/audio/zh.wav` 文件 ASR 识别正常，证明后端引擎代码正确

<important if="you are working on the ADH local digital human">
## ADH 注意事项
- **Python 环境**: 使用 ADH 自带的 `venv/`（Python 3.8.6），不污染主项目环境
- **嵌套 git**: ADH 为独立 git 仓库，改动不影响主仓库 `git status`
- **前端启动**: 不要用 `pnpm dev`（会触发失败的依赖检查），直接用 `node node_modules/next/dist/bin/next dev -p 3000`
- **Live2D 默认模型**: 已改为 Hiyori，首次访问需清 localStorage `character-storage`
- **Agent think 过滤**: `difyAgent.py` 中的 `ThinkFilter` 实时过滤 `<think>...</think>`，TTS 不会读思考内容
- **重启后端**: 改 yaml 或 Python 代码后必须重启后端；改前端代码 Next HMR 自动热更
</important>

# Business Context
- **Visitor 端**: 访客通过数字人 iframe 对话（Dify Chat Agent），系统后台轮询抓取对话内容
- **Admin 端**: 管理后台展示情感趋势、满意度、热点关注和景点热度（数据来自 Dify Workflow 量化分析）
- **数据隔离**: 当前仅支持单一虚拟访客（`DIFY_LIGHT4AI_USER`），无多租户/认证

<important if="you are adding a new domain entity end-to-end">
## 添加新领域实体（全栈）
1. 后端: 按 models/CLAUDE.md → schemas/CLAUDE.md → services/CLAUDE.md → routers/CLAUDE.md 顺序
2. 后端: 在 `main.py` 注册 router
3. 前端: 在 `api/index.ts` 添加端点常量 → 创建 View（见 views/CLAUDE.md）→ 添加路由
</important>

<important if="you are adding or modifying environment configuration">
- 后端: `.env` 文件（`digitalHumanApi/.env`），通过 `app/core/config.py` 的 `Settings` Pydantic 模型加载
- 前端: `.env` 文件（`digitalHumanVue/.env`），仅 `VITE_` 前缀变量在客户端可用，类型声明在 `env.d.ts`
- Docker: 通过 `docker-compose.yml` 的 `environment` 字段传入
</important>

<important if="you are writing or modifying tests">
- 后端: pytest + 内存 SQLite（见 tests/CLAUDE.md），不使用 mock 库
- 前端: 暂无测试基础设施
</important>
