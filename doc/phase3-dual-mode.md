# 阶段3 T1 Agent 深化——LangGraph 双模式（phase3-dual-mode）

> 日期：2026-08-06 ｜ 船长决策方案B：简单问答走快速路径，复杂多步任务走
> LangGraph 规划路径（规划 → 多步执行 → 总结），SSE 步骤可视化思考过程
> 对应任务：阶段3 T1 Agent 深化（双模式）

## 1. 设计决策

### 1.1 双模式分流（方案B）

对话入口先做**意图分类**（`app/services/intent_classifier.py`），按任务复杂度分流：

| 分类 | 判定依据 | 路径 |
|------|----------|------|
| 简单 | 单工具可答（开放时间 / 在哪 / 怎么去 / 门票…；含纯人群/导航单轮查询如「带老人去合适吗」「灵山怎么走」） | 快速路径：现有 `create_agent` 流程（零延迟增加） |
| 复杂 | 明确多步规划意图（规划 / 安排 / 行程 / 一日游 / 路线推荐 / 预算…） | 规划路径：LangGraph 显式图 |

- **分类器选型**：轻量规则（船长决策「建议轻量规则或 LLM 单次分类」，选前者）。
  理由：① 快速路径零延迟——LLM 单次分类会给每条简单消息引入一次额外 LLM 往返
  （数百 ms 级），违背「简单问答延迟无显著增加」验收标准；② 规则分类纯字符串
  匹配、可解释可测试；③ 未命中词表默认简单（安全降级天然成立）。
- **词表设计约束（含船长决策 2026-08-06 方案B 收窄）**：与既有测试查询兼容——
  「拍照」「推荐」「推荐路线」等**单轮偏好表达**不视为复杂（快速路径的
  `recommend_routes` 工具可处理），仅「路线推荐」「推荐一条」等明确多步规划
  表述计入复杂信号；**纯人群/导航词（「怎么走」「带老人」「带小孩」
  「老人小孩」「带娃」「亲子游」「情侣游」「全家游」「家庭游」）不判复杂**
  （单轮可答的导航/适宜性/单偏好查询走快速路径，避免不必要的规划路径延迟
  增量）；组合规划类查询（如「规划灵山一日游，带老人小孩，喜欢拍照」）仍经
  「规划」「一日游」等信号判复杂；同一查询复杂信号不少于简单信号时判复杂
  （多意图优先，规划路径仍能正确回答单事实子问题）。

### 1.2 规划路径架构（`app/services/planner_agent.py`）

LangGraph 状态图（langgraph 1.2 自带能力，**零新依赖**）：

```
START → plan（规划） → execute（多步执行，循环 ≤6 步） → summarize（总结） → END
```

| 阶段 | 节点 | 实现 |
|------|------|------|
| 规划 | `plan` | LLM 按用户需求生成步骤序列（≤6 步，每步一个工具调用），JSON 输出 |
| 执行 | `execute` | 逐步骤确定性调用工具（复用快速路径 6+1 工具），单步失败记录错误继续 |
| 总结 | `summarize` | LLM 聚合全部工具结果生成结构化最终回答（流式逐字输出） |

**关键技术点（实测结论）**：

1. **图内模型调用统一转为流式**：langgraph 会把节点内的模型调用包装为
   `stream=True`（即使调用方用 `ainvoke`）。`with_structured_output` 的输出
   解析器不兼容流式分块（实测报错）→ 规划节点改用 `llm.astream` 累积后手动
   解析 JSON（`_parse_plan`：JSON 优先 + 正则提取兜底），跨 DeepSeek/OpenAI
   兼容端点均可用，不依赖 `response_format` 支持。
2. **工具线程池执行**：`BaseTool.ainvoke` 将同步工具放到线程池——`recommend_routes`
   内部 `asyncio.run` 在事件循环内直接调用会报错，经 `ainvoke` 安全。
3. **总结流式透出**：`graph.astream(stream_mode=["updates", "messages"])`——
   updates 模式产出节点完成事件（映射为 step），messages 模式产出 LLM token
   （元数据 `langgraph_node == "summarize"` 区分，仅透出总结节点 token）。
4. **事件顺序**：LLM token 先于节点完成事件到达 → 总结步骤事件（`正在生成行程
   安排…`）在首个 summarize token 时输出，保证「总结中 → token」顺序正确。
5. **RAG 来源透出**：执行结果中最后一次 `search_knowledge` 的返回按快速路径
   契约提取 `rag_context`/`rag_sources`（done 事件携带）。
6. **规划偶发空响应重试**（阶段3 收官补丁，2026-08-06）：opencode-go 思考型
   模型对长规划 prompt 偶发返回空 content，`plan_node` 对空/无效输出重试
   1 次（`_PLAN_RETRIES=1`）；详见 `doc/phase3-e2e-verification.md` §3.1。

### 1.3 安全降级与双轨回落（不变）

- **分类失败** → 默认简单（快速路径），`chat_agent._is_complex_intent` 双层
  try/except 防御
- **规划路径异常**（规划生成失败 / 步骤全非法 / 总结为空 / LLM 故障）→
  `PlannerError` 上抛 → chat_agent 静默降级快速路径 → 路由层兜底 Dify，
  对话永不中断
- **安全边界不变**：工具集不含预订/账号/支付类操作；规划 prompt 与总结 prompt
  均含人工客服引导；越界工具（如 `book_ticket`）在规划验证阶段被过滤
- **记忆不受影响**：短期记忆会话管理/长期记忆注入/提取钩子仍在 chat_agent 层，
  规划路径仅接收组装好的 `messages`（含记忆注入 SystemMessage），多轮上下文
  追加与记忆提取由 chat_agent 统一完成

### 1.4 配置

- `AGENT_DUAL_MODE`（默认 `true`）：双模式总开关；`false` 时全部走快速路径，
  行为与阶段2 完全一致（回退开关，无需改代码）
- 无其他新增配置（复用 `LLM_*` / `MEMORY_*`）

## 2. SSE 事件序列（复用现有契约，前端零改动）

```
step  tool=plan         label=正在为您规划行程…
step  tool=<工具名>     label=正在查询路线…（每执行一步一个）
step  tool=summarize    label=正在生成行程安排…
token text=回答增量（总结逐字输出）
done  conversation_id / rag_context / rag_sources
```

规划路径降级快速路径时额外输出 `step tool=plan_fallback label=正在直接为您解答…`。

## 3. 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_intent_classifier.py` | 简单/复杂分类、简单信号优先、分类异常降级、与既有查询兼容、船长决策收窄词表回归（38 例） |
| `tests/test_planner_agent.py` | 规划生成/多步执行/总结全链路、SSE 事件序列、RAG 透出、规划失败/未知工具/总结为空/LLM 故障降级、工具失败不中断、规划偶发空响应重试（15 例） |
| `tests/test_dual_mode.py` | chat/chat_stream 分流、规划失败/分类失败降级、`AGENT_DUAL_MODE=false` 回退、记忆注入透传（10 例） |
| 存量回归 | 全部既有对话/记忆/路由测试保留通过 |

全量：**320 passed**（含船长决策 2026-08-06 词表收窄后的全量回归；阶段3 收官新增 2 例规划偶发空响应重试，详见 `doc/phase3-e2e-verification.md`）。

## 4. 真实环境冒烟验证（2026-08-06）

配置：`digitalHumanApi/.env` 真实 LLM（opencode-go + deepseek-v4-flash）。

| 场景 | 结果 |
|------|------|
| 复杂任务非流式「规划灵山一日游，带老人小孩，喜欢拍照」 | ✅ 规划路径（engine=langchain-plan），回答含「轻松休闲亲子游」路线 + 3 条 RAG 来源 |
| 复杂任务流式「帮我安排两日游，带娃，预算充足」 | ✅ SSE 步骤可视化：plan → recommend_routes → filter_by_tags → get_route_detail → search_spots → get_spot_detail → summarize → token（1042 tokens）→ done |
| 简单问答流式「景区几点开门？」 | ✅ 快速路径（无 plan 步骤），回答正常 |
| `AGENT_DUAL_MODE=false` | ✅ 复杂任务也无规划步骤（回退开关生效） |

延迟说明：规划路径耗时主要为 LLM 推理（规划 + 总结两次调用，deepseek-v4-flash
约 20-35s）；快速路径同等复杂查询约 27s——规划路径增量约 8s（多一次规划调用 +
总结聚合），简单问答快速路径不受分类器影响（分类为纯字符串匹配，微秒级）。
RAG 首次索引加载（TF-IDF 全量重向量化约 17s-2min）当时为既有已知项、与本次无关；API embedding 路径的启动重向量化已由批次3 C4 向量落盘解决（sidecar 缓存命中跳过重算，见 AGENTS.md「本地 RAG 知识库」），TF-IDF 路径行为不变。
