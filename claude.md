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
