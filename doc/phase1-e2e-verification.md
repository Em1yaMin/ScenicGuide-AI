# 阶段1 双轨联调验证记录（phase1-e2e-verification）

> **历史文档**：本文为阶段1（2026-08-06）验证报告，描述当时实现状态（对话主链路、Embedding 均已有演进——主链路现为 deepagents 装配层、Embedding 已支持 Qwen/DashScope）。最新实现以根目录 `AGENTS.md`（项目记忆）与 `README.md` 为准。

> 日期：2026-08-06 ｜ 阶段1 收官任务：双轨联调验证（LangChain 新链路 vs Dify 回落）+ 问题修复 + 文档完善
> 对应任务：阶段1 最后一项「联调收尾」（T1~T4 已合并：#20/#21/#22/#23）

## 1. 验证环境

| 项 | 值 |
|----|----|
| 本后端 | `digitalHumanApi/`，uvicorn `127.0.0.1:8000`（真实 LLM/Embedding key，来自项目 `.env`） |
| 对话 LLM | `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`（主端点） |
| Embedding | `https://api.minimaxi.com/v1` + `embo-01`（Minimax 原生 API） |
| Dify | `http://111.228.47.45/v1`（Chat Agent 应用，回落目标） |
| 前端 | `digitalHumanVue/`，Vite dev `127.0.0.1:5173` |
| ADH | `live2d-frontend/`，LangChainAgent 引擎 + `scripts/smoke_langchain_agent.py` |
| 浏览器 | 无头 Chromium 150（CDP 直连驱动） |

## 2. 双轨验证结果

### 2.1 新链路（LangChain agent）全流程 ✅

**用例 A：景点查询（工具调用 + RAG 检索）** — `POST /api/v1/dialogue/chat`
- 响应：`conversation_id=69fa6730…`，`rag_sources` 3 条（命中 `灵山胜境_历史文化景点特色与个性化游览指南_知识库版.md`，最高分 0.956）
- 回答按知识库输出核心景点推荐（灵山大佛/梵宫/吉祥颂等），无编造
- 后端日志：`对话完成: engine=langchain user=visitor … tools=5 answer_len=848`

**用例 B：多轮上下文** — 同一 `conversation_id` 追问「梵宫门票/交通」
- `conversation_id` 不变，回答承接上一轮主题（梵宫不单独售票、一票制等）

**用例 C：RAG 检索质量** — `POST /api/rag/search`
- `embedding_backend=minimax`，`degraded=false`，检索「灵山胜境的开放时间」得分 0.958

**用例 D：SSE 流式（步骤可视化 + 逐字回答）** — `streaming=true`
- 事件统计：`step×3`（`search_routes`→`filter_by_tags`→`recommend_routes`）+ `token×540`（回答 859 字符逐字输出）+ `done×1`
- `done` 事件携带 `conversation_id` 供多轮续接

### 2.2 故障回落（LLM 不可用 → 自动切 Dify）✅

模拟方式：`LLM_API_BASE=http://127.0.0.1:59999/v1`（连接拒绝，快速失败）。

**非流式回落** — 对话仍正常返回，回答风格为 Dify 侧输出（灵山胜境 5A、九龙灌浴、梵宫）：
```
[ERROR] app.routers.dialogue: LangChain 对话链路失败，自动回落 Dify (engine=langchain): Connection error.
```

**流式回落** — SSE 事件序列：
```
data: {"type":"error","message":"主链路异常，已自动切换备用服务。","fallback":true}
data: {"type":"step","tool":"fallback","label":"正在连接备用服务…"}
data: {"type":"token","text":"…"}（Dify 逐字输出，46 帧）
data: {"type":"done","conversation_id":"48947535-…"}
```
`fallback=true` 时演示不中断（前端显示「⚠ 主链路异常，已自动切换备用服务」并继续渲染）。

### 2.3 ADH 数字人走本后端 LangChain 链路 ✅

`scripts/smoke_langchain_agent.py`（ADH LangChainAgent 引擎实际路径）：
- 正常链路：**3/3 通过**（后端 SSE 契约 440 chunks、引擎流式 509 chunks、非流式 JSON）
- 故障回落（LLM 不可达）：**3/3 通过** — 引擎日志 `主链路异常已切换备用服务`，随后继续消费 Dify 兜底流式输出（46 chunks），不中断

### 2.4 前端（digitalHumanVue）流式渲染 ✅

无头 Chromium 实测 `/visitor/chat`，输入「适合拍照打卡的景点有哪些？」：
- 步骤可视化：采样序列 `正在查询景点…`（spinner）→ `✓ 正在查询景点…` ×2 → `✓ 正在检索知识库…`（勾选完成态）
- 逐字回答：回答长度 21 → 222 → 547 → 653 字符递增（`streaming=true` 期间带光标），结束 `streaming=false`
- 截图证据（5 帧过程 + final）：`/tmp/sg-e2e-frontend/*.png`（未入仓）

## 3. 联调发现的问题与修复（均有测试/复验）

| # | 问题 | 根因 | 修复 | 验证 |
|---|------|------|------|------|
| 1 | RAG 索引时 Minimax Embedding API 返回 `响应条数不符: 请求 32 条，返回 0 条`，自动降级 TF-IDF | `MinimaxEmbeddings` 按 **OpenAI 兼容格式**发请求（`{"input"}`，解析 `data` 数组）；实际 `embo-01` 走 **Minimax 原生格式**（`{"texts", "type"}`，响应 `vectors`；curl 实测 2013 missing required parameter） | `app/core/embedding.py`：请求改 `texts`+`type`（`embed_documents`→`db`、`embed_query`→`query`），响应解析 `vectors` 为主、兼容 `data` 格式 | `tests/test_embedding.py` 更新通过（18 passed）；真实索引 `embedding_backend=minimax degraded=false`，检索 0.958 |
| 2 | ADH LangChainAgent 流式解析不到回答（冒烟 FAIL，0 chunks） | 引擎按旧契约解析 `event: message/answer`，本后端 T3 实际发 `type: token/step/done` | `live2d-frontend/digitalHuman/agent/core/langchainAgent.py`：改解析 `type` 字段（token/step/done/error），兼容旧形态；`done` 事件取会话 ID | 冒烟复跑 **3/3 通过**（440/509 chunks） |
| 3 | 故障回落时 ADH 引擎抛错中断，丢掉 Dify 兜底输出 | 引擎对任何 `error` 事件都 `raise`；实际 `fallback=true` 时后端**继续**流出 Dify 回答 | 引擎对 `fallback=true` 仅告警并继续消费，`fallback=false` 才抛错 | 冒烟（LLM 不可达）**3/3 通过**，Dify 兜底回答完整输出 |
| 4 | 服务重启后首次 RAG 检索阻塞约 2 分钟（3769 片段全量重向量化） | `SimpleVectorStore._load` 每次启动从 `_docs.json` 重算向量（向量不落盘） | 当时暂不修复（阶段2 优化项）：运行期已缓存、链路不受影响；**已由批次3 C4 向量落盘解决**（sidecar 缓存命中跳过启动重算，见 AGENTS.md「本地 RAG 知识库」） | 二次检索 11s 返回 |

## 4. 复现方法

```bash
# 1. 起后端（真实 key 在 digitalHumanApi/.env）
cd digitalHumanApi && uvicorn app.main:app --port 8000
# 2. 建索引 + 检索
curl -X POST localhost:8000/api/rag/index
curl -X POST localhost:8000/api/rag/search -H 'Content-Type: application/json' -d '{"query":"灵山胜境的开放时间"}'
# 3. 新链路对话（工具调用 + RAG）
curl -X POST localhost:8000/api/v1/dialogue/chat -H 'Content-Type: application/json' \
  -d '{"query":"适合带老人和小孩的轻松路线有哪些？","user":"visitor","streaming":true}'
# 4. 故障回落：把 .env 的 LLM_API_BASE 改成不可达地址后重启，重复第 3 步（观察 error(fallback=true) 与 Dify 输出）
# 5. ADH 冒烟（需 ADH 依赖环境）
cd live2d-frontend && PYTHONPATH=. python scripts/smoke_langchain_agent.py --url http://127.0.0.1:8000
```

## 5. 结论

- 阶段1 双轨（LangChain 主链路 + Dify 兜底）端到端验证通过：新链路全流程 OK、故障自动回落 OK、ADH 接入 OK、前端流式渲染 OK
- 联调暴露 3 个真实缺陷（Embedding API 格式、ADH SSE 契约、回落错误处理）已修复并有测试/复验
- 遗留优化项（启动重向量化耗时）当时记录于 AGENTS.md、不影响演示；已由批次3 C4 向量落盘解决（sidecar 缓存命中跳过启动重算）
