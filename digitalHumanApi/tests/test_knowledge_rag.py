"""知识库 CRUD 与 RAG 向量索引同步集成测试。

覆盖：
1. 后台知识库增/删/改/导入后，knowledge 命名空间向量索引同步更新
   （检索结果随 DB 内容变化，验收标准 1）。
2. RAG 文档源限定：默认文档目录为 doc/景区知识库/，不含架构/设计文档
   （验收标准 3）。
3. 推荐 Agent 的 search_knowledge 工具与对话/后台共用统一检索入口
   （验收标准 2）。
"""
import json

import pytest
from httpx import AsyncClient

from app.core.rag import DEFAULT_DOC_DIR, NAMESPACE_KNOWLEDGE
from app.services.rag_service import (
    _load_documents,
    _split_documents,
    index_knowledge_entries,
)
from app.services.recommend_agent import search_knowledge

ADMIN_TOKEN = "test-admin-token"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

# 库外非灵山景区（全国多景区行为分析数据已移出 doc/景区知识库/ 至 doc/archive/），
# 这些内容绝不进入 documents 命名空间检索面
NON_LINGSHAN_SCENIC = ("南禅寺", "鼋头渚", "同里古镇", "周庄", "乌镇", "东湖")


@pytest.fixture(autouse=True)
def _redirect_rag_data_dir(monkeypatch, tmp_path):
    """知识库 CRUD 会触发向量索引同步，重定向持久化目录避免污染工作区。"""
    monkeypatch.setattr("app.core.rag.RAG_DATA_DIR", tmp_path / "rag_data")


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


async def _search_knowledge(client: AsyncClient, query: str) -> list:
    """经 /api/rag/search 按 knowledge 命名空间检索（后台统一检索接口）。"""
    resp = await client.post(
        "/api/rag/search",
        json={"query": query, "namespace": NAMESPACE_KNOWLEDGE},
    )
    assert resp.status_code == 200
    return resp.json()["results"]


# ---------------------------------------------------------------------------
# CRUD 同步向量索引
# ---------------------------------------------------------------------------

async def test_create_knowledge_updates_index(client: AsyncClient):
    """创建知识库条目后，knowledge 命名空间可检索到新内容。"""
    resp = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "灵山大佛讲解词",
            "category": "讲解词",
            "content": "灵山大佛高88米，是世界上最高的青铜佛像之一。",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201

    results = await _search_knowledge(client, "灵山大佛有多高")
    assert results, "创建条目后应能检索到"
    assert results[0]["namespace"] == NAMESPACE_KNOWLEDGE
    assert "灵山大佛" in results[0]["content"]


async def test_update_knowledge_updates_index(client: AsyncClient):
    """更新条目内容后，向量索引同步更新（旧内容不再可检索）。"""
    create_resp = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "灵山大佛讲解词",
            "category": "讲解词",
            "content": "灵山大佛高88米，是世界上最高的青铜佛像之一。",
        },
        headers=ADMIN_HEADERS,
    )
    kb_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/admin/knowledge/{kb_id}",
        json={"content": "灵山大佛是世界著名景点，门票80元。"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200

    # 旧内容已从索引移除
    old_results = await _search_knowledge(client, "88米青铜佛像")
    assert not any("88米" in r["content"] for r in old_results)
    # 新内容可检索
    new_results = await _search_knowledge(client, "灵山大佛门票多少钱")
    assert new_results, "更新后新内容应可检索"
    assert "门票80元" in new_results[0]["content"]


async def test_delete_knowledge_updates_index(client: AsyncClient):
    """删除条目后，向量索引同步移除该条目。"""
    create_resp = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "灵山大佛讲解词",
            "category": "讲解词",
            "content": "灵山大佛高88米，是世界上最高的青铜佛像之一。",
        },
        headers=ADMIN_HEADERS,
    )
    kb_id = create_resp.json()["id"]

    before = await _search_knowledge(client, "灵山大佛")
    assert before, "删除前应可检索到"

    resp = await client.delete(f"/api/admin/knowledge/{kb_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    after = await _search_knowledge(client, "灵山大佛")
    assert after == [], "删除后不应再检索到该条目"


async def test_import_knowledge_updates_index(client: AsyncClient):
    """批量导入后，knowledge 命名空间可检索到导入内容。"""
    content = "# 灵山胜境 FAQ\n\n景区每天 08:00 开放，建议提前预约。\n"
    resp = await client.post(
        "/api/admin/knowledge/import",
        params={"default_category": "FAQ"},
        files={"file": ("faq.md", content, "text/markdown")},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["imported_count"] == 1

    results = await _search_knowledge(client, "景区几点开门")
    assert results, "导入后应可检索到"
    assert "08:00" in results[0]["content"]


# ---------------------------------------------------------------------------
# 文档源限定
# ---------------------------------------------------------------------------

def test_default_doc_dir_limited_to_scenic_knowledge():
    """默认文档目录限定到 doc/景区知识库/ 两份纯灵山文档，不含 doc/ 下架构/设计文档
    与已移出至 doc/archive/ 的多景区行为分析数据（2026-08-11 船长决策方案A）。"""
    assert DEFAULT_DOC_DIR.name == "景区知识库"
    assert DEFAULT_DOC_DIR.exists()

    docs = _load_documents()
    assert docs, "景区知识库目录下应有文档"

    for doc in docs:
        source = doc.metadata["source"]
        # 所有加载文档都位于景区知识库目录内
        assert str(DEFAULT_DOC_DIR) in str(source), f"文档越界: {source}"
        # 架构/设计/部署类文档不进入索引
        assert "architecture" not in source
        assert "plan" not in source
        assert "review" not in source
        assert "report" not in source

    # 仅两份纯灵山景区知识文档纳入索引（多景区行为分析数据已移出）
    filenames = sorted(doc.metadata["filename"] for doc in docs)
    assert filenames == [
        "灵山胜境_历史文化景点特色与个性化游览指南_知识库版.md",
        "灵山胜境_景点结构化数据集_知识库版.md",
    ], f"doc/景区知识库/ 应仅含两份纯灵山文档，实际: {filenames}"

    # 索引内容不含非灵山景区（南禅寺/鼋头渚等库外内容不进入检索面）
    all_content = "\n".join(doc.page_content for doc in docs)
    for name in NON_LINGSHAN_SCENIC:
        assert name not in all_content, f"纯灵山文档不应包含非灵山景区内容: {name}"


def test_doc_chunks_contain_only_lingshan_content():
    """分块后 chunk 不包含非灵山景区内容；纯灵山内容正常索引。"""
    docs = _load_documents()
    assert docs

    chunks = _split_documents(docs)
    assert chunks, "纯灵山文档应切分出 chunk"

    chunk_text = "\n".join(c.page_content for c in chunks)
    for name in NON_LINGSHAN_SCENIC:
        assert name not in chunk_text, f"chunk 不应包含非灵山景区内容: {name}"
    assert "灵山" in chunk_text, "纯灵山内容应正常进入 chunk"


async def test_rag_index_endpoint_indexes_only_lingshan_docs(client: AsyncClient):
    """POST /api/rag/index 全量重建后 documents=2；南禅寺等库外内容不再命中，
    灵山内容正常命中（验收标准：重新索引后 documents=2）。"""
    index_resp = await client.post("/api/rag/index", json={})
    assert index_resp.status_code == 200
    index_data = index_resp.json()
    assert index_data["status"] == "ok"
    assert index_data["documents"] == 2, f"应索引 2 份纯灵山文档，实际: {index_data}"

    # 库外内容（多景区行为分析数据已移出）不再命中
    out_resp = await client.post(
        "/api/rag/search",
        json={"query": "南禅寺", "namespace": "documents"},
    )
    assert out_resp.status_code == 200
    out_results = out_resp.json()["results"]
    for result in out_results:
        for name in NON_LINGSHAN_SCENIC:
            assert name not in result["content"], f"库外内容不应命中: {name}"

    # 灵山内容正常命中
    in_resp = await client.post(
        "/api/rag/search",
        json={"query": "灵山胜境", "namespace": "documents"},
    )
    assert in_resp.status_code == 200
    in_results = in_resp.json()["results"]
    assert in_results, "灵山内容应可检索到"
    assert any("灵山" in r["content"] for r in in_results)


# ---------------------------------------------------------------------------
# 推荐 Agent 共用统一检索入口
# ---------------------------------------------------------------------------

async def test_recommend_agent_search_knowledge_uses_unified_entry():
    """search_knowledge 工具经统一检索入口返回 knowledge 命名空间内容。"""
    await index_knowledge_entries([
        {
            "id": 1,
            "title": "灵山大佛讲解词",
            "category": "讲解词",
            "content": "灵山大佛高88米，是世界上最高的青铜佛像之一。",
        },
    ])

    result = search_knowledge.invoke({"query": "灵山大佛有多高"})
    data = json.loads(result)
    assert data, "工具应返回知识库检索结果"
    assert data[0]["namespace"] == NAMESPACE_KNOWLEDGE
    assert "灵山大佛" in data[0]["content"]
