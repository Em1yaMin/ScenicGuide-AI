# Phase 3 工作流执行检查报告

> runId: `2026-07-13_09-08-27-8641` · 工作流类型: `arch` · 检查时间: 2026-07-13

## 一、执行概况

| 阶段 | 状态 | 耗时 | 说明 |
|------|------|------|------|
| 1 research | ✅ 完成 | 17m | 产出研究文档（5 领域 / 18 失败点 / 4 组先例） |
| 2 design | ✅ 完成 | 38m | Phase 3 数据展示修复设计（4 项修复） |
| 3 plan | ✅ 完成 | 11m | 4 阶段实施计划 |
| 4-7 impl 1-4 | ✅ 完成 | 5m | 解析韧性 / 后端 / 前端 / 路由 |
| 8 validate | ⚠️ fail | 17m | Phase 1 缺 isinstance 类型守卫 |
| 9 code-review | ⚠️ 9 blockers | 32m | 2 critical + 7 important + 2 suggestion |
| 10 design (retry) | ❌ failed | 6m | 未产出 artifact，工作流终止 |

**结论**：4 项功能修复已落地，但 validate 未通过、code-review 发现 9 个 blocker，工作流在 design 重试阶段中断，未完成闭环。

## 二、已完成的 4 项功能修复

1. **Parse Failure Resilience**（部分实现）
   - `_parse_workflow_output()` 返回 `Optional[Dict]`，JSON 解析失败返回 None
   - `evaluate_dialogue()` 新增 3 条 None 重试路径（信封失败 / 空输出 / 解析失败）
   - `batch_evaluate()` 添加 commit try/except rollback
   - **缺口**：缺 `isinstance(result, dict)` 类型守卫（validate fail 根因）

2. **sentiment_trend 后端**
   - `get_dashboard_stats()` 改为 JOIN Dialogues + GROUP BY DATE(timestamp) + 7 天连续序列
   - 已修复 timezone（使用 `datetime.now(timezone.utc).date()`）
   - 移除死代码（sentiment_result 查询和 sentiment_map dict）

3. **sentiment_trend 前端**
   - `DashboardView.vue` 的 `sentimentTrendOption` 用 `.map()` 动态驱动 xAxis + 3 series
   - `?? []` 保护空数据情况

4. **Route Wiring**
   - `POST /api/v1/dialogue/sync-now` → 调用 `poll_once()`
   - `POST /api/v1/analysis/run-now` → 调用 `batch_evaluate()`

## 三、9 个 Blocker 清单

### P0 立即修复（阻断部署）

| # | ID | 问题 | 位置 | 修复方案 |
|---|-----|------|------|----------|
| 1 | — | `_parse_workflow_output` 缺 isinstance 类型守卫 | `analysis.py:42` | 添加 `if isinstance(result, dict): return result` 后再返回，非 dict 返回 None |
| 2 | I1 | `/run-now` 与 `analysis_loop` 并发调用 `batch_evaluate` → race condition | `analysis.py:10` / `main.py:43` | `asyncio.Lock` 共享于路由与 loop，或 `SELECT FOR UPDATE` |
| 3 | I3 | docker-compose 缺 `DIFY_WORKFLOW_API_KEY` → 部署时无限重试 | `docker-compose.yml:9-13` | 补环境变量；analysis.py 区分"key 缺失"（永久跳过）vs"解析失败"（重试） |

### P1 短期加固（retry 链路）

| # | ID | 问题 | 修复方案 |
|---|-----|------|----------|
| 4 | Q3 | `trigger_workflow` 缺 `_retry_silent`（GET 方法有） | 包裹 POST 在 `_retry_silent`，3 次退避 |
| 5 | Q4 | `_retry_silent` 的 `except Exception` 太宽 | 收窄到传输异常，非传输异常 re-raise |
| 6 | Q5 | 不可解析对话无限重试，无上限 | Evaluation 加 `retry_count` 列，超 3 次跳过 |
| 7 | Q7 | polling commit 的 `except Exception` 替代 IntegrityError | 恢复 `IntegrityError` 具体捕获 + 区分可重试 |
| 8 | Q8 | guardrails.json 残留 `E:/e/` 路径（3 次复发） | 删除该条；预提交 `test -d` 校验 |

### P2 中期改进（数据质量 + 测试）

| # | ID | 问题 | 修复方案 |
|---|-----|------|----------|
| 9 | I2 | 非规范 sentiment label 静默丢弃 | `evaluate_dialogue` 写入前校验 canonical 集，normalize 小写 |
| 10 | I4 | poll_once 把 API 失败的 `[]` 当"无数据" | `get_conversations` 失败返回 None vs 空返回 []，escalate N 次 |
| 11 | Q6 | `_parse_workflow_output` 无测试 | 补 6 场景：valid/markdown/非 dict/invalid/空/dict |
| 12 | Q9 | GaugeChart 注册但未使用 | 从 `useECharts.ts` 移除 |

## 四、后续推进路线

```
P0 修复 (3项) ──→ 重跑 validate ──→ pass
                                        │
P1 加固 (5项) ──────────────────────────┤
                                        │
P2 改进 (4项) ──→ 与 Phase 1 并行 ──────┤
                                        ↓
                    Phase 1: 部署live2d-frontend (8001+3000, All-in-Dify, user=EmiyaMin)
                                        │
                                        ↓
                    Phase 2: 端到端验证 (真实数据 polling→analysis→大屏)
                                        │
                                        ↓
                              演示视频录制 (≤7分钟)
```

### 建议执行顺序

1. **第一批（今天）**：P0 三项 + P1 的 Q8（guardrails 路径，1 行删除）
   - 修完重跑 `/skill:validate`，目标 verdict: pass
2. **第二批（1-2 天）**：P1 剩余 4 项（retry 链路加固）
   - 这批做完后管道才具备生产可靠性
3. **第三批（与 Phase 1 并行）**：P2 四项 + Phase 1 部署
   - P2 不阻断功能，可在部署 live2d-frontend 时同步进行
4. **Phase 2**：真实数据端到端验证
5. **收尾**：架构文档 + 部署文档 + 演示视频

## 五、关键复发教训（code-review 总结）

1. **静默数据截断无信号** — _QUERY_LIMIT / 非规范 label / 空 poll 当正常，数据丢失藏在正常操作背后
2. **peer 方法 retry 不对称** — trigger_workflow 至今无 retry，制造静默失败区
3. **配置路径拼写是静默失败** — guardrails `E:/e/` 复发 3 次，需 `test -d` 预提交校验
4. **跨层类型/校验漂移** — producer 存任意 label，consumer 硬编码 canonical 集，数据一致性碎片化
5. **无限重试无上限** — scenic_sync 已删，evaluate_dialogue 仍无限重试，需 dead-letter 机制

## 六、参考文档

- 工作流日志：`.rpiv/workflows/runs/2026-07-13_09-08-27-8641.jsonl`
- 研究文档：`.rpiv/artifacts/research/2026-07-13_09-08-27_project-plan-research.md`
- 设计文档：`.rpiv/artifacts/designs/2026-07-13_09-25-36_phase3-data-fixes.md`
- 实施计划：`.rpiv/artifacts/plans/2026-07-13_10-03-09_phase3-data-fixes.md`
- 验证报告：`.rpiv/artifacts/validation/2026-07-13_10-19-58_phase3-data-fixes.md`
- 代码审查：`.rpiv/artifacts/reviews/2026-07-13_10-36-43_working.md`
- 项目计划：`doc/project-plan.md`
