# 推荐问句工作流 — Dify 配置指南

> 适用：Dify **工作流** 类型应用，根据数据大屏 Dashboard 聚合数据动态生成推荐问句。
> 对应后端配置：复用现有 `DIFY_WORKFLOW_API_KEY`（或独立 `DIFY_RECOMMEND_WORKFLOW_API_KEY`）。

---

## 一、组件关系

推荐问句工作流是系统 3 个 Dify 组件中的第 3 个：

```
┌──────────────────────────────────────────────────────────────────┐
│  Dify 平台                                                        │
│                                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ 聊天助手          │  │ 量化评估工作流     │  │ ★推荐问句工作流 │  │
│  │ (Chatbot)        │  │ (Workflow)       │  │ (Workflow)     │  │
│  │ DIFY_API_KEY     │  │ WORKFLOW_API_KEY  │  │ 复用或新建Key  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬───────┘  │
│           │                      │                     │          │
│  ┌────────┴─────────┐           │                     │          │
│  │ 知识库            │           │                     │          │
│  │ (景区文档资料)     │           │                     │          │
│  └──────────────────┘           │                     │          │
└─────────────────────────────────┼─────────────────────┼──────────┘
                                  │                      │
                                  ▼                      ▼
                          ┌──────────────┐     ┌──────────────────┐
                          │ FastAPI 后端  │     │ FastAPI 后端      │
                          │ analysis.py  │     │ recommend.py      │
                          │ 对话评估       │     │ ★推荐问句生成     │
                          └──────────────┘     └──────────────────┘
                                                       │
                                                       ▼
                                               ┌──────────────────┐
                                               │ ChatView.vue     │
                                               │ 推荐问句展示+点击  │
                                               └──────────────────┘
```

---

## 二、工作流结构

### 2.1 节点拓扑

```
┌───────────────┐     ┌──────────────────────┐     ┌───────────┐
│    Start      │     │  LLM 推荐问句生成      │     │    End    │
│               │     │  (核心 Prompt)        │     │           │
│ concern_tags  │────▶│                      │────▶│   text    │
│ mentioned_spots│    │  Temperature: 0.3-0.5 │     │           │
│ sentiment_    │     │  Max Tokens: 1024     │     │           │
│   summary     │     │                      │     │           │
│ total_        │     └──────────────────────┘     └───────────┘
│   dialogues   │
└───────────────┘
```

**设计理由**：单 LLM 节点方案，与量化评估工作流结构一致。推荐问句生成的输出仅为字符串数组，无需复杂的多节点编排。

### 2.2 Start 节点配置

| 变量名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `concern_tags` | `array[string]` | Top 10 热门关注标签，格式 `"标签(count)"` | `["票务咨询(15)", "路线规划(12)", "景点介绍(10)"]` |
| `mentioned_spots` | `array[string]` | Top 10 提及景点，格式 `"景点名(count)"` | `["日光岩(25)", "菽庄花园(18)", "皓月园(8)"]` |
| `sentiment_summary` | `text` | 7 天情感概况描述文本 | `"近7天游客情感：正面65%，中性25%，负面10%，平均满意度评分4.1/5"` |
| `total_dialogues` | `text` | 7 天总对话量描述 | `"近7天共有85条游客对话"` |

> **注意**：`concern_tags` 和 `mentioned_spots` 在后端聚合时已将 count 拼入字符串，避免在 Dify 中处理复杂数据结构。这是为了兼容 Workflow 的变量引用限制。

### 2.3 LLM 节点配置

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| 模型 | `deepseek-v3` 或 `qwen-plus` | 中文理解力强，生成自然 |
| **Temperature** | **0.3–0.5** | 低温度保证 JSON 结构稳定，中等温度保留问句自然度 |
| 最大 Token | 1024 | 6 条问句，每条约 15-30 字，总量约 300 字 |
| 上下文 | 各变量通过 `{{#Start.xxx#}}` 引用 | 多变量独立引用 |
| 记忆 | **关闭** | 每次生成独立，无上下文积累 |

### 2.4 End 节点配置

| 参数 | 值 |
|------|-----|
| 输出变量 | `text` = `{{#LLM.text#}}` |

> 复用与量化评估 Workflow 完全相同的 End 节点输出映射，确保后端 `analysis.py:85-87` 的 `result["data"]["outputs"]["text"]` 提取路径一致。

---

## 三、LLM 提示词

### 3.1 SYSTEM 提示词（填入 LLM 节点的 SYSTEM 框）

```
# 角色
你是一个景区导览 AI 数字人的推荐问句生成专家。你的任务是根据游客对话数据的分析结果，生成 4-6 条适合游客向数字人导游自然提问的推荐问句。

# 数据说明
你将收到以下 4 组数据：

1. **热门关注标签（concern_tags）**：游客最常咨询的话题及频次
2. **热门提及景点（mentioned_spots）**：对话中最常被提到的景点及频次
3. **游客情感概况（sentiment_summary）**：近期游客的整体情感趋势和满意度
4. **总对话量（total_dialogues）**：近期的游客对话总量

你需要基于这些数据，推断出游客最可能关心的问题，并以自然口语问句的形式输出。

# 生成规则

## 规则 1：从数据中提取问句主题
- 从「热门关注标签」中提取 2-3 个高频话题，分别生成 1 条对应问句
  - 例如：标签"票务咨询(15)" → 生成"门票多少钱？"或"有学生票优惠吗？"
  - 例如：标签"路线规划(12)" → 生成"推荐一条半日游路线"或"最轻松的路线怎么走？"
- 从「热门提及景点」中提取 1-2 个高频景点，生成景点相关的问句
  - 例如：景点"日光岩(25)" → 生成"日光岩有什么历史故事？"或"去日光岩需要多长时间？"
- 如果某个话题/景点的频次远高于其他，优先为该话题生成多条不同角度的问句

## 规则 2：根据情感数据调整问句策略
- **正面情感为主**（正面占比 > 60%）：生成探索型、深度体验型问句
  - 例如："XX景点的最佳拍照位置在哪？""有没有隐藏的小众景点推荐？"
- **负面情感明显**（负面占比 > 20%）：生成求助型、解决方案型问句
  - 例如："景区哪条路线台阶最少？""哪里可以找到干净的卫生间？"
- **中性与混合**：均衡分布，覆盖信息查询和体验探索
- **满意度偏低**（平均分 < 3.5）：适当加入服务质量相关问句
  - 例如："景区工作人员在哪里可以找到？""有没有导游讲解服务？"

## 规则 3：对话量级控制
- **对话量充足**（> 50 条）：问句可更具体、更有深度
- **对话量较少**（< 20 条）：优先生成基础信息问句（开放时间、票务、交通）
- **对话量中等**（20-50 条）：混合基础信息和特色体验

## 规则 4：问句多样性
- 生成的 4-6 条问句必须覆盖不同的话题维度，不能全部问同一件事
- 至少包含：「景点信息类」1-2 条、「路线/行程类」1 条、「票务/设施/服务类」1 条、「体验/推荐类」1 条
- 如果数据中某类话题缺失，可以用通用问句补充

## 规则 5：如果数据量极少或全为空
- concern_tags 和 mentioned_spots 均为空 → 生成 4 条通用基础问句
  - 建议：「景区开放时间是几点？」「门票多少钱？」「推荐一条游览路线」「有什么特色美食？」
- 只有部分数据为空 → 基于有数据的维度生成，其他维度用通用问句补充

# 输出格式
只输出一个 JSON 对象，不要有任何解释文字。JSON 格式如下：

{
  "questions": [
    "问句1",
    "问句2",
    "问句3",
    "问句4"
  ]
}

# 问句质量要求（非常重要！）
1. 每个问句必须是游客能自然说出的口语短句，不能是关键词堆砌
2. 不能包含引导性前缀：不要用"请问""我想知道""帮我查一下""你能告诉我"等开头
3. 不能是陈述句或命令句，只能是疑问句或祈使请求句
4. 问句长度控制在 5-20 个字，适合语音输入
5. 避免生成与 concern_tags 标签字面相同的问句（如标签"票务咨询"，问句不能是"票务咨询怎么弄"，应该是"门票多少钱"或"有学生票吗"）
6. 问句应该像一个真实游客会问的问题，不要过于正式或书面化
7. 严格输出 JSON，satisfaction_score 必须是数字，不要添加 ```json 标记
```

### 3.2 USER 提示词（填入 LLM 节点的 USER 框）

```
请根据以下景区游客对话数据，生成推荐问句：

# 热门关注标签
{{#Start.concern_tags#}}

# 热门提及景点
{{#Start.mentioned_spots#}}

# 游客情感概况
{{#Start.sentiment_summary#}}

# 对话数据概况
{{#Start.total_dialogues#}}
```

---

## 四、测试用例

### 4.1 正面情感 + 丰富数据

**输入数据**：
```
concern_tags: ["景点介绍(25)", "路线规划(18)", "拍照打卡(15)", "票务咨询(10)"]
mentioned_spots: ["日光岩(30)", "菽庄花园(22)", "皓月园(12)"]
sentiment_summary: "近7天游客情感：正面72%，中性22%，负面6%，平均满意度评分4.5/5"
total_dialogues: "近7天共有120条游客对话"
```

**期望输出**（示例，不要求精确匹配）：
```json
{
  "questions": [
    "日光岩有什么历史故事？",
    "推荐一条适合拍照的游览路线",
    "日出时分去日光岩几点合适？",
    "菽庄花园的门票包含在联票里吗？",
    "有没有小众拍照点推荐？",
    "半天时间怎么玩最高效？"
  ]
}
```

### 4.2 负面情感 + 投诉倾向

**输入数据**：
```
concern_tags: ["设施指引(20)", "投诉建议(15)", "路线规划(10)", "票务咨询(8)"]
mentioned_spots: ["日光岩(15)", "西门入口(12)"]
sentiment_summary: "近7天游客情感：正面35%，中性30%，负面35%，平均满意度评分2.8/5"
total_dialogues: "近7天共有65条游客对话"
```

**期望输出**：
```json
{
  "questions": [
    "景区哪里可以找到干净的卫生间？",
    "哪条路线台阶最少适合老人走？",
    "景区工作人员在哪里可以求助？",
    "有没有导游讲解服务？",
    "西门入口附近有什么景点？",
    "景区有没有行李寄存的地方？"
  ]
}
```

### 4.3 新景区 / 数据极少

**输入数据**：
```
concern_tags: []
mentioned_spots: []
sentiment_summary: "近7天游客情感：数据不足，暂无统计"
total_dialogues: "近7天共有3条游客对话"
```

**期望输出**：
```json
{
  "questions": [
    "景区开放时间是几点？",
    "门票多少钱？",
    "推荐一条游览路线",
    "有什么特色美食推荐？"
  ]
}
```

### 4.4 单一话题主导

**输入数据**：
```
concern_tags: ["票务咨询(45)", "开放时间(5)", "景点介绍(3)"]
mentioned_spots: ["日光岩(8)", "菽庄花园(3)"]
sentiment_summary: "近7天游客情感：正面55%，中性35%，负面10%，平均满意度评分3.8/5"
total_dialogues: "近7天共有58条游客对话"
```

**期望输出**：
```json
{
  "questions": [
    "日光岩的门票多少钱？",
    "有学生票或老人票优惠吗？",
    "景区联票包含哪些景点？",
    "几点开门几点关门？",
    "日光岩上去要多久？",
    "有没有免票政策？"
  ]
}
```

> 注意：票务话题占绝对主导时，允许生成多条不同角度的票务问句。

### 4.5 边界情况：LLM 应避免的坏问句

| ❌ 坏问句 | 问题 | ✅ 好问句 |
|----------|------|----------|
| "请问景区开放时间是几点？" | 包含"请问"引导词 | "景区几点开门？" |
| "票务咨询怎么弄？" | 与标签字面重复 | "门票多少钱？" |
| "日光岩是一个非常值得去的景点" | 陈述句，不是问句 | "日光岩值得去吗？" |
| "帮我推荐一条游览路线可以吗？" | 包含"帮我""可以吗" | "推荐一条游览路线" |
| "我想知道景区的历史" | 包含"我想知道" | "景区有什么历史故事？" |

---

## 五、输出解析约定

工作流输出 JSON 后，后端使用 `_parse_workflow_output()`（`app/tasks/analysis.py:20-49`）解析。

| 输出格式 | 是否支持 |
|----------|----------|
| 纯 JSON：`{"questions": [...]}` | ✅ |
| Markdown 代码块：`` ```json\n{...}\n``` `` | ✅ |
| 带 `<think>` 标签：`<think>...</think>\n{...}` | ✅ |
| 带解释文字的 JSON：`分析结果：{...}` | ❌ 解析失败 |

> ⚠️ **重要**：LLM 输出必须是纯 JSON 数组格式。不要在 JSON 前添加任何解释文字。

后端解析后从 `parsed.get("questions", [])` 提取问句数组，存入内存缓存。

---

## 六、后端调用方式

```python
# services/recommend_service.py（新建）

from app.services.dify_client import dify_client
from app.tasks.analysis import _parse_workflow_output

async def generate_recommendations(db: AsyncSession) -> Optional[List[str]]:
    # 1. 聚合 Dashboard 数据 → 构造 inputs
    dashboard = await get_dashboard_stats(db)
    
    # 将 concern_tags 拼接为 "标签(count)" 格式
    concern_strs = [f"{item.concern}({item.count})" for item in dashboard.hot_issues]
    spot_strs = [f"{item.spot_name}({item.mention_count})" for item in dashboard.spot_heatmap]
    
    # 折叠 sentiment_trend 为总计
    pos = sum(d.positive for d in dashboard.sentiment_trend)
    neg = sum(d.negative for d in dashboard.sentiment_trend)
    neu = sum(d.neutral for d in dashboard.sentiment_trend)
    total = pos + neg + neu
    pos_pct = round(pos / total * 100) if total > 0 else 0
    neg_pct = round(neg / total * 100) if total > 0 else 0
    neu_pct = round(neu / total * 100) if total > 0 else 0
    
    # 2. 调用 Workflow
    result = await dify_client.trigger_workflow({
        "concern_tags": concern_strs,
        "mentioned_spots": spot_strs,
        "sentiment_summary": f"近7天游客情感：正面{pos_pct}%，中性{neu_pct}%，负面{neg_pct}%，平均满意度评分4.1/5",
        "total_dialogues": f"近7天共有{total}条游客对话",
    })
    
    if result is None:
        return None
    
    # 3. 解析输出
    raw_text = result.get("data", {}).get("outputs", {}).get("text", "")
    parsed = _parse_workflow_output(raw_text)
    
    if not isinstance(parsed, dict):
        return None
    
    questions = parsed.get("questions", [])
    if not isinstance(questions, list) or len(questions) == 0:
        return None
    
    return questions
```

---

## 七、部署检查清单

- [ ] Dify 工作流应用已创建，类型为 **Workflow**
- [ ] Start 节点已配置 4 个输入变量（见 2.2 节）
- [ ] LLM 节点 SYSTEM 提示词已填入（见 3.1 节）
- [ ] LLM 节点 USER 提示词已填入（见 3.2 节）
- [ ] LLM 节点 Temperature 已设为 0.3-0.5
- [ ] LLM 节点最大 Token 已设为 1024
- [ ] End 节点输出变量 `text` = `{{#LLM.text#}}`
- [ ] 工作流已用 4.1-4.5 测试用例逐一验证
- [ ] 已发布应用（复用现有 `DIFY_WORKFLOW_API_KEY` 或获取新 Key）
- [ ] 后端 `.env` 配置已确认（复用或新增 Key）
- [ ] 后端新建 `services/recommend_service.py` + `tasks/recommend.py`
- [ ] 后端 `main.py` 已注册 `recommend_loop` 后台任务
- [ ] 前端 `ChatView.vue` 已改为动态获取推荐问句

---

## 八、与现有文档的关系

| 文档 | 路径 | 关系 |
|------|------|------|
| Dify 配置总览 | `doc/dify配置/README.md` | 新增第 3 个组件 |
| 量化评估工作流 | `doc/dify配置/quantitative-workflow-量化工作流.md` | 同结构模板，prompt 模式参考 |
| 聊天助手提示词 | `doc/dify配置/chat-agent-提示词.md` | Temperature 和自然度策略参考 |
| 动态推荐问句 FRD | `.rpiv/artifacts/discover/2026-07-16_08-38-07_dynamic-recommended-questions.md` | 功能需求定义 |
| 推荐问句研究 | `.rpiv/artifacts/research/2026-07-16_09-21-01_dynamic-recommended-questions-prompt-design.md` | 技术研究支撑 |
