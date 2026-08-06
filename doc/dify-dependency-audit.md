# Dify 依赖盘点与清理审计（dify-dependency-audit）

> 日期：2026-08-06 ｜ 阶段3 收官 + 阶段4 退场前置（船长决策 2026-08-06 方案B）
> 原则：**对话双轨回落保留**（演示兜底）；**历史数据接口保留**（管理后台数据大屏数据源）；其余尽量清理。
> 本报告为阶段4 最终退场提供依据：剩余依赖清单 + 用途 + 清理建议。

## 1. 盘点结论总览

阶段3 三个并行任务（#30 推荐问句脱离 Dify、#31 分析链路本地化、#32 Agent 双模式）合并后，对 `digitalHumanApi/` 全量 Dify 引用盘点结果如下：

| 分类 | 文件/模块 | 用途 | 处置 |
|------|-----------|------|------|
| **对话双轨回落** | `app/routers/dialogue.py`（`_call_dify_chat`/`_call_dify_chat_stream`） | LangChain 主链路异常时的 Dify Chat Agent 兜底（演示不翻车） | **保留**（船长决策，必须） |
| **对话双轨回落** | `app/services/dify_stream.py` | 回落链路的 SSE 流式消费 + ThinkFilter 过滤 | **保留**（同上） |
| **历史数据接口** | `app/tasks/polling.py` | 30s 轮询 Dify `/conversations`、`/messages` 拉对话入库（管理后台数据大屏数据源） | **保留**（历史数据接口，如需要） |
| **历史数据接口** | `app/tasks/backfill.py` | 一次性补拉 CLI（历史迁移工具，自带分页节流） | **保留**（历史数据接口） |
| **历史数据接口** | `app/services/dify_client.py`（`get_conversations`/`get_conversation_messages`） | 轮询/补拉的 Dify API 客户端 | **保留**（同上） |
| **管理后台评估数据源** | `app/tasks/analysis.py`（`analysis_loop`/`batch_evaluate`/`evaluate_dialogue`） | 每 300s 调 Dify Workflow 对未评估对话做量化评估（情感/满意度/关注点/景点），写 Evaluation 表 → 管理后台 Dashboard/Report 数据源 | **保留**（评估数据源，见 §3.2 清理建议） |
| **管理后台评估数据源** | `app/services/dify_client.py`（`trigger_workflow`） | Dify Workflow `/workflows/run` 触发 | **保留**（同上） |
| **管理后台评估数据源** | `app/services/dify_helpers.py`（`parse_workflow_output`） | Dify Workflow 输出解析（JSON 容错） | **保留**（同上；推荐问句已剥离该依赖） |
| 配置项 | `app/core/config.py` | `DIFY_API_URL`/`DIFY_API_KEY`（对话回落+轮询）、`DIFY_WORKFLOW_API_KEY`（量化评估）、`DIFY_LIGHT4AI_USER`（轮询用户） | **保留**（均被上表模块引用） |
| 环境变量 | `docker-compose.yml` | 三个 `DIFY_*` 透传 | **保留** |
| 环境变量 | `.github/workflows/ci.yml` | 三个 `DIFY_*` 占位注入（config 校验器要求非空） | **保留**（测试不发真实请求） |
| 环境变量 | `digitalHumanApi/.env.example` + 根 `.env.example` | 部署文档化 | **保留**（注释已同步，见 §3.3） |

## 2. 已完成清理面（阶段3 各 PR）

| 清理项 | 归属 PR | 现状 |
|--------|---------|------|
| 推荐问句 Dify Workflow 调用 + `DIFY_RECOMMEND_WORKFLOW_API_KEY` | #30 | 已清：改本地 LLM 生成（`recommend_service.py`，`get_chat_llm` opencode-go），`.env.example`/`docker-compose.yml` 已移除该变量；`doc/推荐工作流/` 与 `doc/dify配置/recommend-workflow-推荐工作流.md` 已标记 superseded 存档 |
| 运营分析接口 LLM 链路 | #31 | 已清：`analytics_service.py` 不再依赖 Dify，纯本地 LangChain + DB 统计兜底（`llm_analysis` 降级标记） |
| 推荐 Agent 的 `dify_helpers` 依赖 | #30 | 已清：`recommend_service._parse_questions_output` 自带三层防御，不引用 dify_helpers |

## 3. 剩余依赖明细与阶段4 清理建议

### 3.1 对话双轨回落（阶段4 后仍保留？→ 需决策）

- **现状**：`dialogue.py` 路由层在 LangChain 主链路任何异常（含 `LLMConfigError`）时回落 `_call_dify_chat`/`_call_dify_chat_stream`；`DIALOGUE_ENGINE=dify` 可强制走 Dify。
- **阶段4 清理建议**：这是**演示兜底**，船长决策明确保留，**不建议移除**。若阶段4 需彻底退场 Dify，需先确保本地链路（LangChain + 本地工具 + 本地 LLM opencode-go）稳定性达标（含流式/非流式/多轮），并将回落路径改为「本地备选模型」而非 Dify。建议保留至 Dify 服务下线后再评估。

### 3.2 Dify Workflow 量化评估（最大可清理面，但有管理后台依赖）

- **现状**：`analysis_loop`（300s）→ `batch_evaluate` → 对未评估对话调 Dify Workflow 量化评估 → Evaluation 表。管理后台 `admin_service.py`（Dashboard 情感趋势/满意度分布/热点问题/景点热度）与 `ReportView` 全部消费 Evaluation 表。
- **依赖关系**：`DIFY_WORKFLOW_API_KEY` 仅被 `dify_client.trigger_workflow` 使用（轮询链路不需要）。
- **阶段4 清理建议**（按优先级）：
  1. **方案A（推荐，改动最小）**：`evaluate_dialogue` 改为本地 LLM 评估（复用 `OPENAI_*`/`LLM_*` 配置的 opencode-go，prompt 语义对齐原 Workflow），移除 `trigger_workflow`/`dify_helpers`/`DIFY_WORKFLOW_API_KEY`；Evaluation 表结构与管理后台消费不变。
  2. **方案B**：若评估数据非核心诉求，可移除 `analysis_loop` 与 `/run-now`，Evaluation 表仅保留历史数据（管理后台评估类图表变空，需前端空态兜底——Dashboard 已有 `hasData` 空态判断）。
  3. 清理后同步：`config.py` 校验器不再要求 `DIFY_WORKFLOW_API_KEY`；`docker-compose.yml`/CI/`.env.example` 移除该变量。
- **测试影响**：`tests/test_analysis.py` 的 `test_analysis_loop_survives_batch_failure` 依赖 `batch_evaluate` 符号；方案A 改后仅换评估实现，方案B 需删该测试并调整 `routers/analysis.py` 的 `/run-now`。

### 3.3 配置与环境变量（跟随代码清理）

- 当前 4 个 `DIFY_*` 均被保留模块引用，**无需立即移除**。
- `.env.example` 注释已同步（§1 表格 + §2），明确各变量的用途与「对话回落/历史数据接口」归属；阶段4 按 §3.2 清理后同步移除 `DIFY_WORKFLOW_API_KEY`。
- `DIFY_LIGHT4AI_USER` 为空时轮询 `users=[]` 空转（当前主项目部署即为空，管理后台无新对话数据，属已知部署现状，非代码缺陷）。

### 3.4 非 digitalHumanApi 的 Dify 引用（不在本任务清理范围）

- `live2d-frontend/`（独立 git 仓库）：`DifyAgent`（`configs/agents/difyAgent.yaml`）保留可切换直连 Dify Chat Agent；`config_all_in_dify.yaml` 为历史配置模板。不影响主仓库 Dify 依赖面。
- `doc/dify配置/`：Dify 侧配置存档（chat-agent 提示词、量化工作流、知识库、workflow-spec），阶段4 退场后建议归档删除或标注「Dify 已下线」。

## 4. 联调验证与清理交叉结论

- 阶段3 全链路真实环境联调（见 `doc/phase3-e2e-verification.md`）：双模式、推荐问句本地生成、分析链路（LLM + DB 兜底降级标记）、记忆系统（长期/短期/游客隔离）、双轨回落均验证通过。
- 联调暴露并修复：**规划路径偶发空响应**（opencode-go 思考型模型长 prompt 偶发返回空 content）→ `planner_agent.plan_node` 增加 `_PLAN_RETRIES=1` 重试，新增 2 个单测（`test_chat_plan_retries_empty_plan_output` / `test_chat_plan_retries_exhausted_raises`）。
- Dify 依赖面现状：除对话回落与历史数据接口外，**推荐与分析接口已无 Dify 依赖**；剩余最大可清理面为 Dify Workflow 量化评估（§3.2），留待阶段4。
