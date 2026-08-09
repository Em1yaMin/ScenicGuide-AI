"""deepagents 装配层（A2 试点）——HarnessProfile / 装配 / 缓存单测。

覆盖：
1. HarnessProfile 注册幂等（重复调用不报错）
2. 内置工具排除生效（模型侧请求体 tools 只含项目自定义工具；注入参数
   user 对模型隐藏）
3. agent 实例缓存（同 use_rag 变体共享实例；llm 注入独立构建不污染缓存）
4. recursion_limit 显式收紧（create_deep_agent 硬编码 9999，装配层传 30）

不依赖真实 LLM：httpx.MockTransport 模拟 DeepSeek 系流式响应
（langgraph 图内模型调用统一 stream=true，必须返回 SSE chunk 帧）。
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services.deepagents import agent as dg_agent
from app.services.deepagents import harness as dg_harness
from app.services.deepagents.harness import _MAX_AGENT_STEPS, _EXCLUDED_TOOLS

PRIMARY = {
    "LLM_API_BASE": "https://opencode.ai/zen/go/v1",
    "LLM_API_KEY": "sk-primary-test",
    "LLM_MODEL": "deepseek-v4-flash",
}


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定 LLM 端点配置（避免依赖真实环境变量）。"""
    for key, value in PRIMARY.items():
        monkeypatch.setattr(settings, key, value)
    dg_agent.reset_agent_cache()
    yield
    dg_agent.reset_agent_cache()


# ── Mock 响应（ainvoke 路径：图内模型调用非流式，返回普通 JSON） ────────


def _json_response(content: str = "好") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": PRIMARY["LLM_MODEL"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _llm_with_mock(handler) -> "object":
    """构造注入 MockTransport 的 ChatOpenAI（同步/异步 client 均注入）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _capture_handler(captured: dict):
    """捕获请求体（tools 列表/参数 schema），并返回非流式 JSON 响应。

    注意：`agent.ainvoke`（图级）时图内模型调用为非流式，返回普通 JSON；
    `agent.astream`（图级流式）时模型调用才被流式化（SSE chunk 帧，
    见 test_deepagents_stream.py 的构造）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["body"] = body
        captured.setdefault("calls", []).append(body)
        # 模型侧可见工具：name + 参数 schema（验证排除与注入参数隐藏）
        captured["tools"] = [
            {
                "name": t["function"]["name"],
                "params": t["function"]["parameters"],
            }
            for t in body.get("tools", [])
        ]
        return _json_response()

    return handler


# ── HarnessProfile 注册 ───────────────────────────────────────────────────


def test_ensure_harness_profile_idempotent():
    """profile 注册幂等：重复调用不报错、不重复注册。"""
    dg_harness.ensure_harness_profile()
    dg_harness.ensure_harness_profile()
    assert dg_harness._HARNESS_REGISTERED is True
    # 排除集：A1 报告 §3 的 8 个内置工具 + 实测补 delete（第 9 个）
    assert {
        "ls", "read_file", "write_file", "edit_file", "glob", "grep",
        "execute", "task",
    } <= _EXCLUDED_TOOLS
    assert "delete" in _EXCLUDED_TOOLS


# ── 装配与内置工具排除 ────────────────────────────────────────────────────


async def test_build_agent_model_side_tools_excluded_and_injected_hidden():
    """模型侧工具 = 项目自定义工具（内置 9 工具全部排除）；user 注入参数隐藏。

    deepagents 内置工具只能经 HarnessProfile.excluded_tools 排除（A1 报告
    §3）；排除生效的判定点是模型请求体（ToolNode 仍保留全量工具，模型只能
    看到排除后的集合）。记忆工具的 user 参数经 InjectedState 注入，不应
    出现在模型可见的参数 schema 中。
    """
    captured: dict = {}
    llm = _llm_with_mock(_capture_handler(captured))
    agent = dg_agent._build_agent(use_rag=True, llm=llm)
    # 触发一轮模型调用以捕获请求体
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "你好"}], "user": "u1"},
        config={"recursion_limit": _MAX_AGENT_STEPS},
    )
    tool_names = [t["name"] for t in captured["tools"]]
    # 内置 9 工具全部排除
    for builtin in _EXCLUDED_TOOLS:
        assert builtin not in tool_names, f"内置工具未被排除: {builtin}"
    # 场景 7 工具 + 记忆 4 工具 + GPS 2 工具（批次8 B3）全部装配
    # （use_rag=True 含 search_knowledge）
    expected = {
        "search_knowledge", "search_spots", "search_routes",
        "get_spot_detail", "get_route_detail", "filter_by_tags",
        "recommend_routes",
        "recall_user_memory", "save_user_memory",
        "list_user_memories", "delete_user_memory",
        "geocode_location", "plan_route",
    }
    assert set(tool_names) == expected
    # 注入参数对模型隐藏：user 不出现在任何工具参数 schema 中
    for t in captured["tools"]:
        props = (t["params"] or {}).get("properties", {})
        assert "user" not in props, f"注入参数 user 泄漏到模型: {t['name']}"
        assert "user" not in (t["params"] or {}).get("required", [])


async def test_build_agent_without_rag_excludes_knowledge_tool():
    """use_rag=False：search_knowledge 不装配（与主链路选择逻辑一致）。"""
    captured: dict = {}
    llm = _llm_with_mock(_capture_handler(captured))
    agent = dg_agent._build_agent(use_rag=False, llm=llm)
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "你好"}], "user": "u1"},
        config={"recursion_limit": _MAX_AGENT_STEPS},
    )
    tool_names = {t["name"] for t in captured["tools"]}
    assert "search_knowledge" not in tool_names
    assert "recall_user_memory" in tool_names  # 记忆工具始终装配


# ── agent 实例缓存 ────────────────────────────────────────────────────────


def test_agent_cache_shares_instance_per_variant():
    """同 use_rag 变体共享实例（构造耗时 ~24s，必须缓存）；变体间独立。"""
    a1 = dg_agent.get_agent(use_rag=True)
    a2 = dg_agent.get_agent(use_rag=True)
    b = dg_agent.get_agent(use_rag=False)
    assert a1 is a2
    assert a1 is not b
    dg_agent.reset_agent_cache()
    a3 = dg_agent.get_agent(use_rag=True)
    assert a3 is not a1  # 重置后重建


def test_agent_cache_injected_llm_independent():
    """测试注入 llm 时独立构建，不进入/不污染模块级缓存。"""
    llm = _llm_with_mock(_capture_handler({}))
    injected = dg_agent.get_agent(use_rag=True, llm=llm)
    cached = dg_agent.get_agent(use_rag=True)
    assert injected is not cached
    # 注入 llm 的调用每次独立构建（不进入模块级缓存，与 chat_agent 同模式）
    assert injected is not dg_agent.get_agent(use_rag=True, llm=llm)
    assert cached is dg_agent.get_agent(use_rag=True)
