# 多环境部署配置（批次8 C7）

三档环境定义与配置差异、切换指引。环境标识变量 `APP_ENV`（dev/staging/prod）
在启动日志与健康检查 `GET /api/v1/health`（响应含 `app_env`）透出，便于运维
确认当前档位；非法值启动即报错（`app/core/config.py::_validate_app_env`）。

## 三档环境定义

| 档位 | 名称 | 用途 | 部署形态 | 数据 |
|------|------|------|----------|------|
| `dev` | 本机开发 | 日常开发/单测/本地联调 | 本机四服务（uvicorn 8000 + Vue 5173 + ADH 3000/8001） | 本地 SQLite，可随意清空 |
| `staging` | VM 联调演示 | 客户/船长演示、集成联调 | Docker Compose（api+web）或 VM 本机四服务（演示环境 192.168.16.132） | 演示数据，需可恢复（见备份策略） |
| `prod` | 正式部署 | 线上正式运营 | Docker Compose（api+web）+ 外网域名 | 真实业务数据，必须备份 |

## 配置差异速查

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| `APP_ENV` | `dev` | `staging` | `prod` |
| `DEBUG` | `true`（详细日志/堆栈） | `false` | `false` |
| `CORS_ORIGINS` | 默认白名单（localhost/127.0.0.1/192.168.16.132 × 5173/8001/3000）即可 | 默认白名单可加演示入口域名 | **必须**显式收紧为实际部署域名 |
| `CORS_ALLOW_CREDENTIALS` | `true`（JWT 透传需要，白名单不可含 `*`） | `true` | 按需（无凭证场景可 false + `*`） |
| 认证限流 | 默认参数（宽松，便于联调） | 默认或略收紧 | **收紧**（见下「限流参数建议」） |
| 日志级别 | DEBUG（`DEBUG=true` → 根级别 DEBUG） | INFO | INFO/WARNING（关注 `app.request` 与 ERROR） |
| `JWT_SECRET` | 可留空（临时密钥，重启失效） | **必填**随机长串 | **必填**随机长串 |
| `LLM_API_KEY` 等凭据 | 可留空（对话回落 Dify / 降级 TF-IDF） | 必填真实 key | 必填真实 key |
| `ADMIN_API_TOKEN` / `ADMIN_TOKENS` | 可占位 | 必填 | 必填（建议配 operator 多角色） |
| `EMBEDDING_API_KEY` | 可留空（TF-IDF 降级） | 配置（检索质量） | 配置 |
| `AMAP_WEB_KEY` | 可留空（`amap_not_configured` 错误码） | 配置（演示主推 GPS） | 配置 |
| `USAGE_TRACKING_ENABLED` | `true` | `true` | `true`（成本可视）；`MODEL_PRICES` 建议配置 |
| `MEMORY_ENABLED`（长期记忆） | `true` | `true` | `true`（可按 LLM 成本关闭） |
| 数据库 | 本地 `digital_human.db` | VM 挂载卷内 | 挂载卷内 + **定时备份**（见 doc/database-backup.md） |

## 限流参数建议（staging/prod）

默认：窗口 60s 内 5 次失败 → 冷却 900s；guest 30 次/分钟。演示/staging 保持
默认即可（演示便利不锁死本机 IP）；prod 建议：

```
AUTH_RATE_LIMIT_MAX_FAILURES=5
AUTH_RATE_LIMIT_COOLDOWN_SECONDS=900
AUTH_GUEST_MAX_PER_MINUTE=20
```

## 切换指引

### dev（本机四服务）

1. `cp digitalHumanApi/.env.example digitalHumanApi/.env`，设 `APP_ENV=dev DEBUG=true`
2. 启动后端 `uvicorn app.main:app --reload`（digitalHumanApi/，端口 8000）
3. 前端 `npm run dev`（digitalHumanVue/，5173）；数字人 ADH 见
   `doc/local-digital-human-deploy-plan.md`
4. 单测：`pytest -q`（测试自动跑在内存 SQLite，不依赖 .env 内容，
   仅需 config 校验器要求的占位 DIFY_API_URL/DIFY_API_KEY）

### staging（VM 联调演示）

1. 根目录 `cp .env.example .env` 填写 staging 档（`APP_ENV=staging DEBUG=false`），
   凭据填真实 key（LLM/Embedding/AMAP/ADMIN/JWT_SECRET）
2. `docker-compose up -d --build`（api 8000 + web 80）；或 VM 上本机四服务
   启动（与 dev 相同命令，`APP_ENV=staging`）
3. 验证：`curl http://<host>:8000/api/v1/health` 应返回 `{"status":"ok","app_env":"staging"}`
4. 数据：轮询/量化跟踪 `DIFY_LIGHT4AI_USER` 填演示虚拟访客；建议演示前
   执行一次 `scripts/backup_db.sh`（备份策略见 doc/database-backup.md）

> ⚠ **Docker 场景 APP_ENV 覆盖警告**：`config.py` 的 `load_dotenv(override=True)`
> 使挂载的 `digitalHumanApi/.env` **优先于** docker-compose 透传的容器环境变量。
> 若 `digitalHumanApi/.env` 由模板复制而来（默认 `APP_ENV=dev`），它会静默覆盖
> 根目录 `.env` 里 `APP_ENV=staging/prod` 的透传——健康检查会显示 `app_env=dev`
> 而运维以为在 staging/prod。Docker 部署请以 `digitalHumanApi/.env` 的 `APP_ENV`
> 为准（同步修改或删除该行，让 compose 透传生效），并用健康检查确认档位。

### prod（正式部署）

1. 根目录 `.env` 设 `APP_ENV=prod`，逐一确认「prod 必填」项：
   `JWT_SECRET`（随机长串）、`ADMIN_API_TOKEN`/`ADMIN_TOKENS`、`CORS_ORIGINS`
   （真实域名）、全部 API key
2. `docker-compose up -d --build`；nginx `proxy_read_timeout ≥ 300s`（分析超时预算，
   见 doc/phase3-analytics-verification.md）
3. 配置定时备份（cron 调 `scripts/backup_db.sh`，volume 路径见 doc/database-backup.md）
4. 变更后健康检查确认 `app_env=prod`（Docker 场景注意上节 ⚠ 警告：
   `digitalHumanApi/.env` 的 `APP_ENV` 优先于 compose 透传，需同步修改或删除）

## 配置覆盖机制说明

- `app/core/config.py` 的 `Settings` 直接读进程环境变量（`os.environ`），
  启动时 `load_dotenv(override=True)` 加载 `digitalHumanApi/.env`——**.env 优先于
  shell 环境变量**（临时覆盖需改 .env 或用 pytest monkeypatch，见 AGENTS.md 坑位）
- Docker Compose：根目录 `.env` → `docker-compose.yml` `environment` 显式透传
  （`${VAR:-default}` 语法）；新增配置变量需同步 docker-compose.yml 透传列表
- 前端环境变量仅 `VITE_` 前缀客户端可见（`digitalHumanVue/.env`），与后端无关
