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
    index_knowledge_entries,
)
from app.services.recommend_agent import search_knowledge

ADMIN_TOKEN = "test-admin-token"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


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
    """默认文档目录限定到 doc/景区知识库/，不含 doc/ 下架构/设计文档。"""
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

    # 三份景区知识文档全部纳入
    filenames = {doc.metadata["filename"] for doc in docs}
    assert len(filenames) >= 3, f"应加载至少 3 份景区知识文档，实际: {filenames}"


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
