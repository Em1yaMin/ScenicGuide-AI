# LLM 接入层冒烟测试报告（阶段1 · LLM 接入层）

> 日期：2026-08-05 ｜ 任务：对话主链路 Dify → LangChain agent（阶段1 第一个任务）
> 代码：`digitalHumanApi/app/core/llm.py`（工厂）＋ `digitalHumanApi/scripts/smoke_llm.py`（冒烟脚本）
> 结论：**opencode-go 端点 + deepseek-v4-flash 在 LangChain ChatOpenAI 下兼容，无需自定义 reasoning 剥离，仅 max_tokens 字段需经工厂 extra_body 注入**

## 1. 验证环境

| 项 | 值 |
|------|------|
| 主端点 | `https://opencode.ai/zen/go/v1`（opencode-go，OpenAI 兼容） |
| 模型 | `deepseek-v4-flash` |
| 依赖 | langchain-openai 1.4.1 / langchain-core 1.5.3 / openai SDK 2.53.0 / httpx |
| 冒烟命令 | `LLM_API_KEY=sk-... python -m scripts.smoke_llm` |

## 2. 实测证据（opencode-go 主端点，真实对话）

### 2.1 基本调用 + reasoning_content 剥离

```
content: '杭州西湖是杭州的标志性湖泊，以"三面云山一面城"的秀美自然风光和断桥残雪、苏堤春晓等十景闻名于世。'
additional_kwargs: {"refusal": null}
usage: completion=56 reasoning=17
[PASS] 基本调用 content 非空且思考未泄漏
```

**结论**：DeepSeek 的 `reasoning_content` 字段被 langchain-openai 自动剥离——`invoke` 的
`content` 即最终回答，思考内容不进入 `additional_kwargs`（openai SDK 2.53 的原始
响应 `model_extra` 中可看到 `reasoning_content`，但 LangChain 层已丢弃）。思考 token
数可从 `response_metadata.token_usage.completion_tokens_details.reasoning_tokens` 统计。

### 2.2 流式调用

```
思考阶段空 chunk 数: 20
合并 content: '杭州西湖是杭州的标志性风景名胜……'（完整回答）
[PASS] 流式正常、思考 chunk 被自动丢弃
```

**结论**：思考阶段产生 20 个空 content chunk（`reasoning_content` 被丢弃），随后正常输出。
**不报错、无需自定义处理**；下游消费方（如对话 agent 的流式输出）需容忍空 chunk
（合并时跳过即可，ADH 前端已有类似空串过滤）。

### 2.3 max_tokens 字段（唯一需要兼容性处理的点）

```
请求体字段: ['max_tokens', 'messages', 'model', 'stream', 'temperature']
max_tokens=64  max_completion_tokens=None
实际 completion_tokens=48（含思考）
[PASS] 请求体含原生 max_tokens 且端点遵守长度限制
```

**结论**：langchain-openai 1.4.1 硬编码把 `max_tokens` 重命名为 `max_completion_tokens`
发送（OpenAI 新命名，见 `chat_models/base.py` 的 `_default_params`/`_get_request_payload`），
而 opencode-go 网关的 deepseek-v4-flash 只识别 `max_tokens`（`maxTokensField: "max_tokens"`），
**`max_completion_tokens` 被静默忽略 → 长度限制完全失效**（实测请求 max_tokens=24 输出 502 tokens）。

**处理**：工厂把 `max_tokens` 参数经 openai SDK 的 `extra_body` 注入原生 `max_tokens` 字段，
实测严格生效（completion=48 ≤ 64）。**注意**：不要在 `invoke()` 时显式传 `max_tokens`（会被
langchain 重命名），一律走工厂参数。

### 2.4 多轮对话（历史消息不带 reasoning_content）

```
content: '黄山好玩的地方非常多，主要围绕黄山风景区和周边徽州文化……'
[PASS] 多轮对话正常（端点容错，不强制回传 reasoning_content）
```

**结论**：opencode-go 网关**容错**——历史 assistant 消息不带 `reasoning_content` 不报错。
AIMessage `additional_kwargs` 携带 `reasoning_content` 时也会原样透传（实测正常）。

## 3. 备用通道（官方 DeepSeek API）差异说明

备用端点为官方 DeepSeek API（`https://api.deepseek.com/v1`，模型 `deepseek-chat`）。
未做真实调用（无官方 key），差异按官方文档与社区共识记录：

| 项 | opencode-go（主） | 官方 DeepSeek API（备用） |
|------|------|------|
| `reasoning_content` 剥离 | LangChain 自动剥离，无需处理 | 同左（DeepSeek 兼容 OpenAI 协议） |
| `max_tokens` 字段 | 不识别 `max_completion_tokens` | 同样只识别 `max_tokens` → 工厂 extra_body 方案通用 |
| 多轮回传 `reasoning_content` | **不强制**（网关容错） | **强制要求**：历史 assistant 消息缺 `reasoning_content` 会报错 → 备用通道多轮对话需自定义消息转换（把上轮 reasoning 存入 AIMessage.additional_kwargs 回传）；T2 对话链路（chat_agent）尚未实现该转换，主端点 opencode-go 容错不受影响 |
| 思考 token 统计 | `usage.completion_tokens_details.reasoning_tokens` | 同左 |

## 4. 复现方式

```bash
cd digitalHumanApi
LLM_API_KEY=sk-your-key python -m scripts.smoke_llm          # 主端点（opencode-go）
LLM_FALLBACK_API_KEY=sk-your-key python -m scripts.smoke_llm --fallback  # 备用端点
```

退出码 0 = 全部通过；任一验证失败退出码 1。
单测（不发真实请求，httpx.MockTransport 模拟）：`pytest tests/test_llm.py -v`
