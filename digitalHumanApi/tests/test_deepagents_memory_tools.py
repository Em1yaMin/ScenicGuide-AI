"""deepagents 记忆工具化（A2 试点）——四工具 user_id 隔离单测。

覆盖：
1. 注入机制：ToolNode 执行时经 InjectedState("user") 从图状态注入当前用户
   （模型看不到注入参数，用户无法经文本指定他人 user）
2. user 隔离：save/recall/list/delete 全部限定当前 user，跨用户不可见
3. 游客（visitor / guest-*）读写全部跳过
4. save 输入解析（JSON 数组 / 自然语言句 / 非法输入）
5. delete 按 id（含 legacy 派生 id 兼容旧条目）；跨用户删除失败

与 test_memory.py 同款隔离 fixture：独立持久化目录 + 清空向量库单例。
"""

import json
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.core import rag
from app.services import memory_service
from app.services.deepagents.memory_tools import (
    delete_user_memory,
    list_user_memories,
    recall_user_memory,
    save_user_memory,
)


class _ToolState(TypedDict):
    """迷你图状态：messages + user（模拟 deepagents 图的状态注入路径）。"""

    messages: Annotated[list, add_messages]
    user: str


@pytest.fixture(autouse=True)
def _isolate_memory_store(tmp_path, monkeypatch):
    """每个测试使用独立的 user_memory 持久化目录（与 test_memory.py 同款）。"""
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path)
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    yield
    with rag._vectorstore_lock:
        rag._vectorstores.clear()


def _tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """构造工具调用消息（图输入）。"""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
        ],
    )


async def _run_tool(tool, args: dict, user: str) -> str:
    """经迷你图执行工具（模拟图内路径：InjectedState 从图状态注入）。

    直接 ToolNode.ainvoke 会因缺 langgraph 运行时注入（__pregel_runtime）报错
    （langgraph 1.2 新机制），故包一层 StateGraph 让图执行自动注入。
    """
    graph = StateGraph(_ToolState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()
    result = await app.ainvoke(
        {"messages": [_tool_call_msg(tool.name, args)], "user": user}
    )
    msg: ToolMessage = result["messages"][-1]
    assert msg.name == tool.name
    return msg.content


def _as_list(content: str):
    return json.loads(content)


# ── 注入机制与 user 隔离 ──────────────────────────────────────────────────


async def test_injected_state_provides_current_user():
    """注入机制：工具经 InjectedState('user') 收到图状态里的当前用户。"""
    content = await _run_tool(
        recall_user_memory, {"query": "偏好"}, user="user_42"
    )
    # 无记忆：返回空消息（user_42 被正确注入，检索未抛错）
    assert "暂无相关记忆" in content


async def test_save_recall_isolated_by_user():
    """save/recall 按 user 隔离：u1 的记忆 u2 检索不到。"""
    await _run_tool(
        save_user_memory,
        {"facts": json.dumps([{"fact_type": "preference", "content": "喜欢拍照"}])},
        user="u1",
    )
    # u1 能检索到
    content = await _run_tool(recall_user_memory, {"query": "拍照"}, user="u1")
    entries = _as_list(content)
    assert len(entries) == 1
    assert entries[0]["content"] == "喜欢拍照"
    assert entries[0]["fact_type"] == "preference"
    assert entries[0]["id"]  # 记忆 id 供 delete 使用
    # u2 检索不到（隔离）
    content2 = await _run_tool(recall_user_memory, {"query": "拍照"}, user="u2")
    assert "暂无相关记忆" in content2


async def test_list_isolated_by_user():
    """list 按 user 隔离：只返回当前用户的条目。"""
    await _run_tool(
        save_user_memory,
        {"facts": json.dumps([{"fact_type": "fact", "content": "带老人小孩"}])},
        user="u1",
    )
    await _run_tool(
        save_user_memory,
        {"facts": json.dumps([{"fact_type": "habit", "content": "喜欢早起"}])},
        user="u2",
    )
    list_u1 = _as_list(await _run_tool(list_user_memories, {}, user="u1"))
    list_u2 = _as_list(await _run_tool(list_user_memories, {}, user="u2"))
    assert [e["content"] for e in list_u1] == ["带老人小孩"]
    assert [e["content"] for e in list_u2] == ["喜欢早起"]


async def test_delete_by_id_isolated_by_user():
    """delete 按 id + user 匹配：仅能删除自己的记忆，跨用户 id 删除失败。"""
    await _run_tool(
        save_user_memory,
        {"facts": json.dumps([{"fact_type": "preference", "content": "喜欢拍照"}])},
        user="u1",
    )
    entries = _as_list(await _run_tool(list_user_memories, {}, user="u1"))
    memory_id = entries[0]["id"]
    assert memory_id
    # 跨用户删除失败（隔离）
    res = _as_list(await _run_tool(delete_user_memory, {"memory_id": memory_id}, user="u2"))
    assert res["deleted"] == 0
    # 本用户删除成功
    res = _as_list(await _run_tool(delete_user_memory, {"memory_id": memory_id}, user="u1"))
    assert res["deleted"] == 1
    assert "暂无记忆" in await _run_tool(list_user_memories, {}, user="u1")
    # 重复删除返回 0
    res = _as_list(await _run_tool(delete_user_memory, {"memory_id": memory_id}, user="u1"))
    assert res["deleted"] == 0


async def test_delete_legacy_memory_without_memory_id():
    """兼容旧条目（升级前写入、无 memory_id）：派生 id 可正常删除。"""
    # 直接经 memory_service 写入（_entry_to_document 现在会生成 memory_id，
    # 这里模拟旧条目：手工构造无 memory_id 的 Document 写入存储）
    store = memory_service._memory_store()
    from langchain_core.documents import Document

    with memory_service._vector_index_lock:
        store.add_documents(
            [
                Document(
                    page_content="旧条目记忆",
                    metadata={
                        "user": "u1",
                        "fact_type": "fact",
                        "source_conversation_id": "",
                        "timestamp": 1000.0,
                    },
                )
            ]
        )
    entries = _as_list(await _run_tool(list_user_memories, {}, user="u1"))
    assert len(entries) == 1
    assert entries[0]["id"].startswith("legacy:")  # 派生稳定 id
    res = _as_list(
        await _run_tool(delete_user_memory, {"memory_id": entries[0]["id"]}, user="u1")
    )
    assert res["deleted"] == 1
    assert "暂无记忆" in await _run_tool(list_user_memories, {}, user="u1")


# ── 游客跳过 ──────────────────────────────────────────────────────────────


async def test_guest_user_skipped_for_all_tools():
    """游客（visitor / guest-*）读写全部跳过：不落库、查不到、删不掉。"""
    for guest in ("visitor", "guest-abc"):
        res = _as_list(
            await _run_tool(
                save_user_memory,
                {"facts": json.dumps([{"fact_type": "preference", "content": "喜欢拍照"}])},
                user=guest,
            )
        )
        # 游客 save：返回提示消息（底层 skipped=True 不落库）
        assert "游客用户不保存长期记忆" in res.get("message", "")
        assert "暂无相关记忆" in await _run_tool(
            recall_user_memory, {"query": "拍照"}, user=guest
        )
        assert "暂无记忆" in await _run_tool(list_user_memories, {}, user=guest)
        res = _as_list(
            await _run_tool(delete_user_memory, {"memory_id": "whatever"}, user=guest)
        )
        assert res["deleted"] == 0
    # 游客写入未污染登录用户
    assert await memory_service.count_user_memory("u1") == 0


# ── save 输入解析 ─────────────────────────────────────────────────────────


async def test_save_parses_natural_language_fact():
    """save 输入为自然语言句时按单条 fact 保存。"""
    await _run_tool(save_user_memory, {"facts": "喜欢爬山"}, user="u1")
    entries = _as_list(await _run_tool(recall_user_memory, {"query": "爬山"}, user="u1"))
    assert entries[0]["content"] == "喜欢爬山"
    assert entries[0]["fact_type"] == "fact"


async def test_save_rejects_invalid_input():
    """save 非法输入返回错误消息，不写入。"""
    for bad in ("", "   ", "not-a-json-or-sentence[", "[]"):
        content = await _run_tool(save_user_memory, {"facts": bad}, user="u1")
        assert "无法解析" in content or "没有可保存" in content
    assert await memory_service.count_user_memory("u1") == 0


async def test_recall_requires_query():
    """recall 空关键词返回提示，不触发检索异常。"""
    content = await _run_tool(recall_user_memory, {"query": "  "}, user="u1")
    assert "请提供检索关键词" in content


async def test_save_overwrite_not_grow():
    """同内容重复保存覆盖更新（记忆不膨胀，与 memory_service 契约一致）。"""
    facts = json.dumps([{"fact_type": "preference", "content": "喜欢拍照"}])
    await _run_tool(save_user_memory, {"facts": facts}, user="u1")
    await _run_tool(save_user_memory, {"facts": facts}, user="u1")
    entries = _as_list(await _run_tool(list_user_memories, {}, user="u1"))
    assert len(entries) == 1


async def test_save_truncates_oversized_content():
    """超长记忆句（>200 字符）被截断：与记忆 schema 契约与提取路径一致。

    平行提取路径（memory_service._parse_facts）截断到 _MEMORY_MAX_CONTENT_LEN
    （200 字符）；save_user_memory 工具经 _parse_facts_input 写入同样受限，
    防模型经工具绕过上限写入超长句（review F1）。JSON 数组与自然语言句
    两个分支均覆盖。
    """
    from app.services.deepagents.memory_tools import _CONTENT_MAX_LEN

    long_content = "超长" * 300  # 600 字符
    # JSON 数组分支
    await _run_tool(
        save_user_memory,
        {"facts": json.dumps([{"fact_type": "fact", "content": long_content}])},
        user="u1",
    )
    entries = _as_list(await _run_tool(list_user_memories, {}, user="u1"))
    assert len(entries) == 1
    assert len(entries[0]["content"]) == _CONTENT_MAX_LEN
    assert entries[0]["content"].endswith("长")  # 截断取前 200 字符（偶数位）
    # 自然语言句分支
    await _run_tool(save_user_memory, {"facts": long_content}, user="u2")
    entries2 = _as_list(await _run_tool(list_user_memories, {}, user="u2"))
    assert len(entries2[0]["content"]) == _CONTENT_MAX_LEN
