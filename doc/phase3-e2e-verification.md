# 阶段3 全链路联调验证（phase3-e2e-verification）

> 日期：2026-08-06 ｜ 阶段3 收官：双模式 / 推荐问句本地化 / 分析链路本地化 / 记忆系统 / 双轨回落
> 环境：worktree 自建 `.env`（复制主项目 `digitalHumanApi/.env`，gitignored 不入库），uvicorn `127.0.0.1:8000`
> 真实调用：opencode-go 端点（`LLM_*`/`OPENAI_*` 同 key，deepseek-v4-flash）+ Dify 服务器；配额精炼（每链路 1-2 次调用）

## 1. 验证矩阵

| # | 场景 | 预期 | 结果 |
|---|------|------|------|
| 1 | 简单问答快速路径 | 单轮直答、低延迟（零额外分类往返） | ✅ `tools=10`，RAG 注入 3 条来源；耗时 47s 为 opencode-go 端点延迟（实测 55-90s 区间），路径正确 |
| 2 | 复杂任务规划路径（SSE） | 意图分类→规划→多步执行→总结，`step/token/done` 事件序列 | ✅（修复后）`plan → recommend_routes → search_spots/search_knowledge → summarize`，`done` 透出 3 条 RAG 来源 |
| 3 | 推荐问句本地生成 | 不再调 Dify，本地 LLM 生成 questions 数组 | ✅ `POST /visitor/refresh-recommend` 返回 4 条问句（「门票怎么买？」等），日志 `Refreshed 4 recommended questions` |
| 4 | 分析链路 LLM | `llm_analysis=true`，产出热门问题/建议/高峰时段 | ✅ overview 返回 `llm_analysis: true`，5 热门问题 + 5 建议 |
| 5 | 分析 DB 兜底降级 | LLM 不可用时 `llm_analysis=false` + 「数据库统计模式」标记 | ✅ 置空 `OPENAI_API_KEY` 后服务层验证：`llm_analysis: false`，`ai_summary` 尾部含「（数据库统计模式，LLM 分析不可用）」 |
| 6 | Dify Workflow 量化评估 | 未评估对话批量评估写入 Evaluation 表（管理后台数据源） | ✅ 注入 6 条对话后 `Evaluated 6 dialogues in batch`（Dify Workflow 真实调用 200） |
| 7 | 登录用户长期记忆 | 对话后异步提取事实/偏好写入 user_memory（按 user 隔离） | ✅ 用户 `1` 写入 2 条（`带老人和小孩一起出行` fact / `喜欢自然风光` preference） |
| 8 | 新会话首轮记忆注入 | 首轮回答引用用户画像（多轮不重复注入） | ✅ 新会话问「你还记得我的出行情况吗」→ 回答引用「带着老人和小孩一起出行」并推荐老少皆宜路线 |
| 9 | 游客隔离 | 游客（guest-*）不写长期记忆、不注入 | ✅ 游客对话正常（tools=9），`guest-*` 记忆条数 0 |
| 10 | 双轨回落 | 模拟 LangChain 主链路故障 → 自动回落 Dify | ✅ 置坏 `LLM_API_KEY` 重启后对话 → 日志 `LangChain 对话链路失败，自动回落 Dify`，Dify 正常回答 |
| 11 | 回归测试 | 全量 pytest 无回归 | ✅ 320 passed（含新增 2 个规划重试单测） |

## 2. 验证证据（关键日志/响应摘录）

### 2.1 复杂任务规划路径（SSE 步骤序列，修复后）

```
STEP: plan | 正在为您规划行程…
STEP: recommend_routes | 正在为您推荐路线…
STEP: search_spots | 正在查询景点…
STEP: search_knowledge | 正在检索知识库…
DONE: conversation_id=... rag_sources=3
```
日志：`规划路径(流式): steps=3 tools=['recommend_routes', 'search_spots', 'search_knowledge'] answer_len=1128`（连续 2 次均完整走规划路径，无降级）

### 2.2 推荐问句本地生成（脱离 Dify）

```
POST /api/v1/visitor/refresh-recommend → 200
{"questions": ["门票怎么买？", "适合带孩子玩吗？", "老人游园方便吗？", "怎么坐车到景区？"]}
日志：Refreshed 4 recommended questions（无 Dify 调用，走 LLM_* opencode-go）
```

### 2.3 分析链路（LLM 成功 / DB 兜底）

```
GET /api/v1/analysis/overview?days=30 → llm_analysis: true, hot_questions: 5, suggestions: 5
置空 OPENAI_API_KEY 后 → llm_analysis: false, ai_summary 尾部「（数据库统计模式，LLM 分析不可用）」
```

### 2.4 长期记忆（写入 + 注入 + 游客隔离）

```
登录用户 user=1 对话 → 长期记忆写入: user=1 written=2 overwritten=0 total=2
新会话「你还记得我的出行情况吗？」→ 回答引用「带着老人和小孩一起出行」
游客（guest-* token）对话 → user_memory 中 guest-* 条数 0
```

### 2.5 双轨回落（模拟主链路故障）

```
LLM_API_KEY 置坏重启 → POST /dialogue/chat
日志：LangChain 对话链路失败，自动回落 Dify (engine=langchain): Error code: 401 - Invalid API key.
响应：answer 由 Dify 产出（conversation_id 为 Dify 格式 uuid）
```

## 3. 联调暴露问题与修复

### 3.1 规划路径偶发空响应（真实端点，已修复）

- **现象**：复杂任务首次联调 `STEP: plan_fallback`（规划阶段失败降级快速路径），日志 `规划路径流式失败，降级快速路径: 规划结果为空或全部步骤无效`。
- **定位**：opencode-go 端点（deepseek-v4-flash，思考型模型）对长规划 prompt（工具清单约 1062 字符）**偶发返回 200 但 content 为空**（思考耗尽，不报错）——直接复现 6 次约 50% 失败率（`len=0`），与 `max_tokens`（2048/4096）及 `timeout`（60/120s）均无关；短消息（「只回复两个字」）稳定成功，说明是长 prompt + 思考型模型的偶发空响应。
- **修复**：`planner_agent.plan_node` 增加 `_PLAN_RETRIES = 1` 重试（空输出/无效输出/流式无分块异常均重试一次），修复后真实环境连续 2 次完整走规划路径。
- **测试**：新增 `test_chat_plan_retries_empty_plan_output`（首次空→重试成功）、`test_chat_plan_retries_exhausted_raises`（连续空→PlannerError 降级），全量 320 passed。
- **已知残留**：总结阶段（`summarize_node`）与快速路径 agent 同为长 prompt 偶发空响应场景，因触发概率低且均有降级兜底（总结为空抛 PlannerError → 降级快速路径），本次未加重试；若阶段4 继续观测到偶发，可按同一模式补 `_PLAN_RETRIES`。

### 3.2 其他观察（非缺陷）

- `database is locked` 一次：验证时手动 sqlite3 并发插入与 uvicorn 后台任务冲突所致（SQLite 写锁），应用代码路径正常（重试后 `Evaluated 6 dialogues`）；部署场景单进程写库无此问题。
- 主项目 `.env` 的 `DIFY_LIGHT4AI_USER` 为空 → 轮询 `users=[]` 空转（无对话抓取），管理后台无新对话数据，属部署配置现状（非代码缺陷），已在审计报告中记录。

## 4. 结论

- 双模式（简单快速/复杂规划）、推荐问句本地生成、分析链路（LLM + DB 兜底降级标记）、记忆系统（长期/短期/游客隔离）与双轨回落**全链路真实可用**，有证据（§2）。
- 联调暴露的规划路径偶发空响应已修复并有单测；Dify 依赖面盘点完成（`doc/dify-dependency-audit.md`），除对话回落与历史数据接口外推荐/分析已无 Dify 依赖。
