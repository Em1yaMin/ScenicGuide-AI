# Dify Workflow 量化分析规格

> ⚠️ **已 superseded（阶段4 首项，2026-08-07 船长决策方案A）**：量化评估已改为**本地 LLM**（`app/services/evaluation_service.py`，复用 `LLM_*` 配置 opencode-go），**不再调用 Dify Workflow**，`DIFY_WORKFLOW_API_KEY` 与 `dify_client.trigger_workflow` 已移除。本文档仅作为历史设计存档保留（提示词语义已移植到本地 LLM 版 System 提示词，见 `evaluation_service._EVALUATION_SYSTEM_PROMPT`）。

后端（阶段4 前）通过 `DIFY_WORKFLOW_API_KEY` 调用 Dify Workflow 应用，对每条对话进行情感量化分析。该 Workflow 必须符合以下规格，否则量化分析链路无法工作。

> 完整工作流设计背景参见 [`quantitative-workflow-量化工作流.md`](quantitative-workflow-量化工作流.md)。

---

## 入参（开始节点）

| 变量名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `input` | paragraph (≤1024) | 是 | 对话文本（后端传 `dialogue.content`） |

## 节点编排

```
开始节点 (input: paragraph) → LLM 节点 (结构化分析, temperature=0) → 结束节点 (text = LLM.text)
```

## LLM 节点提示词要点

要求 LLM 只输出 JSON（不要 markdown 代码块），字段如下：

| 字段 | 类型 | 取值 |
|------|------|------|
| `sentiment_label` | string | `positive` / `neutral` / `negative` / `happy` / `confused` |
| `satisfaction_score` | int | 1-5，无法判断给 3 |
| `concern_tags` | string[] | 关注热点，如 `["路线","门票"]`，无则 `[]` |
| `mentioned_spots` | string[] | 提到的景点，如 `["长廊"]`，无则 `[]` |

## 输出（结束节点）

| 输出变量名 | 来源 |
|-----------|------|
| `text` | LLM 节点输出（后端从 `result.data.outputs.text` 取值） |

## 后端解析容错

后端 `_parse_workflow_output()`（`app/tasks/analysis.py`）会自动：
- 剥离 `...` 推理块
- 剥离 markdown 代码块 ```` ```json ````
- 字段名备选：`sentiment` / `concerns` / `spots` 也可识别

## 验证命令

```bash
curl -X POST "http://YOUR_DIFY_SERVER_IP/v1/workflows/run" \
  -H "Authorization: Bearer $DIFY_WORKFLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"inputs":{"input":"你好,请问长廊怎么走"},"response_mode":"blocking","user":"system"}'
```

期望 `data.status=succeeded` 且 `data.outputs.text` 为合法 JSON 字符串。
