# 推荐问句生成工作流设计

> 适用：Dify **工作流** 类型应用，用于根据 Dashboard 聚合数据动态生成游客推荐问句。
> 对应后端配置：`DIFY_RECOMMEND_WORKFLOW_API_KEY`（推荐 Workflow 独立密钥）。

---

> ⚠️ **注意**：本文档取代原有的 `doc/推荐工作流/recommend-workflow-推荐问句工作流.md`。旧文档已 superseded，请以本文档为准。

## 一、背景

### 1.1 工作流定位

推荐问句生成工作流在整个系统中的角色：

```
FastAPI 后台任务(每5分钟) → 聚合7天Dashboard数据 → 调用本工作流 → 推荐问句 → 内存缓存
                                                                              ↓
                                                               GET /api/v1/visitor/recommended-questions
                                                                              ↓
                                                                      前端 ChatView 展示
```

### 1.2 后端调用方式

主后端通过 `dify_client.trigger_workflow()` 调用本工作流，使用 `DIFY_RECOMMEND_WORKFLOW_API_KEY`：

```python
# digitalHumanApi/app/services/recommend_service.py
inputs = {
    "concern_tags": [{"tag": "票务咨询", "count": 15}, ...],
    "mentioned_spots": [{"spot": "日光岩", "count": 22}, ...],
    "sentiment_summary": "positive:45, negative:12, neutral:30",
    "total_dialogues": 87,
}
result = await dify_client.trigger_workflow(inputs)
raw_output = result.get("data", {}).get("outputs", {}).get("text", "")
```

**关键约束**：
- 调用方式：`POST /workflows/run`，`response_mode: "blocking"`（同步等待结果）
- 输入变量：4 个独立变量（详见 2.2 节 Start 节点配置）
- 输出字段名：`text`（对应 LLM 节点）
- 输出格式：JSON 字符串（可包裹在 ```json 代码块中，后端自动剥离）
- Temperature：0.3-0.5（平衡结构化输出与问句自然度）

### 1.3 期望输出结构

后端 `fetch_recommended_questions()` 解析以下字段：

| 字段名 | 类型 | 必需 | 说明 |
|--------|------|------|------|
| `questions` | string[] | 是 | 推荐问句列表，5-8 条，每条 5-20 字 |

> **容错设计**：代码同时接受字段别名（`recommended_questions` → `questions`，`suggestions` → `questions`），但不建议依赖别名。

---

## 二、工作流结构

### 2.1 节点拓扑

```
┌──────────────────┐     ┌─────────────────────┐     ┌───────────┐
│  Start            │────▶│  LLM 推荐问句生成     │────▶│   End     │
│  (4 个输入变量)    │     │  (核心 Prompt)       │     │  (text)   │
└──────────────────┘     └─────────────────────┘     └───────────┘
```

**单 LLM 节点设计** — 推荐问句生成不需要多节点编排，单次 LLM 调用即可完成从数据到问句的转换。

### 2.2 节点配置

#### Start 节点

| 变量名 | 类型 | 说明 |
|--------|------|------|
| `concern_tags` | Text | JSON 数组字符串：`[{"tag":"票务咨询","count":15}, ...]` Top 10 热门关注点 |
| `mentioned_spots` | Text | JSON 数组字符串：`[{"spot":"日光岩","count":22}, ...]` Top 10 热门景点 |
| `sentiment_summary` | Text | 情感汇总字符串：`"positive:45, negative:12, neutral:30"` |
| `total_dialogues` | Number | 7 天内总对话数量 |

#### LLM 节点

| 参数 | 值 |
|------|-----|
| 名称 | `推荐问句生成` |
| 模型 | `deepseek-v3` 或 `qwen-plus`（推荐，中文理解力强） |
| Temperature | **0.4**（比量化评估的 0.1 高，保证问句语言自然度；比 Chat Agent 的 0.7 低，保证输出结构稳定） |
| 最大 Token | 1024 |
| 上下文 | 通过 `{{#Start.concern_tags#}}` 等引用 4 个输入变量 |
| 记忆 | 关闭（每次推荐独立，无上下文积累） |

#### End 节点

| 参数 | 值 |
|------|-----|
| 输出变量 | `text` = `{{#LLM.text#}}`（引用 LLM 节点的文本输出） |

---

## 三、LLM 节点提示词

### 3.1 提示词全文

> 以下内容填入 LLM 节点的「SYSTEM」提示词框（系统级指令）。

```
你是一个景区导览推荐问句生成专家。你的任务是根据景区近7天的游客关注热点数据，生成5-8条游客可能感兴趣的推荐问句，展示在数字人对话界面的引导区。

# 数据解读指南

你会收到以下4个维度的数据，请先理解数据含义再生成问句：

## 1. 热门关注点 (concern_tags)
JSON数组，每项包含 tag(关注标签) 和 count(出现次数)。
反映了游客最近在咨询什么话题（如"票务咨询""景点介绍""路线规划"等）。
- 高热度标签：这些是游客最常问的问题方向，直接作为问句主题
- 低热度但有趣：也可偶尔选取，提供多样性

## 2. 热门景点 (mentioned_spots)
JSON数组，每项包含 spot(景点名称) 和 count(提及次数)。
反映了游客最感兴趣的景点。
- 高热度景点：围绕它们生成"介绍类""攻略类"问句
- 多个景点可组合生成"对比类""路线类"问句

## 3. 情感汇总 (sentiment_summary)
格式：positive:N, negative:N, neutral:N
反映游客整体情绪分布。
- 负面情绪偏高：生成安抚性、服务改善类问句（如"如何反馈建议？"）
- 正面情绪偏高：生成游玩推荐、打卡分享类问句
- 中性居多：以实用信息类问句为主

## 4. 总对话数 (total_dialogues)
7天内对话总量。
- 仅作为生成数量的参考，不影响问句内容
- 对话量很少（<10）时，适当减少问句数量

# 问句生成规则

1. **每个问句必须是游客能自然说出的口语短句** — 不是关键词堆砌，不是标题
2. **不能包含引导性前缀** — 禁止使用"请问""我想知道""帮我查一下""你能告诉我"等，直接给出问句主体
3. **不能是陈述句或命令句** — 只能是疑问句或祈使请求句
4. **问句长度 5-20 字** — 适合语音输入和快速点击
5. **避免与 concern_tags 标签文字直接重复** — 用自然语言改写，不是贴标签
6. **问句之间覆盖不同话题维度** — 避免5条问句都在问同一类问题

问句类型分布建议（5-8条时）：
- 景点介绍类：2-3条（从热门景点中选）
- 实用信息类：1-2条（路线、票务、时间等，从关注点中选）
- 游玩攻略类：1-2条（玩法、美食、打卡，创造性生成）
- 服务互动类：1条（反馈、建议、情感安抚）

# 输出格式
只输出一个 JSON 对象，不要有任何解释文字。JSON 格式如下：

{
  "questions": ["问句1", "问句2", "问句3", "问句4", "问句5"]
}

示例（基于鼓浪屿景区数据）：

{
  "questions": [
    "日光岩几点去最好看？",
    "推荐一条适合亲子的游览路线",
    "菽庄花园和皓月园哪个更值得去？",
    "附近有什么特色小吃？",
    "景区门票有优惠吗？"
  ]
}

注意：
- questions 数组长度 5-8 条，当数据量很少时不少于 3 条
- 所有问句使用中文
- 直接输出 JSON，不要包装在 ```json 或其他标记中
```

> 以上填入 LLM 节点的 **SYSTEM** 框。

### 3.2 USER 提示词（用户消息）

LLM 节点的「USER」框填入：

```
请根据以下景区近7天数据，生成推荐问句：

热门关注点：
{{#Start.concern_tags#}}

热门景点：
{{#Start.mentioned_spots#}}

情感分布：
{{#Start.sentiment_summary#}}

7天总对话数：{{#Start.total_dialogues#}}
```

---

## 四、工作流测试用例

### 4.1 正常数据 — 多话题多景点

**输入**：
- concern_tags: `[{"tag":"景点介绍","count":25},{"tag":"票务咨询","count":18},{"tag":"路线规划","count":15},{"tag":"亲子游玩","count":10},{"tag":"拍照打卡","count":8}]`
- mentioned_spots: `[{"spot":"日光岩","count":22},{"spot":"菽庄花园","count":18},{"spot":"皓月园","count":12}]`
- sentiment_summary: `"positive:45, negative:12, neutral:30"`
- total_dialogues: `87`

**期望输出**：
```json
{
  "questions": [
    "日光岩有什么历史故事？",
    "菽庄花园的门票多少钱？",
    "带孩子去哪个景点比较合适？",
    "皓月园附近有什么好吃的？",
    "鼓浪屿最佳拍照点在哪里？",
    "推荐一条不走回头路的路线"
  ]
}
```
**验证点**：5-8 条问句，覆盖景点介绍、票务、亲子、拍照、路线等维度，每条 5-20 字，无引导前缀。

### 4.2 负面情绪偏高

**输入**：
- concern_tags: `[{"tag":"投诉建议","count":20},{"tag":"设施指引","count":15},{"tag":"票务咨询","count":10}]`
- mentioned_spots: `[{"spot":"日光岩","count":8}]`
- sentiment_summary: `"positive:15, negative:35, neutral:20"`
- total_dialogues: `70`

**期望输出**：
```json
{
  "questions": [
    "日光岩的开放时间是几点？",
    "景区卫生间在哪里？",
    "如何反馈游览体验？",
    "景区有行李寄存服务吗？",
    "门票可以网上提前买吗？"
  ]
}
```
**验证点**：当前负面情绪较高，应有服务改善类问句（如"如何反馈游览体验？"），整体偏实用信息。

### 4.3 数据稀少（低对话量）

**输入**：
- concern_tags: `[{"tag":"景点介绍","count":3}]`
- mentioned_spots: `[{"spot":"日光岩","count":2}]`
- sentiment_summary: `"positive:2, negative:1, neutral:2"`
- total_dialogues: `5`

**期望输出**：
```json
{
  "questions": [
    "日光岩有什么好看的？",
    "景区有哪些必去景点？",
    "游览鼓浪屿需要多长时间？",
    "景区门票怎么买？"
  ]
}
```
**验证点**：数据少时问句数量 3-4 条（不少于 3 条），问句偏向通用推荐。

### 4.4 空数据（无 evaluations）

**输入**：
- concern_tags: `[]`
- mentioned_spots: `[]`
- sentiment_summary: `"positive:0, negative:0, neutral:0"`
- total_dialogues: `0`

**期望输出**：
```json
{
  "questions": [
    "鼓浪屿有哪些必去景点？",
    "景区开放时间是几点？",
    "推荐一条经典游览路线",
    "附近有什么特色美食？",
    "景区门票有优惠吗？"
  ]
}
```
**验证点**：无数据时回退到通用推荐问句（不少于 3 条），不能返回空数组。

---

## 五、输出解析约定

工作流输出 JSON 后，后端使用与量化评估 Workflow 相同的 `_parse_workflow_output()` 解析链（从 `app/tasks/analysis.py` 提取为共享工具函数）。兼容以下格式变体：

| 输出格式 | 是否支持 |
|----------|----------|
| 纯 JSON：`{"questions": ["..."]}` | ✅ |
| Markdown 代码块：`` ```json\n{...}\n``` `` | ✅ |
| 带 `<think>` 标签：`<think>...</think>\n{...}` | ✅ |
| 带解释文字的 JSON：`以下是推荐：{...}` | ❌ 解析失败 |

> ⚠️ **重要**：LLM 输出必须是纯 JSON 或以 JSON 开头（`<think>` 标签除外）。不要在 JSON 前添加任何解释性文字。

---

## 六、部署检查清单

在 Dify 中完成以下配置后，工作流方可正常使用：

- [ ] 工作流应用已创建，类型为 **Workflow**，应用 Key 为 `app-YOUR_RECOMMEND_WF_KEY`
- [ ] Start 节点已配置，4 个输入变量：`concern_tags`（Text）、`mentioned_spots`（Text）、`sentiment_summary`（Text）、`total_dialogues`（Number）
- [ ] LLM 节点已添加，SYSTEM 提示词已填入（见 3.1 节）
- [ ] LLM 节点 USER 提示词已填入（见 3.2 节），正确引用 `{{#Start.xxx#}}`
- [ ] LLM 节点 Temperature 已设为 **0.4**
- [ ] LLM 节点 Memory 已关闭
- [ ] End 节点输出变量 `text` = `{{#LLM.text#}}`
- [ ] 工作流已测试（用 4.1-4.4 测试用例逐一验证）
- [ ] 已发布应用并确认 API Key（即 `DIFY_RECOMMEND_WORKFLOW_API_KEY`）
- [ ] 后端 `.env` 中 `DIFY_RECOMMEND_WORKFLOW_API_KEY=app-YOUR_RECOMMEND_WF_KEY` 已配置
- [ ] 启动后端后 `/api/v1/visitor/recommended-questions` 可正常返回

---

## 七、成本控制

### 7.1 调用频率

后端每 5 分钟调用 1 次推荐 Workflow，日调用量：

```
24h × 12 次/h = 288 次/天
```

### 7.2 成本估算

| 模型 | 每次 Token 消耗 | 日成本 |
|------|----------------|--------|
| deepseek-v3 | ~500 input + ~200 output | < ¥0.05 |
| qwen-plus | ~500 input + ~200 output | < ¥0.03 |

> 每次 LLM 调用仅 ~700 token（输入数据简短，输出 5-8 句中文），成本极低。

### 7.3 降级策略

- 首次启动时缓存为空，API 返回 `{ questions: [], total: 0 }`
- Dify 调用失败时保留上次缓存（stale-while-revalidate），不写入空值
- Dify API 不可用时不影响其他功能（Chat Agent、量化评估 Workflow 独立运行）
