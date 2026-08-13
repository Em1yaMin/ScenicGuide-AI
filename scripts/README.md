# 运维脚本（四服务一键起停 + 健康检查 + .env 校验）

演示/开发环境四服务的一键运维基建，仓库根 `scripts/` 目录：

| 脚本 | 用途 |
|------|------|
| `start_all.sh` | 一键启动四服务（幂等；支持单服务参数） |
| `stop_all.sh` | 停止四服务（PID 文件 + 端口 + 进程签名三重匹配，不误杀） |
| `health_check.sh` | 探测四端口 + HTTP 健康检查，输出状态表，异常退出码 1 |
| `check_env.sh` | 后端 `.env` 配置校验（告警不阻断，可作 CI/启动前门禁） |
| `smoke_ops.sh` | 运维脚本全链路冒烟测试（真实起停本机服务） |
| `lib/common.sh` | 公共库：服务注册表 + 端口/进程/HTTP 探测 + .env 解析 |

全部脚本 `bash` 实现、shellcheck 干净、零新增依赖（ss/lsof/curl 缺失时逐级退化）。

## 服务拓扑（四服务）

| 服务 | 端口 | 目录 | 启动命令 | 健康路径 |
|------|------|------|----------|----------|
| backend | 8000 | `digitalHumanApi/` | `.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` | `/docs` |
| frontend | 5173 | `digitalHumanVue/` | `node node_modules/vite/bin/vite.js --host 0.0.0.0` | `/` |
| adh | 8001 | `live2d-frontend/` | `venv/bin/python main.py`（带 `QWEN_API_KEY`） | `/docs` |
| adhweb | 3000 | `live2d-frontend/web/` | `node node_modules/next/dist/bin/next dev -p 3000` | `/` |

- **健康路径说明**：ADH 的 FastAPI 未挂根路由（`/` 恒 404），故与后端一样探测 `/docs`；其余 `/` 返回 200。
- **日志/PID**：`/tmp/sg-<服务>.log`、`/tmp/sg-<服务>.pid`（可用环境变量 `SG_LOG_DIR` 覆盖，测试用）。

## 用法

```bash
# 一键启动四服务（幂等：端口已占用则跳过）
scripts/start_all.sh

# 单服务/多服务参数（backend|frontend|adh|adhweb）
scripts/start_all.sh backend
scripts/start_all.sh backend adhweb

# 停止（默认全部；同样支持单服务参数）
scripts/stop_all.sh
scripts/stop_all.sh frontend

# 健康检查（默认全部；支持单服务参数；任一异常退出码 1）
scripts/health_check.sh
scripts/health_check.sh backend adh

# .env 校验（默认 digitalHumanApi/.env；可指定任意 env 文件）
scripts/check_env.sh
scripts/check_env.sh /path/to/other.env

# 冒烟测试（真实起停本机服务，前置不齐的服务自动跳过）
scripts/smoke_ops.sh
scripts/smoke_ops.sh backend adhweb
```

退出码约定：起停/健康/校验脚本均为 `0=全部成功（健康/通过）/ 1=存在异常或失败`；`check_env.sh` 的 `1` 表示「存在问题但可继续」（告警不阻断）。

## 集成的已知坑（学习记录）

1. **QWEN_API_KEY 自动提取**：ADH（8001）TTS 必须带 `QWEN_API_KEY`，否则无声。`start_all.sh` 启动 ADH 时自动从 `digitalHumanApi/.env` 的 `EMBEDDING_API_KEY` 提取注入（演示部署两 key 同源）；显式导出的 `QWEN_API_KEY` 环境变量优先；两处皆空时启动继续但输出醒目警告。
2. **前端不能用 `npm run dev`**：直接 `node node_modules/vite/bin/vite.js --host 0.0.0.0`（等价 `npx vite --host 0.0.0.0`，但不引入 npx 包装子进程——包装进程会导致 PID 文件 ≠ 服务进程、stop 时杀不死 vite 本体）。
3. **ADH-Web 不能用 `pnpm dev`**：直接 `node node_modules/next/dist/bin/next dev -p 3000`（绕过 pnpm dev 依赖检查 bug）。
4. **驻留方式**：`setsid nohup bash -c '...' < /dev/null > log 2>&1 & disown`——受限 shell 下脱离控制终端与 SIGHUP；PID 文件在 bash -c 内 `echo $$` 写入后 `exec`，保证 PID 文件即服务进程本身。
5. **后端 Python 优先 `.venv`，缺失回退系统 python3**（需可 `import uvicorn`），都不可用给出建 venv 修复指引。
6. **ADH 前置要求 `live2d-frontend/configs/config.yaml`**（gitignored 不入库）：缺失时提示 `cp configs/config_template.yaml configs/config.yaml`。
7. **stop 防误杀**：PID 文件进程必须存活且 cmdline 匹配服务签名（uvicorn/vite/main.py/next）才终止；PID 文件失效时按「端口 + cmdline 签名」双条件兜底匹配，TERM → 等 10s → KILL。

## check_env.sh 校验规则

| 级别 | 检查项 | 说明 |
|------|--------|------|
| 严重 | .env 缺失 | 给出复制 .env.example 指引 |
| 严重 | APP_ENV 非法（非 dev/staging/prod） | 后端启动即报错（config.py 校验器） |
| 严重 | DIFY_API_URL / DIFY_API_KEY 为空 | config.py 校验器强制非空，后端启动直接失败 |
| 警告 | LLM_API_BASE 为空/非 http(s)、LLM_API_KEY 为空、备用 key 为空 | 对话主链路不可用/回落 Dify |
| 警告 | **Embedding 组合错配**（最典型静默降级坑） | `EMBEDDING_API_STYLE=minimax` 时 BASE 应含 `minimax`、MODEL 应为 `embo-*`；`openai` 时 BASE 应含 `dashscope`、MODEL 应含 `qwen`。错配 → 向量化请求发往错误端点 → 静默降级本地 TF-IDF 且无告警（worktree/旧副本半迁移配置的典型症状） |
| 警告 | EMBEDDING_API_KEY 为空 | Embedding 降级 TF-IDF（检索质量下降） |
| 警告 | AMAP_WEB_KEY 为空 | GPS 工具不可用（对话返回 amap_not_configured） |
| 警告 | QWEN_API_KEY 与 EMBEDDING_API_KEY 均为空 | ADH TTS 无声 |
| 警告 | ADMIN_API_TOKEN / JWT_SECRET / ADMIN_USERNAME / ADMIN_PASSWORD 默认值 | 管理端/会话安全（prod 必改） |
| 警告 | 占位符残留（your-/xxx/placeholder 等） | 全局扫描，列出键名（不回显值，防泄漏密钥） |

安全约定：脚本只输出键名与结论，**绝不回显任何值内容**（值可能含密钥）。

## 手动验证记录

- **2026-08-13 冒烟测试（本仓库 worktree，真实起停）**：`scripts/smoke_ops.sh` 28/28 通过——
  - 一键启动四服务全部就绪（backend HTTP 200 / frontend 200 / adh 200 / adhweb 200）；
  - 重复执行全部「跳过」（幂等）；健康检查全绿退出码 0；
  - 单服务参数：`stop_all.sh backend` 停止且 8000 释放 → `start_all.sh backend` 单独拉起；
  - `stop_all.sh` 全部停止、端口全部释放、PID 文件清理；
  - 未知服务名退出码 1、help 退出码 0；
  - ADH 日志无「未配置 API Key」错误（QWEN_API_KEY 已从 .env EMBEDDING_API_KEY 自动注入）；
  - 测试后无残留进程/端口占用。
- **2026-08-13 check_env.sh 验证**：真实 `digitalHumanApi/.env` 校验输出 10 通过/3 警告/0 严重（APP_ENV 未配、EMBEDDING_API_STYLE 未配、AMAP_WEB_KEY 未配——均与实际一致，退出码 1）；合成错配 .env（style=openai + minimax BASE、DIFY 空、APP_ENV 非法、占位符等）正确列出 2 严重/9 警告；缺失文件场景正确报严重。
