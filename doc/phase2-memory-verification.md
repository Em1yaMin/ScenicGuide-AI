# 阶段2 记忆系统登录生效联调验证记录（phase2-memory-verification）

> 日期：2026-08-06 ｜ 阶段2 收官任务（T4：登录生效联调）：登录用户长期记忆全链路
> （写入 + 检索注入）真实环境验证 + 游客降级验证 + 文档收尾
> 对应任务：T1 短期记忆（#25）/ T2 长期记忆写入（#26）/ T3 检索注入（#27）/ 本任务

## 1. 验证环境

| 项 | 值 |
|----|----|
| 本后端 | `digitalHumanApi/`，uvicorn `127.0.0.1:8000`（真实 LLM/Embedding key，来自项目 `.env`，worktree 内 `.env` 为 gitignored 临时配置） |
| 对话 LLM | `https://opencode.ai/zen/go/v1` + `deepseek-v4-flash`（主端点，2026-08-06 船长决策单用） |
| Embedding | `https://api.minimaxi.com/v1` + `embo-01`（Minimax 原生 API） |
| 对话引擎 | `DIALOGUE_ENGINE=langchain`（默认，异常回落 Dify 未触发——全链路正常） |
| 记忆配置 | `MEMORY_ENABLED=true` / `MEMORY_INJECT_ENABLED=true` / `MEMORY_MAX_AGE_DAYS=180`（默认值） |
| 存储 | 短期记忆 `memory_store.db`（SQLite）；长期记忆 `rag_data/user_memory_docs.json`（user_memory 命名空间持久化） |

## 2. 验证矩阵总览

| 链路 | 场景 | 预期 | 结果 |
|------|------|------|------|
| 登录用户 · 写 | 带 JWT 对话，表达偏好/事实 | 对话后异步提取并写入 user_memory（按 user 隔离） | ✅ 写入 5 条（user=1） |
| 登录用户 · 短期 | 同 conversation_id 多轮 | 多轮上下文正常（T1 回归） | ✅ 第二轮回答引用第一轮偏好 |
| 登录用户 · 注入 | 新 conversation_id（同用户）提问 | 首轮检索记忆注入，回答个性化 | ✅ 回答引用「带老人/带小孩/自然风光优先」 |
| 登录用户 · 覆盖去重 | 新会话重申同一偏好 | 同事实覆盖更新，条数不膨胀 | ✅ 重申「喜欢拍照」后仍 5 条 |
| 登录用户 · 流式 | streaming=true 对话 | SSE 正常，记忆链路不受影响 | ✅ step/token/done 事件正常 |
| 游客 · 无 token | 免登录对话（多轮） | 短期记忆正常；不写长期记忆 | ✅ 多轮承接上下文；user_memory 无游客条目 |
| 游客 · 无注入 | 新会话问「还记得我吗」 | 无记忆注入，回答不回忆用户 | ✅ 明确回答「我没有记忆功能」 |
| 游客 · guest token | 游客 token 对话 | 同游客行为（短期有、长期无） | ✅ 同上 |

## 3. 登录用户全链路验证（写入 + 检索注入）

### 3.1 注册 / 登录

```bash
# 注册（真实用户落库）
POST /api/v1/auth/register {"username":"mem_user_1785996440","password":"TestPass123","nickname":"记忆验证用户"}
→ 200 {"id": 1, "username": "mem_user_1785996440", ...}

# 登录 → JWT
POST /api/v1/auth/login {"username":"mem_user_1785996440","password":"TestPass123"}
→ 200 {"token_type": "bearer", "access_token": "eyJhbGciOiJIUzI1NiIs..."}  # 后续请求带 Authorization: Bearer
```

### 3.2 多轮对话（同会话）→ 长期记忆写入

**第 1 轮**（新会话 `conversation_id=""` → 服务端生成 `5373cc7a8771436ca0eacdfe849b94a5`）：
```
query: 我喜欢拍照，尤其喜欢拍风景，这次是带着老人和小孩一起来的
→ 200，回答推荐「轻松休闲亲子游」路线（工具调用 4 次）
后端日志：
  对话完成: engine=langchain user=1 conversation=5373cc7a8771436ca0eacdfe849b94a5 tools=4 answer_len=812
  长期记忆写入: user=1 written=3 overwritten=0 total=3        ← 异步提取钩子
  长期记忆提取并写入: user=1 facts=3
```

**第 2 轮**（同 `conversation_id` 续聊）：
```
query: 我们时间不太多，大概半天，想优先去自然风光好的地方
→ 200，回答开头「结合『半天 + 自然风光优先 + 拍照 + 带老人小孩』这几个需求」
  └ 引用第 1 轮信息 = 短期记忆（同会话多轮上下文）回归确认 ✅
后端日志：
  对话完成: engine=langchain user=1 conversation=5373cc7a... tools=6 answer_len=925
  长期记忆写入: user=1 written=2 overwritten=0 total=5        ← 累计 5 条
```

**user_memory 落库证据**（`rag_data/user_memory_docs.json`，user=1 共 5 条）：
```
[preference] 喜欢拍照                          （source_cid 5373cc7a…）
[preference] 尤其喜欢拍风景
[fact]       这次是带着老人和小孩一起来的
[fact]       时间不太多，大概半天
[preference] 优先去自然风光好的地方
```

### 3.3 新会话（新 conversation_id，同用户）→ 记忆检索注入

```
query: 您还记得我的情况吗？帮我总结一下        （conversation_id="" → 新会话 401ff6ee38a9…）
→ 200，回答：
  「当然记得！😊 让我帮您梳理一下目前了解到的情况：
   👤 您的出游情况总结
     - 带着老人一起 / 带着小孩一起
     - 优先选择自然风光好的地方
   …需要我基于这些情况，帮您推荐几条适合老人小孩、自然风光又好的路线吗？」
后端日志：
  对话完成: engine=langchain user=1 conversation=401ff6ee38a9438e9cadb36e7a47dd56 tools=0 answer_len=332
  （tools=0：未调工具即回答，信息完全来自注入的记忆上下文）
```

**结论：登录用户长期记忆「写入 → 新会话检索注入」全链路真实可用 ✅**

### 3.4 覆盖去重（真实环境）

```
新会话重申：我再重申一下，我喜欢拍照
→ 回答「收到～爱拍照这点我记牢啦！结合您之前提到的带老人和小孩一起…」（注入再次生效）
→ 8 秒后检查 user_memory_docs.json：仍为 5 条（「喜欢拍照」被覆盖更新，不膨胀）✅
```

### 3.5 流式链路（登录用户）

`streaming=true` 时 SSE 事件正常：`token` 逐字输出 → `step`（search_spots 等）→ `done`（携带 conversation_id），与阶段1 T3 契约一致，记忆注入/提取不受影响。

## 4. 游客降级验证（无 token / guest token）

### 4.1 无 token 对话：短期记忆正常、无长期记忆

**第 1 轮**（新会话 `74b69199676c4fad92b0d189dc2209d5`）：
```
query: 我喜欢拍照，带着老人小孩来的
→ 200，回答推荐「轻松休闲亲子游」（该偏好来自本轮问题上下文，非记忆）
```

**第 2 轮**（同会话）：
```
query: 还记得我刚才说的吗？我们想玩半天
→ 200，回答「半天游完全没问题！这三条都是半日游…非常适合老人小孩」
  └ 引用第 1 轮信息 = 游客短期记忆（多轮上下文）正常 ✅
```

### 4.2 无 token 新会话：无记忆注入

```
query: 您还记得我的情况吗？帮我总结一下我的偏好   （新会话 c42f4178…）
→ 200，回答：「我是没有「记忆功能」的，每一次对话对我来说都是全新的开始，
  所以我并不记得您之前的情况和偏好，还请您见谅！」
  └ 与登录用户回答「当然记得！」形成鲜明对比 = 游客无注入 ✅
```

### 4.3 guest token：同游客行为

```
POST /api/v1/auth/guest → 签发 guest token（guest-ba72a0f77f004124a5d05e4f758c8ab2）
带 token 对话「我喜欢拍照，带着老人小孩」→ 正常推荐路线
```

### 4.4 游客不写长期记忆证据

- `rag_data/user_memory_docs.json` 全程仅 `users: ['1']`（5 条），无 visitor / guest-* 条目 ✅
- 后端日志游客轮次仅出现 `对话完成: … user=visitor …`，**无**任何 `长期记忆提取/写入` 日志
  （`_schedule_memory_extraction` 对游客直接跳过，不产生 LLM 调用）✅

## 5. 联调问题与修复

真实环境验证中**未发现新缺陷**（写入/注入/隔离/降级/覆盖去重/流式各环节均符合契约），
本任务无代码改动；改动全部为文档收尾（README / 本验证记录 / AGENTS.md 状态更新）。

## 6. 结论与阶段2 收官自检

| 功能点 | 验收 |
|--------|------|
| 长期记忆写入（登录用户，按 user 隔离） | ✅ 真实环境 5 条落库，来源会话/类型/时间戳正确 |
| 长期记忆检索注入（新会话首轮） | ✅ 回答引用用户画像（带老人小孩/自然风光） |
| 短期记忆（同会话多轮，登录+游客） | ✅ 两轮问答上下文承接（T1 回归） |
| 覆盖去重 / 条数上限 | ✅ 重申事实不膨胀（5 条不变） |
| 游客降级（无 token / guest token） | ✅ 无长期记忆写入、无注入，短期正常 |
| 异常降级 | ✅ 提取失败/检索失败静默降级不阻断（代码路径 + 既有单测覆盖） |
| 测试 | ✅ `pytest` 全量 248 通过（含记忆相关 test_memory / test_memory_inject / test_memory_store 等） |

**阶段2 收官：记忆系统（写/读/注入/隔离/降级）全功能点在真实环境验证可用。**
