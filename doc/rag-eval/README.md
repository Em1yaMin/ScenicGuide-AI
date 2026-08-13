# RAG 景区知识问答评测（灵山景区）

船长要求：RAG 景区信息准确率 ≥90%、尽可能高，评测可复跑，报告留存。

**最终成绩：100%（58/58，TF-IDF 离线评测，可复现）**，全量 pytest 783 绿无回归。

## 目录

| 文件 | 内容 |
| --- | --- |
| `eval_set.json` | 评测集（58 题，9 类题型，判定规则见文件 meta） |
| `report_baseline.md` | 基线报告（优化前：46.6%） |
| `report_round1.md` | Round 1 报告（标题感知分块：98.3%） |
| `report_round2_final.md` | Round 2 最终报告（+查询规范化：100%） |
| `result_round2_final.json` | 最终结构化结果（含每题判定/分数，可机读） |

评测引擎：`digitalHumanApi/app/services/rag_eval.py`；CLI：`digitalHumanApi/scripts/rag_eval.py`；pytest 门禁：`digitalHumanApi/tests/test_rag_eval.py`。

## 评测集设计

- 数据源：`doc/景区知识库/` 两份纯灵山文档（结构化数据集 52KB + 历史人文指南 16KB）。
- 58 题覆盖：景点介绍（15）/门票（5）/演出时间（5）/位置（5）/路线（5）/历史典故（5）/注意事项（5）/边界混淆（7）/知识库外拒答（6）。
- 边界混淆用例：相似景点区分（吉祥颂演出地=梵宫 vs 九龙灌浴、曼飞龙塔=南传 vs 五印坛城=藏传、五智门六柱=六度波罗蜜）、灵山胜境 vs 拈花湾门票归属、「华夏第一壁」归属、小灵山与灵鹫山典故。
- 知识库外拒答用例：黄果树瀑布/迪士尼/故宫/西湖/泰山/高铁交通（文档确无交通章节，防编造）。
- 判定规则：正向题=答案关键词（对照文档原文摘取）在 top_k 拼接文本中命中（any/all）；negative 题=检索结果不含库外关键词 且 top1 score 低于拒答阈值（TF-IDF 默认 0.30，API 默认 0.25）。

## 运行方法（离线可复跑）

```bash
# 完整评测 + Markdown 报告（默认 TF-IDF，零外部依赖，约 1s）
cd digitalHumanApi
python -m scripts.rag_eval --out ../doc/rag-eval/report_latest.md
python -m scripts.rag_eval --json-out /tmp/result.json   # 结构化结果

# 参数：--top-k / --threshold / --backend api（部署环境用配置的远端 Embedding 复测）
python -m scripts.rag_eval --top-k 5 --threshold 0.25 --backend api

# pytest 门禁（CI 哨兵）：准确率 ≥90% + 关键题型 ≥90%
python -m pytest tests/test_rag_eval.py -v
```

评测每次从零建索引（独立临时持久化目录），TF-IDF 为确定性算法，结果可精确复现。

## 轮次分数

| 轮次 | 改动 | 准确率 | 说明 |
| --- | --- | --- | --- |
| 基线 | 无（旧按行/段落切分） | 46.6%（27/58） | 3769 chunks 中 56% 为 <50 字符碎片，top1 常命中孤立标题行/「景区名称」行 |
| Round 1a | 标题感知分块（初版） | 96.5% | 标题链前缀重复 bug（栈尾与当前标题重复） |
| Round 1 | 修复标题链重复 | 98.3% | 剩 1 失败（show_004 营业时间） |
| Round 2 | + 查询规范化（时间/营业意图词扩展） | **100%** | show_004 的演艺块从第 9 名进入 top3 |

## 优化内容与失败分析

### Round 1：标题感知分块（`rag_service._split_documents` 重构）

旧策略按段落/句子裸切，结构化文档的字段行（「- 景区名称：灵山胜境」）被切成孤立 chunk，检索「灵山大佛有多高」只命中标题行、答案不在 top_k。新策略：

1. 以 Markdown 标题为边界划分 section，chunk 携带完整标题链前缀（如「子表1：灵山胜境 景点数据集 > LS-011 灵山大佛」），metadata 新增 `title`；
2. 超长 section 按行聚合切分（字段保整，不截断「- 建筑/景观参数：…」行），单行超长再按句子二次切分，子块均带标题前缀；
3. 无标题文档回退旧切分；`_parse_markdown_sections` 用标准标题栈（flush 时取栈全链），避免标题链重复。

效果：碎片 chunk 归零，全部 15 道景点介绍题命中，总准确率 46.6% → 98.3%。

### Round 2：检索前查询规范化（`rag_service._normalize_search_query`）

剩余失败 show_004「拈花湾禅意小镇晚上营业到几点」：TF-IDF 词袋模型下查询词「营业/几点」与演艺块「开放时间 9:00-21:30」词面无交集，演艺块实测排第 9 名。处理：统一检索入口（`_search_documents_sync`，对话 RAG/后台/推荐 Agent 共用）在检索前对时间/营业意图词**追加**同义词（几点→时间、营业/开门/开到→开放、关门→闭园时间；仅追加不替换，目标词已有则跳过）。评测引擎走同一函数，保证评测覆盖生产行为。

### 基线失败分类（31 个，供参考）

- 碎片 chunk 型（~20）：答案行不在 top3（spot/ticket/show/location/tips 多数失败）——Round 1 解决；
- 标题行干扰型：top1 为「### LS-xxx 景点名」纯标题行——Round 1 解决（标题并入 section 前缀）；
- 词袋语义型（1）：营业时间口语问法——Round 2 解决；
- 拒答边界型（2）：库外查询 top1 score 略高于 0.30 阈值（泰山 0.304/高铁 0.435）——Round 1 后分数分布变化自然通过（阈值判定保持不变）。

## 部署注意（重要）

- 本改动改变了 documents 命名空间的 chunk 形态，**生产环境需执行一次 `POST /api/rag/index` 全量重建**，否则旧持久化索引（3769 旧碎片 chunk，sidecar 缓存命中时不会自动重建）仍按旧形态检索；
- `knowledge` 命名空间（管理后台知识库条目）不受影响（`_build_knowledge_documents` 未改动）；
- 远端 Embedding 部署建议用 `--backend api` 复测一次。**拒答阈值校准（2026-08-14 firstmate 用真实 Qwen embedding 复测结论）**：Qwen 语义向量分数整体高于 TF-IDF，阈值 0.25 会把库外查询全部误判为高分命中（negative 0/6）；**阈值 0.75 下 100%（58/58）**，正向题最低分 0.837、拒答题最高分 0.707（分离带清晰，0.72-0.75 区间任取均可）。部署时请用 `--backend api --threshold 0.75` 复测；如后续语料增长，以正向最低分/拒答最高分重新校准；
- 检索 top_k 保持默认 3（RAG_TOP_K），未改动；对话注入上限 RAG_MAX_CONTEXT_LEN=2000 不变。
