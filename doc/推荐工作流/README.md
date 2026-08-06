# 推荐问句工作流 — Dify 配置

> ⚠️ **已 superseded（阶段3 T3，2026-08-06）**：推荐问句已改**本地 LLM 生成**（`app/services/recommend_service.py`），不再依赖 Dify 工作流。本目录文档仅供历史参考。

> 动态推荐问句功能的 Dify Workflow 提示词设计与配置文档。

---

## 文档清单

| 文件 | 说明 |
|------|------|
| [recommend-workflow-推荐问句工作流.md](./recommend-workflow-推荐问句工作流.md) | **主文档** — 工作流结构、提示词设计、测试用例、部署清单 |

---

## 快速导航

### 我想...

- **了解工作流整体设计** → 主文档 § 二「工作流结构」
- **复制 SYSTEM 提示词到 Dify** → 主文档 § 三.1「SYSTEM 提示词」
- **查看测试用例** → 主文档 § 四「测试用例」
- **部署确认** → 主文档 § 七「部署检查清单」
- **理解后端如何调用** → 主文档 § 六「后端调用方式」

### 相关 Dify 配置

| 组件 | 文档 |
|------|------|
| 聊天助手（Chat Agent） | `../dify配置/chat-agent-提示词.md` |
| 量化评估工作流 | `../dify配置/quantitative-workflow-量化工作流.md` |
| 知识库配置 | `../dify配置/knowledge-base-知识库.md` |
| Dify 总体配置 | `../dify配置/README.md` |

### 相关项目文档

| 文档 | 路径 |
|------|------|
| 动态推荐问句功能需求 | `.rpiv/artifacts/discover/2026-07-16_08-38-07_dynamic-recommended-questions.md` |
| 推荐问句技术研究 | `.rpiv/artifacts/research/2026-07-16_09-21-01_dynamic-recommended-questions-prompt-design.md` |

---

## 提示词设计要点速览

- **角色**: 推荐问句生成专家（非评估者、非对话者）
- **输入**: 4 个变量 — `concern_tags`, `mentioned_spots`, `sentiment_summary`, `total_dialogues`
- **输出**: `{"questions": ["问句1", "问句2", ...]}` — 4-6 条字符串
- **Temperature**: 0.3-0.5（JSON 结构稳定 + 问句语言自然）
- **核心约束**: `visitor-askable` — 每个问句必须是游客能自然说出的口语
- **节点结构**: Start → LLM → End（与量化评估工作流一致）
