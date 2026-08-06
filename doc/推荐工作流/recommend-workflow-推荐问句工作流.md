# 推荐问句工作流 — Dify 配置指南（已归档）

> ⚠️ **已 superseded（阶段3 T3，2026-08-06 船长决策方案B）**：推荐问句已改为**本地 LLM 生成**（`digitalHumanApi/app/services/recommend_service.py`，复用 `LLM_*` 配置 opencode-go），**不再调用 Dify Workflow**，`DIFY_RECOMMEND_WORKFLOW_API_KEY` 已移除。本文档的历史设计与提示词存档以 [`../dify配置/recommend-workflow-推荐工作流.md`](../dify配置/recommend-workflow-推荐工作流.md) 为准（该文档已标记 superseded，作为历史存档保留）。当前实现见 `app/services/recommend_service.py` 与 `AGENTS.md`「推荐问句（阶段3 T3）」专节。
