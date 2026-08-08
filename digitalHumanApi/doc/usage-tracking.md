# LLM token 用量记账（批次7 C5）契约文档

> 船长决策：token 记账放后台，分不同模型用量、可查询值详细一点（参考
> GitHub 用量报表风格：趋势图 + 明细表）；倾向详细可查。opencode-go 为
> 订阅制，可只记 token 不记钱，但保留 cost 列与单价映射能力。

## 1. 数据模型（llm_usage 表）

Alembic 迁移：`migrations/versions/c5f1a2b3d4e5_添加_llm_usage_表.py`
（upgrade 建表 + 索引，downgrade 回退删表）。ORM 实体：`app/models/usage.py`。

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK | 自增主键 |
| model | String(128) | 实际调用的模型名（如 `deepseek-v4-flash` / `deepseek-chat`） |
| input_tokens | Integer | 输入 token 数（响应 usage 字段） |
| output_tokens | Integer | 输出 token 数 |
| total_tokens | Integer | input + output（本地求和，不依赖响应 total 字段防不一致） |
| duration_ms | Integer | 单次模型调用耗时（毫秒，调用层计时） |
| chain | String(32) | 链路来源（见下「链路常量」） |
| user | String(64) | 用户标识（登录用户 id / 游客 id / visitor；后台任务为 NULL） |
| cost | Float | 估算费用（元），按 `MODEL_PRICES` 单价表换算；未配置为 NULL |
| created_at | DateTime | 记录时间（UTC naive） |

索引：`model` / `chain` / `created_at`（报表筛选与时间范围查询）。

### 链路常量

| 值 | 来源 |
|----|------|
| 对话 | deepagents 主链路 `chat`/`chat_stream`（含弃用 chat_agent 快速路径） |
| 推荐 | 推荐问句生成（`recommend_service.fetch_recommended_questions`） |
| 评估 | 量化评估（`evaluation_service.evaluate_content`，analysis 链路） |
| 压缩 | 长会话上下文压缩摘要（`deepagents.compaction.maybe_compact`） |
| 管理员agent | 管理员 agent（`admin_agent.agent.chat`/`chat_stream`，user=admin） |
| 记忆提取 | 长期记忆事实提取（`memory_service.extract_facts`） |
| 规划 | 弃用链路 planner_agent 规划图（参考实现） |
| 其他 | 未标注链路的兜底 |

## 2. 埋点机制

**统一封装层拦截（`app/services/usage_tracker.py`）**：`UsageTrackingChatOpenAI`
（ChatOpenAI 子类）覆盖 `_generate`/`_agenerate`/`_stream`/`_astream` 四个
模型调用入口；`llm.py` 工厂（`create_chat_llm`）在 `USAGE_TRACKING_ENABLED`
（默认 true）时构造该子类——**所有经 `get_chat_llm`/`create_chat_llm` 构造
的链路自动记账，调用方零改动**。analytics_service（`OPENAI_*` 独立构造，
不走工厂）不在埋点范围。

- **非流式**：从 `ChatResult.llm_output["token_usage"]` 取用量（OpenAI
  兼容响应 usage 字段）
- **流式**：请求附带 `stream_options.include_usage`（`USAGE_STREAM_USAGE`
  开关，默认 true；opencode-go / DeepSeek 官方实测支持），从最后一个带
  `usage_metadata` 的 chunk 取用量
- **调用行为不变**：不修改请求/响应内容（仅流式多请求一个 usage 字段）；
  调用异常或流被中断（未收到 usage）不记账（宁缺毋滥）
- **链路/用户上下文**：各调用点用 `usage_context(chain, user)` contextmanager
  标注（contextvars 语义：异步任务 / `asyncio.to_thread` 线程 / langgraph
  图内子任务均自动继承）

### 调用点清单

| 调用点 | 文件 | 标注 |
|--------|------|------|
| 对话（非流式/流式） | `deepagents/agent.py` chat/chat_stream | 对话 + user |
| 推荐问句 | `recommend_service.py` | 推荐 |
| 量化评估 | `evaluation_service.py` | 评估 |
| 上下文压缩 | `deepagents/compaction.py` | 压缩 |
| 管理员 agent | `admin_agent/agent.py` chat/chat_stream | 管理员agent + admin |
| 记忆提取 | `memory_service.py` extract_facts | 记忆提取 |
| 弃用链路 | `chat_agent.py` / `planner_agent.py` | 对话 / 规划 |

意图分类为纯规则匹配（`intent_classifier.py`），无 LLM 调用，无需埋点。

## 3. 批量 flush（不拖慢对话）

`usage_tracker.record_usage()` 仅入队（`queue.Queue`，线程安全，零阻塞）：

- **后台 flush 循环**：main.py lifespan 注册 `usage_tracker.flush_loop()`，
  每 `USAGE_FLUSH_INTERVAL_SECONDS`（默认 5s）drain 队列批量写库（单事务）
- **队列满兜底**（`USAGE_QUEUE_MAX_SIZE`，默认 2000）：立即向绑定事件循环
  调度一次 flush（`run_coroutine_threadsafe`，不阻塞调用线程）；仍未腾出则
  丢弃最旧条目（`dropped` 计数）防内存膨胀
- **关闭兜底**：lifespan 关闭时 cancel flush 循环后 `shutdown()` 最后 flush
  一次；此后新记录丢弃（`USAGE_TRACKING_ENABLED=false` 时完全不入队）
- **写库失败**：告警并丢弃本批（`failed` 计数），绝不阻塞/重试拖垮主流程

## 4. 配置（`.env.example`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `USAGE_TRACKING_ENABLED` | true | 总开关；关闭后工厂不包装埋点模型，零额外开销 |
| `USAGE_FLUSH_INTERVAL_SECONDS` | 5 | 后台批量写库周期（秒） |
| `USAGE_QUEUE_MAX_SIZE` | 2000 | 进程内队列上限（超出兜底落库） |
| `USAGE_STREAM_USAGE` | true | 流式请求 usage（不兼容网关可关，流式只记耗时/次数） |
| `MODEL_PRICES` | 空 | 单价表 `"模型:输入价,输出价"`（元/百万 tokens），多项分号分隔；订阅制可留空 |

## 5. 报表聚合 API（管理端 X-Admin-Token）

路由：`app/routers/usage.py`（前缀 `/api/admin/usage`，全部端点要求
`X-Admin-Token`；查询类 operator 角色可访问，与知识库/记忆查询一致）。

### GET /api/admin/usage/overview

查询参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| start | str | 起始时间（ISO 日期 `2026-08-01` 或 datetime `2026-08-01T00:00:00[±tz]`），缺省 end-30 天 |
| end | str | 结束时间，缺省当前时刻 |
| granularity | str | 趋势粒度 `day`（默认）/ `week` / `month` |

响应（UsageOverviewResponse）：

```json
{
  "start": "2026-08-01T00:00:00",
  "end": "2026-08-31T12:00:00",
  "granularity": "day",
  "totals": {"calls": 120, "input_tokens": 100000, "output_tokens": 50000,
             "total_tokens": 150000, "avg_duration_ms": 3200, "cost": 0.2},
  "series": [{"bucket": "2026-08-01", "calls": 5, "input_tokens": 1000,
              "output_tokens": 500, "total_tokens": 1500,
              "avg_duration_ms": 3000, "cost": 0.002}, ...],
  "by_model": [{"name": "deepseek-v4-flash", "calls": 100, "input_tokens": 90000,
                "output_tokens": 45000, "total_tokens": 135000,
                "avg_duration_ms": 3000, "cost": 0.18}, ...],
  "by_chain": [{"name": "对话", "calls": 80, "input_tokens": 70000,
                "output_tokens": 35000, "total_tokens": 105000,
                "avg_duration_ms": 3500, "cost": 0.14}, ...]
}
```

- `series`：按粒度分桶的趋势序列（升序，供趋势图）
- `by_model` / `by_chain`：按 total_tokens 降序（供明细表）
- `cost`：未配置单价表或该行模型无单价时聚合值为 null

### GET /api/admin/usage/details

分页明细（created_at 倒序）。查询参数：`start` / `end` / `model` / `chain` /
`user`（筛选，均可选）+ `page`（≥1）/ `page_size`（1-100，默认 20）。

响应：`{"total", "page", "page_size", "items": [...]}`，items 元素字段：
`id/model/input_tokens/output_tokens/total_tokens/duration_ms/chain/user/cost/created_at`。

### GET /api/admin/usage/export

CSV 导出（与 details 同筛选，全量行不分页；UTF-8 BOM 兼容 Excel）。
响应：`text/csv` + `Content-Disposition: attachment`，表头：
`id,created_at,model,chain,user,input_tokens,output_tokens,total_tokens,duration_ms,cost`。

### 校验

- start/end 非法格式 → 422；start > end → 422；granularity 非 day/week/month → 422
- 未带 X-Admin-Token / token 错误 → 401

## 6. 已知边界

- 流式调用在 `USAGE_STREAM_USAGE=false` 或端点不支持 `stream_options` 时
  无 token 明细（调用次数/耗时照记）
- 调用异常 / 流中断（客户端断开发生在 usage chunk 之前）不记账
- analytics_service（运营分析，`OPENAI_*` 配置独立构造）不在埋点范围
- 报表时间为 UTC；前端展示可本地化转换
