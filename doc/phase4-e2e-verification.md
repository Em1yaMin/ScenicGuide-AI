# 阶段4 收官：脱离 Dify 独立运行全链路自检（phase4-e2e-verification）

> **历史文档**：本文为阶段4（2026-08-07）验证报告；其结论（Dify 依赖面仅剩对话回落 + 历史数据接口，模拟不可达全链路自检通过）至今仍有效，过程细节为当时状态记录。最新实现以根目录 `AGENTS.md`（项目记忆）与 `README.md` 为准。

> 日期：2026-08-07 ｜ 阶段4 收官：Dify 退场最后一项（T1 量化评估本地化 #34 已完成）
> 目的：**验证系统除保留项（对话双轨回落、历史数据接口）外完全不依赖 Dify**——模拟 Dify 不可达后核心链路全部正常，仅保留项有明确降级路径。
> 环境：worktree 自建 `.env`（复制主项目 `digitalHumanApi/.env`，gitignored 不入库；`DIFY_API_URL` 改为 `http://127.0.0.1:9/v1` 模拟 Dify 不可达），uvicorn `127.0.0.1:8010`（主链路实例）+ `127.0.0.1:8011`（回落验证实例）
> 真实调用：opencode-go 端点（`LLM_*` 配置，deepseek-v4-flash）；配额精炼（每链路 1-2 次调用）

## 0. 自检基线

- 代码基线 = `master`（阶段3 收官 #33）+ 阶段4 首项（#34 量化评估本地化，本次合并基线）
- Dify 依赖面（自检前代码确认）：`DIFY_WORKFLOW_API_KEY`/`trigger_workflow`/`dify_helpers.py` 已不存在；`DIFY_API_URL`/`DIFY_API_KEY`/`DIFY_LIGHT4AI_USER` 仅被对话回落 + 历史数据接口引用（`app/core/config.py` 校验器仍要求前两者非空——配置存在但地址指向不可达端口）
- 全量测试基线：**344 passed**（含 #34 新增 `tests/test_evaluation_service.py` 19 用例与 `test_analysis.py` 批量用例）

## 1. 验证矩阵

| # | 场景 | 预期 | 结果 |
|---|------|------|------|
| 1 | 对话快速路径（非流式） | 单轮直答、RAG 注入，不依赖 Dify | ✅ 回答「灵山胜境主要景点」（知识库来源），`rag_sources=3`；耗时 53s 为 opencode-go 端点延迟 |
| 2 | 对话规划路径（非流式） | 意图分类→规划→多步执行→总结 | ✅ 「规划灵山一日游，带老人小孩，喜欢拍照」输出完整行程方案（路线推荐+拍照点位+时间分配），耗时 62s |
| 3 | 对话流式（SSE） | `step/token/done` 事件序列 + RAG 来源透出 | ✅ `search_spots → search_knowledge → search_routes → search_knowledge → token… → done(rag_sources=3)`，耗时 15s |
| 4 | 短期记忆（多轮上下文） | 同一 `conversation_id` 延续上下文 | ✅ 第二轮「那门票多少钱？」延续灵山景区上下文作答（回答针对灵山门票），耗时 7s |
| 5 | 推荐问句本地生成 | 本地 LLM 生成 questions 数组，无 Dify 调用 | ✅ 启动即刷新缓存 **7 条**（日志 `Refreshed 7 recommended questions`）；手动刷新偶发生成失败时保留旧缓存（`stale-while-revalidate`，日志 `keeping 7 cached questions`） |
| 6 | 分析链路（LLM） | `llm_analysis=true`，产出热门问题/建议/高峰 | ✅ overview 返回 `llm_analysis: true`，7 热门问题 + 5 条建议（含票务 FAQ/路线/差评预警等），耗时 109s |
| 7 | 量化评估本地 LLM | 未评估对话批量评估写入 Evaluation 表（含 suggestions） | ✅ 注入 3 条模拟对话（Dify 不可达，模拟轮询已抓取场景）→ `POST /analysis/run-now` 返回 `{"evaluated_count":2}`，Evaluation 表新增 2 条（票务咨询 neutral/4、停车投诉 negative/2，均含 `suggestions`） |
| 8 | 管理后台报告 | 报告含情感/关注点/建议，suggestions 来自本地评估 | ✅ `GET /api/v1/admin/report`（X-Admin-Token）返回 sentiment_stats=3、concern_ranking=10、suggestions=1（「游客反映停车场满位且缺乏引导，建议增加实时车位信息和停车引导措施」） |
| 9 | 长期记忆（写入 + 注入） | 登录用户对话后提取事实写入 user_memory；新会话首轮注入 | ✅ 用户 `2` 注册/登录后对话 → 日志 `长期记忆写入: user=2 written=3`；新会话「帮我推荐一条适合我的路线」→ 回答引用「您之前提到想要轻松一点的路线」 |
| 10 | 游客隔离 | 游客不写长期记忆、不注入 | ✅ 游客（visitor）多轮对话正常；`user_memory` 按 user 分布仅 `{1: 2, 2: 3, mem-user: 2}`，无 visitor |
| 11 | 历史数据接口降级（保留项） | Dify 不可达时轮询明确降级不崩溃 | ✅ `POST /dialogue/sync-now?user=e2e-poll-user` → 日志 `Dify get_conversations ... failed (attempt 1-3/3): All connection attempts failed`，接口返回 `{"status":"ok","synced":0}`（进程不崩溃） |
| 12 | 对话双轨回落降级（保留项） | Dify 不可达时回落路径礼貌报错不崩溃 | ✅ 8011 实例 `DIALOGUE_ENGINE=dify`（强制走 Dify）→ 3 次重试失败 → 返回 `抱歉，AI 服务暂时不可用，请稍后再试。`，服务持续可用 |

## 2. 验证证据（关键响应/日志摘录）

### 2.1 对话本地链路（快速 / 规划 / 流式，Dify 不可达）

```
POST /api/v1/dialogue/chat {"query":"介绍一下灵山景区的主要景点"}
→ answer「灵山胜境 · 主要景点…灵山大佛/九龙灌浴…」rag_sources=3（知识库来源）   # 快速路径

POST /api/v1/dialogue/chat {"query":"规划灵山一日游，带老人小孩，喜欢拍照"}
→ answer「轻松休闲亲子游…儿童乐园/碧波湖/民俗文化村，全程约230分钟…」           # 规划路径

POST /api/v1/dialogue/chat {"query":"碧波湖怎么走","streaming":true}
→ data: {"type":"step","tool":"search_spots",...} × step → token → done(rag_sources=3)  # 流式
```

全程**零 Dify 调用**（日志无任何 Dify 请求；Dify 地址为不可达端口）。

### 2.2 推荐问句本地生成

```
GET /api/v1/visitor/recommended-questions → {"questions": [...7 条...]}
日志：app.tasks.recommend_questions: Refreshed 7 recommended questions
手动 POST /visitor/refresh-recommend 偶发生成 None → 日志：keeping 7 cached questions（旧缓存兜底）
```

### 2.3 分析链路（LLM 产出，与 Dify 无关）

```
GET /api/v1/analysis/overview?days=30
→ llm_analysis: true
→ suggestions: ["完善票务FAQ：…", "优化路线类回答：…", "结构化展示设施服务：…", "建立差评预警机制：…", "…24小时AI值守…"]
```

### 2.4 量化评估本地 LLM（Evaluation 表 + 管理后台报告）

```
注入模拟对话（external_message_id=e2e-*）→ POST /api/v1/analysis/run-now → {"evaluated_count":2}
Evaluation 新增：dialogue 17 → (票务咨询, neutral, 4, suggestions) / dialogue 18 → (投诉建议, negative, 2, suggestions=「游客反映停车场满位且缺乏引导…」)
GET /api/v1/admin/report → sentiment_stats=3, concern_ranking=10, suggestions=1（含上述停车建议）
```

### 2.5 记忆系统（长期写入 + 注入 + 游客隔离）

```
登录用户 user=2 对话（带老人小孩不爬山）→ 日志：长期记忆写入: user=2 written=3 overwritten=0 total=3
  检索 user_memory：「带老人和小孩同行」「老人腿脚不好，不能爬山」…
新会话首轮 → 回答引用「您之前提到想要轻松一点的路线」（记忆注入生效）
游客对话 → user_memory 无 visitor 条目（仅 {1, 2, mem-user}）
```

### 2.6 保留项降级（Dify 不可达的明确降级路径）

```
历史数据接口：POST /dialogue/sync-now?user=e2e-poll-user
  日志：Dify get_conversations for user=e2e-poll-user failed (attempt 1/3): All connection attempts failed; retrying in 0.5s
       …failed after 3 attempts: All connection attempts failed
  响应：{"status":"ok","synced":0}   # 明确降级，进程不崩溃

对话双轨回落：8011 实例 DIALOGUE_ENGINE=dify
  日志：Dify chat failed (attempt 1/3): All connection attempts failed; retrying in 0.5s
  响应：{"answer":"抱歉，AI 服务暂时不可用，请稍后再试。","conversation_id":"…"}   # 礼貌报错，服务持续可用
```

## 3. 发现的问题与观察

### 3.1 无代码缺陷（自检全绿）

Dify 不可达状态下系统核心链路（对话双模式、流式、推荐问句、分析、量化评估、记忆系统、管理后台报告）**全部正常**，未发现需修复的 bug；无代码改动，未新增测试。

### 3.2 运维观察：`.env` 优先于环境变量（既有设计，非缺陷）

自检中 8011 实例首次以 `env DIALOGUE_ENGINE=dify` 启动，实际仍走 langchain 引擎——`app/core/config.py` 的 `load_dotenv(override=True)` 用 `.env` 值覆盖环境变量（配置以 `.env` 为准）。验证回落时需直接改 `.env` 后重启（本自检即采用此法）。属既有设计（部署以 `.env` 为唯一配置来源），已在 `doc/dify配置/README.md` 部署说明隐含；此处记录供运维参考，不构成改动项。

### 3.3 自检数据清理

自检注入的模拟对话/评估/测试用户/记忆已全部清理，DB 恢复自检前状态（dialogues=16、evaluations=16、users=1）；`.env` 已恢复主项目配置（gitignored 不入库）。

## 4. 结论

- **Dify 退场完成**：Dify 不可达时系统核心链路（对话 LangChain 双模式 + 流式 + 多轮记忆、推荐问句本地生成、分析链路 LLM + DB 兜底、量化评估本地 LLM → Evaluation 表 → 管理后台报告、长期/短期/游客隔离记忆）**全部正常**，有证据（§1-2）。
- **保留项降级路径明确**：对话双轨回落与历史数据接口在 Dify 不可达时均明确降级（重试后失败/礼貌报错），不崩溃、不阻断其他链路。
- **文档收官**：`doc/dify-dependency-audit.md` 标记全链路自检完成；`doc/dify配置/` 标注「Dify 已退场（除对话回落/历史接口），配置存档仅供参考」；README 验收状态更新（§阶段4 收官）。
