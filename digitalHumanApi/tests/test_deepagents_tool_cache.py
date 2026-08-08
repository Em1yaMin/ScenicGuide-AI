"""工具结果归一化缓存（app/services/deepagents/tool_cache.py）单测。

覆盖：归一化（去空白/标点/大小写）、缓存键构造（工具名 + 位置参数 +
排序 kwargs）、命中/未命中/TTL 过期、知识索引版本失效（仅 search_knowledge）、
条目上限 LRU 淘汰、总开关关闭时原样返回、包装工具行为（同 query 不重查）、
统计计数、invalidate_all。

不依赖真实 LLM/Embedding：直接操作缓存单例与 @tool 构造的本地工具。
"""

import time

import pytest
from langchain_core.tools import tool

from app.core.config import settings
from app.services.deepagents import tool_cache


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """每个测试清空模块级缓存单例 + 固定开关/TTL 配置，防跨测试污染。"""
    tool_cache.get_cache().invalidate_all()
    monkeypatch.setattr(settings, "AGENT_TOOL_CACHE_ENABLED", True)
    yield
    tool_cache.get_cache().invalidate_all()


# ── 归一化 ────────────────────────────────────────────────────────────────


def test_normalize_query_strips_whitespace_punct_and_case():
    """去空白/中英文标点/大小写：不同写法归一为同一键。"""
    assert tool_cache.normalize_query("灵山 景区！") == "灵山景区"
    assert tool_cache.normalize_query("带老人、小孩") == "带老人小孩"
    assert tool_cache.normalize_query("Lingshan Scenic Area,") == "lingshanscenicarea"
    assert tool_cache.normalize_query("  半日游  ") == "半日游"
    assert tool_cache.normalize_query("带老人小孩") == tool_cache.normalize_query(
        "带老人、小孩！"
    )


def test_make_key_includes_tool_name_args_and_sorted_kwargs():
    """缓存键 = (工具名, 归一化位置参数..., 排序后的 kwargs)。"""
    cache = tool_cache.ToolResultCache()
    k1 = cache.make_key("search_spots", ("灵山 景区",), {})
    k2 = cache.make_key("search_spots", ("灵山景区",), {})
    assert k1 == k2
    k3 = cache.make_key("search_knowledge", ("讲解词",), {"top_k": 3})
    k4 = cache.make_key("search_knowledge", ("讲解词",), {"top_k": 3})
    assert k3 == k4
    # kwargs 排序：{b:1, a:2} 与 {a:2, b:1} 同键
    k5 = cache.make_key("t", (), {"b": "1", "a": "2"})
    k6 = cache.make_key("t", (), {"a": "2", "b": "1"})
    assert k5 == k6
    # 工具名隔离：同名参数不同工具不同键
    assert k1 != k3
    # top_k 参与键（不同取值不同缓存）
    assert cache.make_key("search_knowledge", ("讲解词",), {"top_k": 5}) != k3


# ── 读写与 TTL ────────────────────────────────────────────────────────────


def test_get_set_hit_and_miss():
    """写入后同键命中；未写过的键未命中。"""
    cache = tool_cache.ToolResultCache()
    key = cache.make_key("search_spots", ("灵山",), {})
    assert cache.get(key) is None
    cache.set(key, '{"id": "spot_001"}', ttl_seconds=100)
    assert cache.get(key) == '{"id": "spot_001"}'


def test_ttl_expiry_returns_none():
    """过期条目视为未命中（惰性清除）。"""
    cache = tool_cache.ToolResultCache()
    key = cache.make_key("search_routes", ("亲子",), {})
    now = time.time()
    cache.set(key, "value", ttl_seconds=10, now=now)
    assert cache.get(key, now=now + 9) == "value"
    assert cache.get(key, now=now + 11) is None


def test_knowledge_epoch_invalidation():
    """知识索引重建（invalidate_knowledge）后 search_knowledge 立即失效，
    静态数据工具缓存不受影响。"""
    cache = tool_cache.ToolResultCache()
    k_key = cache.make_key("search_knowledge", ("开放时间",), {})
    s_key = cache.make_key("search_spots", ("灵山",), {})
    cache.set(k_key, "k1", ttl_seconds=3600)
    cache.set(s_key, "s1", ttl_seconds=3600)
    cache.invalidate_knowledge()
    assert cache.get(k_key) is None          # 知识缓存已失效
    assert cache.get(s_key) == "s1"          # 静态缓存保留
    # 重建后新写入的知识条目使用新版本号，可正常命中
    cache.set(k_key, "k2", ttl_seconds=3600)
    assert cache.get(k_key) == "k2"


def test_limit_evicts_lru():
    """条目数超限时淘汰最久未命中的条目。"""
    cache = tool_cache.ToolResultCache(limit=3)
    keys = [cache.make_key("search_spots", (f"q{i}",), {}) for i in range(3)]
    for k in keys:
        cache.set(k, f"v{k}", ttl_seconds=3600)
    cache.get(keys[0])  # 命中 keys[0]，移到队尾（最不淘汰）
    cache.set(cache.make_key("search_routes", ("新查询",), {}), "v4", ttl_seconds=3600)
    assert cache.get(keys[0]) is not None      # 命中过的保留
    assert cache.get(keys[1]) is None          # 最早未命中的被淘汰


# ── 包装工具 ──────────────────────────────────────────────────────────────


def _make_tools(calls):
    @tool
    def fake_lookup(query: str) -> str:
        """本地只读查询工具（测试用）。"""
        calls.append(query)
        return f"result:{query}"

    @tool
    def search_knowledge(query: str, top_k: int = 3) -> str:
        """知识检索（测试用）。"""
        calls.append(f"kb:{query}:{top_k}")
        return f"kb_result:{query}"

    return fake_lookup, search_knowledge


def test_wrap_tool_caches_by_normalized_key():
    """同查询不同写法（归一化）只执行一次底层函数；不同查询分别执行。"""
    calls = []
    fake_lookup, _ = _make_tools(calls)
    wrapped = tool_cache.wrap_tool(fake_lookup, ttl_seconds=3600)
    assert wrapped.name == fake_lookup.name
    assert wrapped.description == fake_lookup.description
    r1 = wrapped.invoke({"query": "灵山 景区"})
    r2 = wrapped.invoke({"query": "灵山景区"})
    assert r1 == r2 == "result:灵山 景区"
    assert calls == ["灵山 景区"]  # 第二次命中缓存，底层只执行一次
    wrapped.invoke({"query": "另一处景点"})
    assert len(calls) == 2


def test_wrap_tool_knowledge_ttl_and_invalidation():
    """知识工具：短 TTL 生效；索引重建后立即失效重查。"""
    calls = []
    _, search_knowledge = _make_tools(calls)
    wrapped = tool_cache.wrap_tool(search_knowledge, ttl_seconds=300)
    r1 = wrapped.invoke({"query": "开放时间", "top_k": 3})
    assert r1 == "kb_result:开放时间"
    assert calls == ["kb:开放时间:3"]
    # 同键命中
    wrapped.invoke({"query": "开放时间", "top_k": 3})
    assert len(calls) == 1
    # 知识索引重建 → 缓存失效 → 重查
    tool_cache.get_cache().invalidate_knowledge()
    wrapped.invoke({"query": "开放时间", "top_k": 3})
    assert len(calls) == 2


def test_wrap_tool_disabled_returns_original(monkeypatch):
    """AGENT_TOOL_CACHE_ENABLED=false 时原样返回原工具（零行为变化）。"""
    monkeypatch.setattr(settings, "AGENT_TOOL_CACHE_ENABLED", False)
    calls = []
    fake_lookup, _ = _make_tools(calls)
    wrapped = tool_cache.wrap_tool(fake_lookup, ttl_seconds=3600)
    assert wrapped is fake_lookup


# ── 统计 ──────────────────────────────────────────────────────────────────


def test_stats_counts_hits_and_misses():
    """命中/未命中计数（实测报告观测点）。"""
    cache = tool_cache.ToolResultCache()
    key = cache.make_key("search_spots", ("灵山",), {})
    cache.get(key)          # miss
    cache.get(key)          # miss
    cache.set(key, "v", ttl_seconds=100)
    cache.get(key)          # hit
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["entries"] == 1
