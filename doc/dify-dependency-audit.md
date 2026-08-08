# Dify 依赖盘点与清理审计（dify-dependency-audit）

> 日期：2026-08-06 ｜ 阶段3 收官 + 阶段4 退场前置（船长决策 2026-08-06 方案B）
> 更新：2026-08-07 ｜ 阶段4 首项落地（船长决策 2026-08-06 方案A）：**Dify Workflow 量化评估已本地化**（见 §2 已完成清理面），剩余 Dify 依赖仅对话双轨回落 + 历史数据接口。
> 收官（2026-08-07）：**全链路自检通过**——模拟 Dify 不可达（`DIFY_API_URL` 指向无效地址）后系统核心链路（对话双模式/流式/记忆、推荐问句、分析、量化评估、管理后台报告）全部正常，仅保留项（对话回落/历史接口）明确降级不崩溃，详见 [`doc/phase4-e2e-verification.md`](doc/phase4-e2e-verification.md)。**Dify 依赖面至此收敛定稿**：除对话双轨回落 + 历史数据接口（船长决策保留）外无任何 Dify 依赖。
> 原则：**对话双轨回落保留**（演示兜底）；**历史数据接口保留**（管理后台数据大屏数据源）；其余尽量清理。
> 本报告为阶段4 最终退场提供依据：剩余依赖清单 + 用途 + 清理建议。

## 1. 盘点结论总览

阶段3 三个并行任务（#30 推荐问句脱离 Dify、#31 分析链路本地化、#32 Agent 双模式）合并后，对 `digitalHumanApi/` 全量 Dify 引用盘点结果如下：

| 分类 | 文件/模块 | 用途 | 处置 |
|------|-----------|------|------|
| **对话双轨回落** | `app/routers/dialogue.py`（`_call_dify_chat`/`_call_dify_chat_stream`） | 对话主链路（deepagents 装配层，A3 起）异常时的 Dify Chat Agent 兜底（演示不翻车） | **保留**（船长决策，必须） |
| **对话双轨回落** | `app/services/dify_stream.py` | 回落链路的 SSE 流式消费 + ThinkFilter 过滤 | **保留**（同上） |
| **历史数据接口** | `app/tasks/polling.py` | 30s 轮询 Dify `/conversations`、`/messages` 拉对话入库（管理后台数据大屏数据源） | **保留**（历史数据接口，如需要） |
| **历史数据接口** | `app/tasks/backfill.py` | 一次性补拉 CLI（历史迁移工具，自带分页节流） | **保留**（历史数据接口） |
| **历史数据接口** | `app/services/dify_client.py`（`get_conversations`/`get_conversation_messages`） | 轮询/补拉的 Dify API 客户端 | **保留**（同上） |
| ~~管理后台评估数据源~~ | ~~`app/tasks/analysis.py`（`analysis_loop`/`batch_evaluate`/`evaluate_dialogue`）~~ | ~~每 300s 调 Dify Workflow 对未评估对话做量化评估~~ | ✅ **已清理（阶段4 首项）**：改本地 LLM 评估（`app/services/evaluation_service.py`，`LLM_*` 配置 opencode-go），Evaluation 表与管理后台消费不变（详见 §2） |
| ~~管理后台评估数据源~~ | ~~`app/services/dify_client.py`（`trigger_workflow`）~~ | ~~Dify Workflow `/workflows/run` 触发~~ | ✅ **已清理（同上）**：方法已移除 |
| ~~管理后台评估数据源~~ | ~~`app/services/dify_helpers.py`（`parse_workflow_output`）~~ | ~~Dify Workflow 输出解析~~ | ✅ **已清理（同上）**：文件已删除（解析逻辑移入 `evaluation_service._parse_evaluation_output`，容错行为对齐） |
| 配置项 | `app/core/config.py` | `DIFY_API_URL`/`DIFY_API_KEY`（对话回落+轮询）、`DIFY_LIGHT4AI_USER`（轮询用户）；~~`DIFY_WORKFLOW_API_KEY`~~（量化评估，已移除） | **保留**（剩余均被上表模块引用） |
| 环境变量 | `docker-compose.yml` | `DIFY_API_URL`/`DIFY_API_KEY`/`DIFY_LIGHT4AI_USER` 透传；~~`DIFY_WORKFLOW_API_KEY`~~（已移除） | **保留**（剩余） |
| 环境变量 | `.github/workflows/ci.yml` | `DIFY_API_URL`/`DIFY_API_KEY` 占位注入（config 校验器要求非空）；~~`DIFY_WORKFLOW_API_KEY`~~（已移除） | **保留**（测试不发真实请求） |
| 环境变量 | `digitalHumanApi/.env.example` + 根 `.env.example` | 部署文档化 | **保留**（注释已同步，见 §3.3） |

## 2. 已完成清理面（阶段3 各 PR）

| 清理项 | 归属 PR | 现状 |
|--------|---------|------|
| 推荐问句 Dify Workflow 调用 + `DIFY_RECOMMEND_WORKFLOW_API_KEY` | #30 | 已清：改本地 LLM 生成（`recommend_service.py`，`get_chat_llm` opencode-go），`.env.example`/`docker-compose.yml` 已移除该变量；`doc/推荐工作流/` 与 `doc/dify配置/recommend-workflow-推荐工作流.md` 已标记 superseded 存档 |
| 运营分析接口 LLM 链路 | #31 | 已清：`analytics_service.py` 不再依赖 Dify，纯本地 LangChain + DB 统计兜底（`llm_analysis` 降级标记） |
| 推荐 Agent 的 `dify_helpers` 依赖 | #30 | 已清：`recommend_service._parse_questions_output` 自带三层防御，不引用 dify_helpers |
| **Dify Workflow 量化评估（阶段4 首项）** | 本次 | **已清**：`evaluate_dialogue` 改本地 LLM 评估（`app/services/evaluation_service.py`，复用 `app/core/llm.py` 工厂 `LLM_*` 配置 opencode-go，prompt 语义对齐原 Workflow）；移除 `dify_client.trigger_workflow`、删除 `dify_helpers.py`、移除 `DIFY_WORKFLOW_API_KEY`（config/校验器/.env.example/docker-compose/CI 同步）；Evaluation 表结构与管理后台消费**不变**；真实调用验证：positive/negative/neutral 分类、满意度 1-5、关注点/景点提取正确（3-7s/条，并发 4，批量 50 约 90s 内完成，300s 循环足够） |

## 3. 剩余依赖明细与阶段4 清理建议

### 3.1 对话双轨回落（阶段4 后仍保留？→ 需决策）

- **现状**：`dialogue.py` 路由层在对话主链路（deepagents 装配层，A3 起）任何异常（含 `LLMConfigError`）时回落 `_call_dify_chat`/`_call_dify_chat_stream`；`DIALOGUE_ENGINE=dify` 可强制走 Dify。
- **阶段4 清理建议**：这是**演示兜底**，船长决策明确保留，**不建议移除**。若阶段4 需彻底退场 Dify，需先确保本地链路（LangChain + 本地工具 + 本地 LLM opencode-go）稳定性达标（含流式/非流式/多轮），并将回落路径改为「本地备选模型」而非 Dify。建议保留至 Dify 服务下线后再评估。

### 3.2 ~~Dify Workflow 量化评估~~（✅ 已清理：阶段4 首项，2026-08-07 船长决策方案A）

- **原现状**：`analysis_loop`（300s）→ `batch_evaluate` → 对未评估对话调 Dify Workflow 量化评估 → Evaluation 表。管理后台 `admin_service.py`（Dashboard 情感趋势/满意度分布/热点问题/景点热度）与 `ReportView` 全部消费 Evaluation 表。
- **已实施（方案A）**：`evaluate_dialogue` 改为本地 LLM 评估——`app/services/evaluation_service.py`（复用 `app/core/llm.py` 工厂，`LLM_*` 配置 opencode-go，prompt 语义移植自原 Workflow 提示词），输出解析 `_parse_evaluation_output`（think 标签/代码围栏/JSON/别名/类型归一/全空判定，含 suggestions 改进建议字段——报告「改进建议」区数据源，1-3 条、无建议为空数组），失败静默降级返回 None（该对话本轮跳过、下轮重试）；`batch_evaluate` 三阶段化（查未评估 → **并发**本地 LLM 评估（`asyncio.gather` + 信号量 4 + `asyncio.to_thread` 非阻塞）→ 串行写 Evaluation 并提交）；Evaluation 表结构与管理后台消费**不变**。
- **依赖移除**：`dify_client.trigger_workflow` 已移除（polling/backfill 用的 `get_conversations`/`get_conversation_messages` 保留）；`dify_helpers.py` 已删除；`DIFY_WORKFLOW_API_KEY` 已从 config（含校验器）/docker-compose/CI/.env.example 移除。
- **测试**：新增 `tests/test_evaluation_service.py`（解析/评估/降级/Evaluation 映射），`tests/test_analysis.py` 新增批量写入与部分失败静默降级用例，`test_analysis_loop_survives_batch_failure` 适配（`batch_evaluate` 符号保留）。
- **真实调用验证**（2026-08-07，opencode-go + deepseek-v4-flash）：三类样本（中性咨询/负面投诉/正面感谢）分类与字段提取正确，单条 3-7s（并发 4 时批量 50 约 90s 内完成，300s 循环周期足够）；端到端 `batch_evaluate` → Evaluation 表写入正常。

### 3.3 配置与环境变量（跟随代码清理）

- 当前 `DIFY_*` 剩余 `DIFY_API_URL`/`DIFY_API_KEY`/`DIFY_LIGHT4AI_USER`，均被保留模块（对话回落 + 历史数据接口）引用，**无需移除**；`DIFY_WORKFLOW_API_KEY` 已随阶段4 首项清理移除。
- `.env.example` 注释已同步（§1 表格 + §2），明确各变量的用途与「对话回落/历史数据接口」归属。
- `DIFY_LIGHT4AI_USER` 为空时轮询 `users=[]` 空转（当前主项目部署即为空，管理后台无新对话数据，属已知部署现状，非代码缺陷）。

### 3.4 非 digitalHumanApi 的 Dify 引用（不在本任务清理范围）

- `live2d-frontend/`（独立 git 仓库）：`DifyAgent`（`configs/agents/difyAgent.yaml`）保留可切换直连 Dify Chat Agent；`config_all_in_dify.yaml` 为历史配置模板。不影响主仓库 Dify 依赖面。
- `doc/dify配置/`：Dify 侧配置存档（chat-agent 提示词、量化工作流、知识库、workflow-spec），**已标注「Dify 已退场（除对话回落/历史接口），配置存档仅供参考」**（README.md 顶部横幅 + 相关文档 superseded 标记，方案：保留标注而非删除——对话回落仍引用聊天助手配置，且存档对复盘/未来彻底下线有价值，见 `doc/dify配置/README.md`）。

## 4. 联调验证与清理交叉结论

- 阶段3 全链路真实环境联调（见 `doc/phase3-e2e-verification.md`）：双模式、推荐问句本地生成、分析链路（LLM + DB 兜底降级标记）、记忆系统（长期/短期/游客隔离）、双轨回落均验证通过。
- 联调暴露并修复：**规划路径偶发空响应**（opencode-go 思考型模型长 prompt 偶发返回空 content）→ `planner_agent.plan_node` 增加 `_PLAN_RETRIES=1` 重试，新增 2 个单测（`test_chat_plan_retries_empty_plan_output` / `test_chat_plan_retries_exhausted_raises`）。
- 阶段4 首项（2026-08-07）：Dify Workflow 量化评估本地化完成（§2 表格），真实调用 + 端到端验证通过。
- 阶段4 收官（2026-08-07）：**脱离 Dify 独立运行全链路自检通过**（`doc/phase4-e2e-verification.md`）——Dify 不可达时：对话（快速/规划/流式/多轮）✅、推荐问句 ✅、分析链路 ✅、量化评估 → Evaluation 表 → 管理后台报告（含 suggestions）✅、记忆系统（长期/短期/游客隔离）✅；仅保留项明确降级：历史数据接口重试后 `synced=0` 不崩溃、对话回落礼貌报错不崩溃。自检未发现代码缺陷，无代码改动。
- Dify 依赖面现状（定稿）：除对话回落与历史数据接口外，**推荐/分析/量化评估均已无 Dify 依赖**；剩余依赖（对话双轨回落、历史数据接口）按船长决策保留（Dify 服务下线后再评估）。
