# 真实环境全链路验证报告（阶段0~2 合并后）

- 日期：2026-08-06
- 分支：`fm/sg-real-e2e`
- 背景：阶段0~阶段2 代码合并（#14~#26），但主项目 `.env` 此前停留在阶段0快照，缺少 `LLM_*`/`DIALOGUE_ENGINE`/`MEMORY_*` 配置，LangChain 新链路从未在真实 key 下运行过。firstmate 已按船长指示补全主 `.env`（LLM/OPENAI 单用 opencode-go，DIALOGUE_ENGINE=langchain，MEMORY_* 已配）。本任务在独立 worktree 内复制主 `.env` 做真实环境全链路验证。
- 环境：Python 3.12.8 / FastAPI 0.141.1 / langchain 1.3.14 / langchain-openai 1.4.1 / uvicorn 0.52.1
- 端点：对话 LLM = `https://opencode.ai/zen/go/v1`（deepseek-v4-flash）；Embedding = `https://api.minimaxi.com/v1`（embo-01）；Dify = `http://111.228.47.45/v1`

## 验证矩阵

| # | 链路 | 验证点 | 结果 | 证据 |
|---|------|--------|------|------|
| 2a | LangChain 对话（非流式） | 真实 LLM 调用成功（非回落）、结构化工具调用、RAG 检索 | ✅ | 见 §1.1 |
| 2a | LangChain 对话（流式 SSE） | step（工具）事件、token 增量、done 事件携带 conversation_id/rag_sources | ✅ | 见 §1.2 |
| 2b | 短期记忆持久化 | 同 conversation_id 多轮上下文 → SQLite 落盘 → 重启后端历史恢复 | ✅ | 见 §2 |
| 2c | 长期记忆 | 登录用户对话产生 user_memory；游客对话不写 | ✅ | 见 §3 |
| 2d | 双轨回落 | LLM_API_BASE 指向不可达地址重启后，自动回落 Dify，非流式 + 流式输出正常 | ✅ | 见 §4 |
| 2e | 运营分析 | OPENAI key 新配置后接口不报错，LLM 分析路径真实走通 | ✅ | 见 §5 |
| — | RAG 索引/检索 | 首次索引 3 文档 → 3769 片段；检索命中 | ✅ | 见 §6（含发现的问题 #1） |

## 1. 2a：LangChain 新链路对话

### 1.1 非流式

`POST /api/v1/dialogue/chat`（query=“景区有哪些推荐路线？”，user=real-e2e-tester，无 conversation_id）

- 响应 `conversation_id=b52cfaf4dfac45c1a92b45f09b2e7d95`（LangChain 生成的进程内 uuid，非 Dify 的 `uuid-...` 形态）
- 回答为完整的 8 条路线分类介绍（796 字符），风格与内容均为 LangChain agent + 本地工具产出
- 服务端日志（证明非回落、工具调用发生）：

```
[INFO] app.services.chat_agent: 新建对话会话: conversation_id=b52cfaf4dfac45c1a92b45f09b2e7d95 user=real-e2e-tester
[INFO] app.services.chat_agent: 对话完成: engine=langchain user=real-e2e-tester conversation=b52cfaf4dfac45c1a92b45f09b2e7d95 tools=4 answer_len=796
```

`tools=4` 表示 4 次结构化工具调用（景点/路线查询），未出现任何回落日志。

### 1.2 流式 SSE

`POST /api/v1/dialogue/chat`（query=“帮我看看翠微峰的介绍”，streaming=true）

事件实录（`data: {json}\n\n` 逐帧）：

```
data: {"type": "step", "tool": "search_spots", "label": "正在查询景点…"}
data: {"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"}
data: {"type": "token", "text": "好的"}
data: {"type": "token", "text": "，"}
data: {"type": "token", "text": "我"}
...（共 277 个 token 事件，回答 452 字符）
data: {"type": "done", "conversation_id": "0a966aabbc3b421c95ae620d23421980",
       "rag_context": "【本地知识库检索结果】\n[1] (景点景区旅游数据行为分析数据_知识库版.md) ...",
       "rag_sources": [{"source": "景点景区旅游数据行为分析数据_知识库版.md", "score": 0.141},
                       {"source": "灵山胜境_景点结构化数据集_知识库版.md", "score": 0.128},
                       {"source": "景点景区旅游数据行为分析数据_知识库版.md", "score": 0.123}]}
```

统计：step 事件（search_spots / search_knowledge 工具）✓，token 事件 277 个 ✓，error 事件 0 个 ✓，done 事件携带 conversation_id + 3 条 RAG 来源 ✓。

## 2. 2b：短期记忆持久化（SQLite + 重启恢复）

1. 第一轮（`conversation_id=memtest-1785994947`，user=mem-user）：“我叫小明，我想带家里老人去玩” → 回复推荐银发康养慢游路线
2. 第二轮（同 id）：“我刚才说我叫什么名字？” → **准确回答“你叫小明”**（多轮上下文生效）
3. SQLite 落盘检查：

```
conversation_sessions: ('memtest-1785994947', 'mem-user', 'client', 907)  -- messages JSON 序列化长度
```

4. **重启后端**后第三轮（同 id）：“重启后你还记得我叫什么吗？” → 回答“在这段对话里我肯定记得你叫小明，因为咱们的聊天记录还在”，并延续路线推荐话题 → **历史从 SQLite 懒恢复，多轮上下文不丢** ✓

## 3. 2c：长期记忆（user_memory）

1. 注册 `reale2e_user`（id=1）→ 登录取 Bearer JWT → 带 `Authorization: Bearer <token>` 对话“我喜欢拍日出照片，也喜欢爬山，帮我推荐一下”
2. 后台提取任务写入 user_memory（`rag_data/user_memory_docs.json`）：

```json
{"user": "1", "fact_type": "preference", "source_conversation_id": "edcfff0079e14bb08b43467ddbc793f7", "timestamp": ...}
  content: 喜欢拍日出照片
{"user": "1", "fact_type": "preference", "source_conversation_id": "edcfff0079e14bb08b43467ddbc793f7", "timestamp": ...}
  content: 喜欢爬山
```

`/api/rag/status`：user_memory 命名空间 document_count=4（含本轮 2 条 + 前期 mem-user 2 条，按 user 隔离）。子集检索“用户喜欢什么”命中 2 条（score 0.564 / 0.396）✓
3. **游客隔离**：以 `user=visitor`（无 token）对话“我特别喜欢拍晚霞” → 对话后记忆条数保持 4 不变 → **游客不写记忆** ✓

## 4. 2d：双轨回落验证

临时将 worktree `.env` 中 `LLM_API_BASE`/`LLM_FALLBACK_API_BASE` 指向 `http://127.0.0.1:9/nonexistent`（不可达），重启 uvicorn。

### 非流式

- 日志：`[ERROR] app.routers.dialogue: LangChain 对话链路失败，自动回落 Dify (engine=langchain): Connection error ... httpx.ConnectError: All connection attempts failed`
- 响应：Dify 兜底回答（“抱歉，我目前的知识库里暂时没有‘翠微峰’的具体介绍信息…建议你到现场后咨询游客中心或工作人员确认一下”），conversation_id 为 Dify 形态 `03159775-88b8-49e7-b...`，RAG 预检索 3 条来源正常注入 ✓

### 流式

```
data: {"type": "error", "message": "主链路异常，已自动切换备用服务。", "fallback": true}
data: {"type": "step", "tool": "fallback", "label": "正在连接备用服务…"}
data: {"type": "token", "text": "..."}   （Dify 兜底流式输出，共 60 个 token）
data: {"type": "done", "conversation_id": "581d1c72-d5ad-46ce-803d-4ce067e822c5", "rag_sources": [...]}
```

error(fallback=true) → step → token → done 序列正确，前端可据此清空部分回答并继续消费 Dify 输出 ✓

## 5. 2e：运营分析（OPENAI key 新配置）

- 空库时 `GET /api/v1/analysis/overview`：正常 200，返回 `ai_summary="最近 30 天内暂无对话数据。"`（纯 DB 统计路径，无 LLM 调用）；`POST /analysis/run-now` → `{"evaluated_count": 0}` ✓
- 插入 2 条测试对话后：LLM 分析路径**真实走通**——`ai_summary` 为 LLM 生成的综合分析（“共分析 2 条对话记录…满意度趋势：平稳。识别到 1 个热门问题、1 个待优化问题”），`hot_questions` 识别出“今天景区人多吗？值得去吗？”，`suggestions` 生成 2 条运营建议 → **OPENAI_*（opencode-go）key 真实可用** ✓（测试数据已清理）

## 6. 发现的问题与修复

### 问题 #1：MiniMax Embedding 账户业务错误被误报为“响应条数不符”（已修复）

- **现象**：`POST /api/rag/index`（首次索引 3769 片段）返回 `degraded=true, embedding_backend=tfidf`，`degraded_reason="Minimax Embedding 响应条数不符: 请求 32 条，返回 0 条"`
- **根因**：MiniMax API 对账户余额不足返回 HTTP 200 + `{"vectors": null, "base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}`。原 `_request` 未解析 `base_resp`，落入“响应条数不符”的误导性错误分支（真实环境实测；账户余额为环境问题，代码修复保证错误信息可诊断）
- **修复**：`app/core/embedding.py` 的 `MinimaxEmbeddings._request` 在解析 `vectors`/`data` 前先检查 `base_resp.status_code`，非 0 时抛出明确 `RuntimeError("Minimax Embedding API 返回业务错误 status_code=1008: insufficient balance")`；降级路径（`SimpleVectorStore` 捕获后切 TF-IDF）保持不变
- **验证**：新增单测 `tests/test_embedding.py::test_minimax_embeddings_base_resp_business_error`（MockTransport 模拟 200 + base_resp 错误，断言抛错信息含 `1008`/`insufficient balance` 且不含“响应条数不符”）；重跑索引后 `degraded_reason="Embedding API 不可用（文档向量化）: Minimax Embedding API 返回业务错误 status_code=1008: insufficient balance"` ✓
- **环境备注**：MiniMax 账户余额不足为环境/账户问题（需在 MiniMax 开放平台充值恢复 API embedding；恢复后 RAG 向量化自动切回 minimax 后端，无需改代码）。降级 TF-IDF 下检索仍可用（§1.2 RAG 命中正常）

## 7. 文档同步

- `README.md`：`OPENAI_*` 行注明当前部署单用 opencode-go 端点/模型 deepseek-v4-flash；`LLM_FALLBACK_*` 行更新为船长决策（备用同主端点 opencode-go，无需另配官方 DeepSeek）
- `digitalHumanApi/.env.example`：同步 LLM_FALLBACK（备用同主端点）与 OPENAI（单用 opencode-go）注释；LLM_*/MEMORY_* 说明此前已齐备，无需新增
- 根 `.env.example`（Docker Compose 部署）：同步 LLM_FALLBACK_* 值与 OPENAI 注释（备用同主端点 opencode-go）

## 附注

- worktree `.env` 为 gitignored 临时验证配置（复制自主 `.env`，含真实 key 但不入库）；文档中 key 一律脱敏
- 验证期间主 `.env` 未做任何修改（firstmate 已维护）
- 测试数据清理：`reale2e_user` 注册用户、analytics 测试对话均已从 worktree 本地库清理；`memory_store.db`/`rag_data/` 为本地运行时产物（gitignored）
