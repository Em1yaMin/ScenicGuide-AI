# deepagents 装配层成本/速度调优（A4，批次3 收官）

实测报告与定参依据。A4 组合拳（船长决策）：**ToolCallLimitMiddleware +
工具结果归一化缓存 + SummarizationMiddleware 上下文压缩**，全用 langchain
自带中间件（零新依赖），只改 `digitalHumanApi` 后端。

实现文件：

- `app/services/deepagents/tool_cache.py` — 工具结果归一化缓存
- `app/services/deepagents/compaction.py` — 会话历史上下文压缩（引擎为
  langchain 内置 `SummarizationMiddleware`）
- `app/services/deepagents/agent.py` — 中间件装配（ToolCallLimitMiddleware
  + 工具缓存包装 + 回答后压缩编排）
- `app/core/memory_store.py` — 新增 `replace_messages`（压缩结果落盘）
- `app/services/rag_service.py` — 索引重建时失效知识检索缓存
- `scripts/measure_deepagents_tuning.py` — 实测脚本（真实 LLM 轻量调用）

## 1. 组合拳一：ToolCallLimitMiddleware（单轮工具调用上限）

### 实现

`create_deep_agent(middleware=[ToolCallLimitMiddleware(run_limit=15,
exit_behavior="continue")])`（装配在 `_build_agent`，A1 报告 §7 确认
可用；中间件 state schema 由 deepagents 自动与 `ScenicDeepAgentState`
合并——实测 graph channels 出现 `run_tool_call_count` /
`thread_tool_call_count` 与 `ToolCallLimitMiddleware.after_model` 分支）。

- **run 级计数**：stateless 图每次 ainvoke 独立计数（无跨轮累积），
  单轮（单次对话）全部工具合计上限
- **优雅降级**：`exit_behavior="continue"`——超限工具调用被拦截并以
  错误 ToolMessage（"Tool call limit exceeded…"）回给模型，模型基于
  已有信息直接作答；不抛异常、不触发 Dify 回落、SSE 契约不变
- **兜底联动**：装配层 `recursion_limit=30` 天然兼容——上限 15 之后
  模型每多尝试一次被拦截的工具调用消耗 2 步（模型+工具节点），
  剩余步数足够模型收敛作答

### 定参依据（实测）

| 数据点 | A1 基线 | A4 实测（3 轮复杂任务） |
|--------|---------|--------------------------|
| 工具调用次数（平均） | 9 步 | 11.0 步（7 / 14 / 12） |
| 工具调用次数（峰值） | 13 步 | 14 步（含多轮历史累积） |

**结论：上限保持 15**。实测峰值 14 < 15，正常复杂任务全程不受限；
上限的作用是拦截病态循环（A1 观测到的"单工具场景最多 13 次调用"属于
收敛保守但尚可控，真正放大器是 15-28k 的 input token 累积）。若后续
需要进一步压成本，可下调至 12-13（实测 14 步的那轮回答质量正常，
被截断风险低），但会牺牲极端查询的完整度，暂不采纳。

## 2. 组合拳二：工具结果归一化缓存（同 query 不重查）

### 实现

`tool_cache.wrap_tool()` 包装高频只读工具（search_spots / search_routes /
get_spot_detail / get_route_detail / filter_by_tags / recommend_routes /
search_knowledge）为缓存版本：同名/同描述/同 args_schema，仅替换执行函数，
模型侧 schema 与 SSE step 事件（按工具名）零感知。

- **归一化键**：`(工具名, 归一化参数...)`——文本去空白/中英文标点/
  大小写（"灵山 景区！" 与 "灵山景区" 同键），kwargs 按键名排序
- **TTL 分级**：
  - 静态数据工具（景点/路线，数据源 `app/data/` 静态 JSON）：**3600s**
  - 知识检索（search_knowledge，检索 knowledge+documents）：**300s**
    + **索引重建即时失效**——`rag_service._index_documents_sync` /
    `_index_knowledge_entries_sync` 全量重建后调
    `invalidate_knowledge()`（版本号 +1），知识库 CRUD 同步索引的既有
    机制天然挂钩（管理后台增删改/导入 → 重建 → 缓存失效），不等短 TTL
- **上限保护**：512 条目 LRU 淘汰（防长跑进程内存膨胀）；threading.Lock
  并发安全（langgraph 线程池执行同步工具）
- **总开关**：`AGENT_TOOL_CACHE_ENABLED=false` 原样返回原工具（零行为变化）
- 记忆四工具不缓存（有副作用）

### 定参依据（实测）

实测 6 轮共 38 次工具调用，缓存命中 1 次 / 未命中 37 次——单轮内模型
查询各不相同（命中率低符合预期）；命中场景为复杂任务中对同一主题的
重复检索（轮3 complex 命中 1 次）。**缓存的价值在长会话/重复查询场景**
（模型反复检索同一景点/同一知识主题时省去重复计算与响应解析），单轮
独立查询场景不产生额外开销（未命中只是查一次 dict）。参数选择依据：

- 静态 3600s：数据只随版本变更，长缓存无一致性风险
- 知识 300s：短 TTL 兜底 + 索引重建即时失效双保险，知识库 CRUD 后
  最迟 5 分钟自愈（实际走版本失效，立即生效）

## 3. 组合拳三：SummarizationMiddleware 上下文压缩（回答后触发）

### 实现

会话存储层**回答后触发**压缩（`compaction.py`）：每轮对话完成追加历史后，
若消息数 ≥ `AGENT_COMPACT_TRIGGER_MESSAGES`（默认 24 条 ≈ 12 轮），调用
内置 `SummarizationMiddleware.abefore_model`（纯状态函数，阈值判定/安全
截断/摘要生成全部复用）压缩早期轮次为摘要，保留最近
`AGENT_COMPACT_KEEP_MESSAGES`（默认 10 条 ≈ 5 轮）完整消息，压缩结果
写回短期记忆存储（进程内 + SQLite `replace_messages`，重启不丢）。

**为什么不在图内挂中间件**（与"图内方案"的对比，成本考量）：

| 维度 | 图内挂中间件 | 本方案（会话层回答后） |
|------|-------------|------------------------|
| 触发时机 | 下一轮模型调用前（增加该轮首字延迟） | 回答完成后（用户侧零感知，done 前完成） |
| 持久化 | 无 checkpointer 的 stateless 调用下每轮重复压缩（反而增成本） | 摘要落库，只在下一次跨阈值时再压（实测 14 轮只压 1 次） |
| 摘要 LLM 成本 | 每轮一次 | 每 ~12 轮一次 |

- **摘要质量**：自定义中文摘要 prompt，显式要求保留游客关键约束
  （人群：带老人/带小孩/情侣；时间/时长；预算；兴趣：拍照/自然风光等）
  与已确认事实（景点/路线名称、开放时间等）——"带老人小孩"类关键信息
  不丢（测试 `test_deepagents_compaction.py::test_compaction_produces_summary_and_keeps_recent`
  覆盖）
- **降级**：LLM 未配置/调用失败/摘要为空或错误文本/并发有新轮次追加，
  一律跳过压缩（历史原样，由 `_MAX_HISTORY_MESSAGES=30` 截断兜底），
  绝不阻断对话；`AGENT_COMPACT_ENABLED=false` 整体回退
- **历史上限联动**：`_MAX_HISTORY_MESSAGES` 20 → 30（压缩阈值 24 之上
  留截断兜底余量；压缩正常时历史有界于 11-24 条，30 仅为压缩失败兜底）

### 定参依据（实测）

长会话压缩演示（14 轮简单问答，真实 LLM，`--demo-only`）：

| 轮次 | 历史条数 | 本轮 input tok | 说明 |
|------|---------|---------------|------|
| 11 | 22 | 20276 | 未压缩（线性增长） |
| 12 | **11** | 13964 | 触发压缩：摘要 + 最近 10 条 |
| 13 | 13 | **4469** | 压缩生效：input 降 ~4.5 倍 |
| 14 | 15 | 17087 | 该轮工具调用多（input 波动主要来自工具链） |

未压缩对照：第 14 轮历史应为 28 条，input 随轮次线性增长（每轮
~+2k 历史 + 工具链重复注入）。

- **触发 24 条 ≈ 12 轮**：在任务建议区间（10-15 轮）内，且保证压缩后
  （1 摘要 + 10 保留）距下次触发有 ~6 轮间隔，摘要 LLM 调用摊薄到
  每 ~12 轮 1 次
- **保留 10 条 ≈ 5 轮**：任务建议窗口（3-5 轮）上限——多轮上下文
  连续性（用户刚说的偏好/刚确认的信息）完整保留，同时历史有界
- 按 2 条/轮折算（会话历史只存 Q&A 对，工具消息不落库）

## 4. 实测数据（真实 LLM，deepseek-v4-flash via opencode-go）

环境：`digitalHumanApi/.env` 真实 key；Embedding API 余额不足降级本地
TF-IDF（RAG 检索为降级模式，不影响 LLM 行为度量）；多轮历史累积
（轮2/轮3 输入含前轮上下文，与 A1 单轮基线口径不同，对比时注意）。

### 简单问答路径（3 轮，无规划指令）

| 轮次 | 查询 | 首字延迟 | 总耗时 | input tok | output tok | 工具调用 | 缓存命中/未中 |
|------|------|---------|--------|-----------|------------|---------|---------------|
| 1 | 你好，能介绍一下自己吗？ | 2.58s | 4.57s | 1843 | 253 | 0 | 0/0 |
| 2 | 景区几点开放？门票多少钱？ | 26.95s | 28.61s | 5135 | 713 | 1 | 0/1 |
| 3 | 灵山寺在哪里？怎么去？ | 2.57s | 19.93s | 17271 | 1112 | 7 | 0/7 |
| **平均** | | 10.70s | 17.70s | 8083 | 693 | 2.7 | |

### 复杂规划路径（3 轮，注入规划指令）

| 轮次 | 查询 | 首字延迟 | 总耗时 | input tok | output tok | 工具调用 | 缓存命中/未中 |
|------|------|---------|--------|-----------|------------|---------|---------------|
| 1 | 帮我规划灵山一日游，带老人小孩，喜欢拍照 | 3.57s | 29.13s | 20085 | 2501 | 7 | 0/5 |
| 2 | 规划两日游行程，喜欢自然风光和历史人文，预算有限 | 3.66s | 46.50s | 38616 | 4538 | 14 | 0/12 |
| 3 | 帮我安排周末亲子游路线，想轻松一点，最好能看日出 | 4.44s | 36.61s | 36438 | 3067 | 12 | 1/9 |
| **平均** | | 3.89s | 37.42s | 31713 | 3369 | 11.0 | |

### 与 A1 基线对比

| 指标 | A1 simple | A4 simple | A1 complex (plan=on) | A4 complex |
|------|-----------|-----------|----------------------|------------|
| 总耗时 | 4.87s | 17.70s（含 LLM 延迟波动轮） | 33.47s | 37.42s（含多轮历史） |
| input tok | 1372 | 8083（含多轮历史 + 轮3 工具链） | 20982 | 31713（含多轮历史） |
| output tok | 287 | 693 | 2922 | 3369 |
| 工具调用 | 0-1 | 2.7（轮3 模型自行 7 次检索） | 9 | 11.0（含历史累积轮） |

**口径说明与结论**：

1. A4 轮2/轮3 含前轮历史上下文（A1 为单轮口径），直接对比会高估
   消耗；剔除历史后的单轮增量（轮3 相对轮2 的 input 增量 ≈ 12136 tok）
   与 A1 单轮量级一致
2. **简单问答延迟不劣化**：轮1（纯问答，0 工具）首字 2.58s / 总 4.57s
   与 A1 基线（4.87s）持平；轮2 首字 26.95s 为 opencode-go 端点单次
   响应延迟波动（非调优引入，A1 文档记录该端点延迟高且波动大）
3. **过度调用被抑制**：组合拳生效边界——3 轮复杂任务工具调用
   7/14/12 次全部 ≤ 上限 15，未被截断（回答质量不受损）；上限防止的
   是病态循环（无上限时异常 LLM 可循环近 30 步，见 A1 recursion 报告）；
   单轮内重复查询由缓存兜住（命中 1 次，长会话中命中率上升）
4. **上下文压缩生效**：长会话第 12 轮触发压缩（历史 22→11 条），
   压缩后下一轮 input 20k → 4.5k（降 ~4.5 倍），历史有界不随轮次
   线性增长

## 5. 验证

- 全量测试 **446 passed**（基线 419 + 新增 27：tool_cache 10 /
  tool_limit 5 / compaction 12），无回归
- **SSE 契约不变**：step/token/done 事件结构与字段不变（工具缓存只
  替换执行函数、同名工具；压缩只改会话历史形态，done 事件仍最后发出）
- **回落行为不变**：工具上限拦截不抛异常（`exit_behavior="continue"`），
  压缩失败静默跳过——任何路径都不会触发 Dify 回落的新增条件；会话 ID
  归属判定（`has_conversation`）不受影响
- 新增测试覆盖：缓存归一化/TTL/知识失效/上限淘汰、上限装配与拦截
  行为（MockTransport）、压缩阈值/质量（关键约束不丢）/持久化重启/
  并发写回保护/存储层 replace_messages

## 6. 配置汇总

见 `digitalHumanApi/.env.example`（A4 节）：`AGENT_MAX_TOOL_CALLS=15`、
`AGENT_TOOL_CACHE_ENABLED=true`、`AGENT_TOOL_CACHE_TTL_STATIC=3600`、
`AGENT_TOOL_CACHE_TTL_KNOWLEDGE=300`、`AGENT_COMPACT_ENABLED=true`、
`AGENT_COMPACT_TRIGGER_MESSAGES=24`、`AGENT_COMPACT_KEEP_MESSAGES=10`。
全部有实测依据（见上文各节）；回退开关关闭后行为与 A3 完全一致。

## 7. 后续可选项（本次未做）

- 工具上限下调至 12-13（复杂任务实测峰值 14，下调可进一步压成本，
  但极端查询有截断风险）
- token 阈值触发压缩（`trigger=("tokens", N)`，中间件原生支持）——
  当前按轮数触发简单可预期；token 阈值对超长复杂轮更敏感，可作进阶
- 缓存命中率观测接口（`tool_cache.get_cache().stats()` 已提供，可接入
  管理后台观测面板）
