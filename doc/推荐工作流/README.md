# 推荐问句工作流 — Dify 配置

> ⚠️ **已 superseded（阶段3 T3，2026-08-06）**：推荐问句已改**本地 LLM 生成**（`app/services/recommend_service.py`），不再依赖 Dify 工作流。本目录文档仅供历史参考。

> 动态推荐问句功能的 Dify Workflow 提示词设计与配置文档。

---

## 文档清单

| 文件 | 说明 |
|------|------|
| [recommend-workflow-推荐问句工作流.md](./recommend-workflow-推荐问句工作流.md) | 历史主文档（已缩减为指向存档的指针；历史设计与提示词存档见 [`../dify配置/recommend-workflow-推荐工作流.md`](../dify配置/recommend-workflow-推荐工作流.md)） |

---

## 快速导航

- **历史设计与提示词存档** → [`../dify配置/recommend-workflow-推荐工作流.md`](../dify配置/recommend-workflow-推荐工作流.md)（已标记 superseded，含工作流结构、提示词、测试用例、部署清单）

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
