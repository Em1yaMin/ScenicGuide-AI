# Dify 数据迁移盘点报告（阶段 0）

> 日期：2026-08-04 ｜ 任务：fm/sg-data-migration
> 目标：盘点并迁移 Dify 侧历史数据（对话历史 + 知识库内容）到本地系统，为
> 从 Dify 全量迁移到本地 LangChain 提供数据基线。

## 1. 盘点结果

### 1.1 本地侧现状

| 项 | 现状 |
|----|------|
| SQLite 数据库 | 迁移前 `digitalHumanApi/digital_human.db` **不存在**（全新部署，无历史数据） |
| dialogues 表 | 补拉脚本首次运行时自动建表（`Base.metadata.create_all`），当前 0 条 |
| 本地知识源 | `doc/景区知识库/` 三份灵山胜境 md（船长 2026-08-04 提供），本次已纳入 git |

### 1.2 Dify 侧对话历史（API 盘点）

- **API 健康验证**：`GET /v1/conversations?user=xxx`、`/v1/messages`、`/v1/info`、
  `/v1/parameters` 均可达（HTTP 200），应用为「数字人聊天程序」（mode=chat）。
- **数据盘点**：对候选 user 逐一查询 `/conversations`，**全部返回空列表**
  （`has_more=false, data=[]`）：

  | user 候选 | 来源 | 结果 |
  |-----------|------|------|
  | `adh` | ADR-005：light4ai 固定 user（awesome-digital-human-live2d 默认值） | 空 |
  | `light4ai` / `visitor` / `user` / `YOUR_DIFY_USER` / `default` / `ADH` / 空串 / 缺省 | 常见约定值 | 空 |

- **结论**：Dify 侧当前**无可见对话记录**。两种可能：(a) 该 Chat Agent 应用下确实
  没有历史对话（此前 polling「拉不到对话」为已知问题，见 `doc/dify配置/README.md`
  故障排查）；(b) 对话存在于未知 user 下（light4ai 透传的 user 不可枚举——
  Dify API 无「列出全部 user」端点）。需控制台确认（见缺口清单）。
- Dify `/conversations` 响应不含 user 维度枚举能力，且无 cursor 时仅返回单页，
  补拉脚本已实现 `cursor + has_more` 分页以覆盖 >100 条场景（当前无数据，逻辑经
  mock 测试验证）。注意：分页契约未对真实数据端到端验证过，脚本对
  `has_more=true` 但响应缺 cursor/items 的情况会**抛错中止而非静默截断**，
  对单页请求重试耗尽的情况会标记迁移不完整并以非零退出码结束——若部署的 Dify
  版本分页字段不同（部分版本用 `first_id`/`last_id`），首次真实分页时会立即暴露。

### 1.3 Dify 侧知识库（API 盘点）

| 项 | 结果 |
|----|------|
| `GET /v1/datasets` | **401 unauthorized** —— 需 Dataset API Key（datasets 权限），当前仅有应用 key（chat 权限） |
| embedding 模型 | 船长确认 Dify 部署**从未配置 embedding 模型**，知识库检索一直不可用 |
| 知识库内容 | 无法通过 API 读取；源文档约定见 `doc/dify配置/knowledge-base-知识库.md`（10 类文档结构设计，含分段策略） |

**获取途径（不猜测凭据）**：Dify 控制台（`http://111.228.47.45/install`）管理员
登录 →「知识库」页可查看/导出文档；或由管理员在「API 访问」页创建 Dataset Key 后
用 `GET /datasets/{id}/documents` 导出。管理员邮箱/密码为占位符
（`doc/dify访问账户和数字人调用约定格式.md`），需船长提供真实凭据。

### 1.4 本地知识源质量评估（三份 md）

| 文件 | 大小 | 结构 | 质量评估 |
|------|------|------|----------|
| `灵山胜境_历史文化景点特色与个性化游览指南_知识库版.md` | 16.9 KB / 211 行 | `#`/`##`/`###` 层级 + 表格 | 优：段落自包含，票价/时间/路线信息完整，适合问答检索 |
| `灵山胜境_景点结构化数据集_知识库版.md` | 52.9 KB / 291 行 | 24 个 `### LS-XXX` 景点条目，每条约 12 个固定字段 | 优：条目自包含，字段化程度高 |
| `景点景区旅游数据行为分析数据_知识库版.md` | 1.47 MB / 3718 行 | 158 个 `## 景点名` 章节 ×（基本信息/游客画像/景点简介） | 良：章节自包含，但部分「景点简介」段落为**超长单行（最长 2 万+ 字符）**，分块必须按句切分 |

## 2. 迁移内容

### 2.1 对话历史补拉脚本（新增）

- **文件**：`digitalHumanApi/app/tasks/backfill.py`
- **能力**：分页拉取（`cursor + has_more`）、按 `external_message_id` 去重（预查 +
  UNIQUE 兜底）、限流（默认请求间隔 ≥ 1 秒）、指数退避重试（默认 3 次）、幂等可重跑、
  独立 CLI（`python -m app.tasks.backfill [--user xxx]`，自动建表）。
  `/messages` 按 Dify 服务端 API 真实结构（`query`/`answer` 一问一答）映射为
  user/assistant 两条记录；分页契约不匹配时抛错、请求失败时以非零退出码+警告行
  体现迁移不完整。
- **不动主链路**：不修改 `dify_client.py` / `polling.py` / `_call_dify_chat`。
- **测试**：`digitalHumanApi/tests/test_backfill.py`，13 个用例（分页跟随 cursor、
  契约违规抛错、页失败标记不完整、跨会话去重、重跑幂等、role/content 与
  query/answer 两种字段映射、限流间隔、退避重试、headers 透传），全部通过；
  全量测试 94 passed。
- **真实执行结果**：对 `adh` user 补拉，Dify 返回 200 且 0 条对话，本地库保持 0 条
  （与盘点一致；脚本链路端到端验证通过）。

### 2.2 知识库内容（新增）

- `doc/景区知识库/` 三份 md 已纳入 git（随本任务提交）。

### 2.3 未迁移（无源可迁）

- Dify 对话历史：Dify 侧无可见数据，无需迁移。
- Dify 知识库：无 API 访问权，且检索功能从未可用（无 embedding），迁移价值低；
  本地以三份 md 为权威知识源。

## 3. 缺口清单

| # | 缺口 | 影响 | 处理 |
|---|------|------|------|
| G1 | Dify 对话 user 值不可枚举，无法 100% 确认「无对话」而非「user 不符」 | 可能漏迁历史对话 | 控制台「日志与标注」页核对实际 user；或请 light4ai 侧确认固定 user |
| G2 | Dify 知识库内容未导出（无 Dataset Key / 管理员凭据） | 无法核对 Dify 侧文档与三份 md 的差异 | 船长提供控制台凭据或 Dataset Key 后补导出一版存档 |
| G3 | `DIFY_LIGHT4AI_USER` 本地 .env 为空 | 增量 polling 与补拉默认不生效（需显式 `--user`） | 部署时在 `.env` 配置与 ADH `difyAgent.yaml` 的 `DIFY_API_USER` 一致的 user 值 |
| G4 | 三份 md 未建本地 RAG 索引 | 本地检索不可用 | ✅ 已由 sg-kb-index 任务完成（`DEFAULT_DOC_DIR` 限定 `doc/景区知识库/`，`POST /api/rag/index` 全量重建，分块建议见下） |

## 4. 后续建议（供 sg-kb-index 参考）

> ✅ 该任务已完成（fm/sg-kb-index）：知识库 CRUD/导入同步重建 knowledge 命名空间，统一检索入口 `/api/rag/search`（对话 RAG、推荐 Agent、后台共用），详见 AGENTS.md「本地 RAG 知识库」。以下建议保留供后续索引调优参考。

1. **分块策略**（本地 RAG `CHUNK_SIZE=500` 字符默认值需按文件调整）：
   - 指南 md：按 `##` 标题分块即可，块约 300~800 字符，与默认分段兼容。
   - 结构化数据集 md：**按 `### LS-XXX` 景点条目分块**，每个景点一个 chunk
     （条目自带完整字段，检索时自包含）。
   - 行为数据 md：**按 `## 景点名` 章节分块**；章节内「景点简介」为超长单行，
     必须按句子（`。`/换行）二次切分，避免单 chunk 超过 2 万字符。
   - 表格行（票价/时间）建议保留为 markdown 表格原文，供 FAQ 类查询。
2. **索引方式**：本地 `app/core/rag.py`（TF-IDF + numpy）即可承载；若后续要
   语义检索再启用 Minimax embo-01（`.env` 已配置 `EMBEDDING_*`）。
3. **对话侧收尾**：配置 `DIFY_LIGHT4AI_USER` 后，用
   `python -m app.tasks.backfill` 重跑一次（幂等），再启动 polling 增量同步。
4. **知识库维护**：遵循 `doc/dify配置/knowledge-base-知识库.md` 的文档结构约定，
   三份 md 作为 `doc/景区知识库/` 权威源（已纳入 git 版本管理）。

## 5. 命令速查

```bash
# 补拉全部配置用户（需 .env 配置 DIFY_LIGHT4AI_USER）
cd digitalHumanApi && python -m app.tasks.backfill --interval 1.0

# 指定 user 补拉
python -m app.tasks.backfill --user adh --interval 1.0

# 测试
python -m pytest tests/test_backfill.py -v
```
