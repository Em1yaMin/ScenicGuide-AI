# 阶段3 运营分析链路本地化确认（phase3-analytics-verification）

> 日期：2026-08-06 ｜ 阶段3 T2：分析链路本地化确认——LLM 分析走真实 opencode-go 端点可用、DB 统计兜底完整
> 船长决策（2026-08-06）：LLM key 单用 opencode-go（`OPENAI_*` 已配同端点）；分析链路迁 LangChain（LLM 分析失败时用纯 DB 统计结果，不阻断任务）

## 1. 验证环境

| 项 | 值 |
|----|----|
| 本后端 | `digitalHumanApi/`，uvicorn `127.0.0.1:8000`（真实 key 来自项目 `.env`；worktree 内 `.env` 为 gitignored 临时配置，验证后不入库） |
| 运营分析 LLM | `OPENAI_API_BASE=https://opencode.ai/zen/go/v1` + `OPENAI_MODEL=deepseek-v4-flash`（与 `LLM_*` 同一 key，船长决策单用 opencode-go） |
| 分析链路 | `app/services/analytics_service.py`（LangChain 链：prompt → ChatOpenAI → JSON 解析）+ `app/routers/analysis.py` |
| 定时任务 | `app/tasks/analysis.py::analysis_loop`（每 300s 跑 `batch_evaluate`，Dify Workflow 量化评估；任务级 try/except，失败记录日志继续下一轮） |
| 样本数据 | worktree `digital_human.db` 灌入 16 条对话 + 8 条评估（游客 `verify-analytics`，覆盖 2 天、8 个评分） |

## 2. 验证矩阵总览

| 场景 | 预期 | 结果 |
|------|------|------|
| 真实 LLM 分析（服务层） | opencode-go 调用成功，产出完整 JSON（6 keys） | ✅ 调用成功，`LLM analysis succeeded`，JSON keys 完整 |
| 真实 LLM 分析（HTTP 接口） | `GET /api/v1/analysis/overview` 返回 `llm_analysis=true` | ✅ 200，`llm_analysis: true`，8 个热门问题（带分类）+ 5 条运营建议 |
| LLM 失败兜底 | 捕获异常 → DB 统计摘要 + 降级标记 | ✅ `llm_analysis=false`，确定性摘要（评分均分/高频咨询/高峰时段），ai_summary 带「数据库统计模式」 |
| 未配置 key | 跳过 LLM → DB 统计兜底 | ✅ 单测覆盖（`test_overview_without_key_uses_db_fallback`） |
| 定时任务韧性 | batch_evaluate 异常 → 记日志继续下一轮 | ✅ 单测覆盖（`test_analysis_loop_survives_batch_failure`）；真实环境 `Evaluated 8 dialogues in batch` |
| 超时调优 | 原 60s/90s 超时在 opencode-go 上稳定超时 → 调大 | ✅ 实测单次调用 55-90s，改为 180s/240s 后真实调用成功 |

## 3. 验证暴露的问题与修复

### 3.1 opencode-go 分析调用超时（实测 55-90s，原超时 60s/90s 必超时）

**现象**：`POST /api/v1/analysis/overview` 首调 90s 后 `TimeoutError`，自动落入 DB 兜底——真实 opencode-go 端点对分析类 prompt（数千 token）延迟高且波动大，原 `ChatOpenAI(request_timeout=60)` + `asyncio.wait_for(..., timeout=90)` 均过紧。

**证据**（真实端点直测 3 次，`chat/completions` 同参数）：
```
summary_len=747 → attempt 0: OK 55.3s / attempt 1: OK 90.2s / attempt 2: OK 68.6s（均解析成功）
```

**修复**：`analytics_service.py` 新增常量 `LLM_REQUEST_TIMEOUT=180`（httpx 超时）、`LLM_ANALYSIS_TIMEOUT=240`（asyncio.wait_for 总上限，需大于 httpx 超时使底层超时正常上抛）。修复后真实调用成功（见 §4 日志）。

**部署拓扑超时说明（2026-08-06 船长决策，方案 A）**：本验证在直连 `127.0.0.1:8000`（绕过 nginx）下完成，真实调用总耗时约 2m08s（§4）且失败路径需等 httpx 180s 超时——均超过 docker 部署拓扑中 nginx `/api/` 的 `proxy_read_timeout 120s`（`nginx.conf`），管理后台经 nginx 代理访问 `/api/v1/analysis/overview` 会被 504 掐断、LLM 结果与 DB 兜底都送不到客户端。已将 `nginx.conf` 的 `proxy_read_timeout` 提升至 `300s`（> `LLM_ANALYSIS_TIMEOUT=240` 总预算），并同步在 README 部署说明记录该超时约束；后续如再调大分析超时预算，需同步检查 nginx 代理超时。

### 3.2 DB 兜底摘要不完整（LLM 失败时满意度摘要误导）

**现象**：LLM 失败后 `satisfaction_summary` 恒为「暂无足够数据进行分析」、`satisfaction_direction` 恒为「平稳」、无运营建议——即使 DB 已有 8 条评估与关注点数据。

**修复**：
- 新增 `_build_db_fallback_summary()`：基于 DB 满意度趋势（前后半段均分对比，阈值 ±0.05）判定方向；摘要含评估条数/平均分/高频咨询/高峰时段；由热点与高峰时段推导确定性运营建议
- 响应新增降级标记 `llm_analysis: bool`（`app/schemas/analysis.py`；true=LLM 产出，false=DB 统计兜底），`ai_summary` 附「（数据库统计模式，LLM 分析不可用）」

### 3.3 测试环境被 worktree .env 污染（真实 key 触发慢速网络调用）

**现象**：本地 pytest 时 `test_analysis_overview_with_data` 用真实 key 发起 opencode-go 调用（实测 21.8s），慢且不稳定。

**修复**：`tests/conftest.py` 新增 autouse fixture `_pin_openai_api_key_off`（同 `_pin_embedding_api_key_off` 模式），固定 `settings.OPENAI_API_KEY=""` 与 CI 一致；LLM 路径由测试显式 monkeypatch `_get_analysis_chain` 模拟（假链返回合法 JSON / 直接抛异常）。

## 4. 真实环境调用证据（HTTP 接口 + 日志）

```bash
# 启动后端（真实 key）
nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info > /tmp/uvicorn_analytics.log 2>&1 &

# 定时任务真实跑通（Dify Workflow 评估）
# 日志：
#   app.tasks.analysis: Analysis task started (interval: 300s, max batch: 50)
#   httpx: POST http://111.228.47.45/v1/workflows/run "HTTP/1.1 200 OK"
#   app.tasks.analysis: Evaluated 8 dialogues in batch

# LLM 分析真实调用
curl -s "http://127.0.0.1:8000/api/v1/analysis/overview?days=30"
# 日志：
#   httpx: POST https://opencode.ai/zen/go/v1/chat/completions "HTTP/1.1 200 OK"
#   app.services.analytics_service: LLM analysis succeeded, extracted JSON with keys: ['hot_questions', 'satisfaction_summary', 'satisfaction_direction', 'unanswered_questions', 'peak_hours', 'suggestions']
#   GET /api/v1/analysis/overview?days=30 "HTTP/1.1 200 OK"
# 响应要点：llm_analysis=true；hot_questions 8 个（含 category）；unanswered_questions 5 个（含 reason）；suggestions 5 条
# 总耗时约 2m08s（端点层 = LLM 分析 + 统计数据计算）

# DB 兜底链路（服务层强制 LLM 失败模拟）
# 响应要点：llm_analysis=false
#   satisfaction_direction=下降（DB 趋势计算）
#   satisfaction_summary=最近统计显示：基于 8 条游客评分数据（平均 3.2 分）；高频咨询：票务咨询、路线指引、设施服务；高峰时段：0:00-0:59。满意度趋势下降（数据库统计兜底）。
#   ai_summary 尾部含「（数据库统计模式，LLM 分析不可用）」
```

## 5. 测试与回归

- 新增单测（`tests/test_analysis.py`）：
  - `test_overview_llm_failure_falls_back_to_db_stats` — LLM 异常 → 不失败、DB 统计输出、`llm_analysis=false`、方向由 DB 趋势判定（day0 均分 2 < day1 均分 4 → 上升）
  - `test_overview_llm_success_marks_llm_analysis` — LLM 成功 → `llm_analysis=true`，字段来自 LLM 输出
  - `test_overview_without_key_uses_db_fallback` — 无 key → 跳过 LLM 直接 DB 兜底
  - `test_analysis_loop_survives_batch_failure` — 定时任务 batch 异常 → 记日志继续下一轮（asyncio.sleep patch 保留原始引用避免递归）
- 全量回归：`pytest -q` → **253 passed**（无回归）
- 无新增依赖；改动范围：`digitalHumanApi/` 后端（schema/service/tests/conftest/文档）+ 根目录 `nginx.conf`（部署层代理超时对齐，见 §3.1）

## 6. 结论

- 分析链路本地化确认完成：真实 opencode-go key 下 LLM 分析成功（接口与日志证据），DB 统计兜底完整且带降级标记
- 验证暴露的 3 个问题全部修复：LLM 超时过紧、兜底摘要误导、测试环境 key 污染
- 部署拓扑超时对齐：nginx `/api/` `proxy_read_timeout` 120s→300s，避免分析接口（真实调用约 128s、失败路径等 httpx 180s）在 docker 部署下被 504 掐断
- 定时任务（analysis_loop）在 LLM/Workflow 故障时任务级 try/except 保证持续运行
