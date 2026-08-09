"""高德对话工具（批次8 B3：GPS/高德）——工具行为 + 装配 + SSE 步骤测试。

覆盖：
1. geocode_location 工具：成功（候选 JSON 透出）/ 无 key（amap_not_configured
   错误信封透出，不抛异常）/ 服务层意外异常（防御性包装 amap_unavailable）
2. plan_route 工具：成功（distance/steps JSON）/ 失败（错误码透出）/
   服务层意外异常（防御性包装）
3. 工具装配：deepagents 用户侧工具集（use_rag 开/关）均含
   geocode_location/plan_route；管理员 agent（ADMIN_TOOLS）不含
   （用户侧有、管理员无）；GPS 工具不参与工具结果缓存包装
4. SSE 步骤标签：_STEP_LABELS 含「正在定位…」「正在规划路线…」，
   流式 step 事件按名透出（geocode_location/plan_route）
5. 系统提示词：含 GPS 工具使用原则（先 geocode 后 plan_route 引导）

不依赖真实高德 API：monkeypatch app.services.amap 的模块级函数返回
构造好的信封；流式事件用假 agent（与 test_deepagents_stream 同模式）。
"""

import json

from app.services import amap as amap_service
from app.services.admin_agent.tools import ADMIN_TOOLS
from app.services.deepagents import agent as dg_agent
from app.services.deepagents.amap_tools import AMAP_STEP_LABELS

GEO_OK = {
    "ok": True,
    "data": {
        "address": "黄鹤楼",
        "count": 2,
        "candidates": [
            {
                "location": "114.306967,30.545389",
                "formatted_address": "湖北省武汉市武昌区黄鹤楼",
                "province": "湖北省",
                "city": "武汉市",
                "district": "武昌区",
                "adcode": "420106",
            },
            {
                "location": "103.818,30.410",
                "formatted_address": "四川省成都市新津区黄鹤楼",
                "province": "四川省",
                "city": "成都市",
                "district": "新津区",
                "adcode": "510132",
            },
        ],
    },
}

ROUTE_OK = {
    "ok": True,
    "data": {
        "mode": "walking",
        "origin": "114.306967,30.545389",
        "destination": "114.320,30.560",
        "distance": "1262",
        "duration": "908",
        "steps": [
            {"instruction": "步行向南出发，进入黄鹤楼东路", "distance": "10", "duration": "8", "road": "黄鹤楼东路"}
        ],
        "strategy": "",
    },
}

ERR_NOT_CONFIGURED = {"ok": False, "error": "amap_not_configured", "message": "高德地图服务未配置（AMAP_WEB_KEY 缺失）"}


def _tool_names(tools) -> set:
    return {getattr(t, "name", "") for t in tools}


# ── geocode_location 工具 ─────────────────────────────────────────────────


async def test_geocode_location_success(monkeypatch):
    """成功：候选 JSON 原样透出（模型可据此消歧选择）。"""
    async def _fake_geocode(address, city=None):
        assert address == "黄鹤楼"
        return GEO_OK

    monkeypatch.setattr(amap_service, "geocode", _fake_geocode)
    from app.services.deepagents.amap_tools import geocode_location

    out = json.loads(await geocode_location.ainvoke({"description": "黄鹤楼"}))
    assert out["ok"] is True
    assert out["data"]["count"] == 2
    assert out["data"]["candidates"][0]["city"] == "武汉市"
    assert out["data"]["candidates"][0]["location"] == "114.306967,30.545389"


async def test_geocode_location_city_param_passed(monkeypatch):
    """city 参数透传给服务层（结构化参数消歧）。"""
    seen = {}

    async def _fake_geocode(address, city=None):
        seen["city"] = city
        return GEO_OK

    monkeypatch.setattr(amap_service, "geocode", _fake_geocode)
    from app.services.deepagents.amap_tools import geocode_location

    await geocode_location.ainvoke({"description": "黄鹤楼", "city": "武汉"})
    assert seen["city"] == "武汉"


async def test_geocode_location_no_key_error_code(monkeypatch):
    """无 key：amap_not_configured 错误信封透出，不抛异常。"""
    async def _fake(address, city=None):
        return ERR_NOT_CONFIGURED

    monkeypatch.setattr(amap_service, "geocode", _fake)
    from app.services.deepagents.amap_tools import geocode_location

    out = json.loads(await geocode_location.ainvoke({"description": "黄鹤楼"}))
    assert out == ERR_NOT_CONFIGURED


async def test_geocode_location_service_exception_wrapped(monkeypatch):
    """服务层意外异常：防御性包装为 amap_unavailable（工具不抛裸异常）。"""
    async def _boom(address, city=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(amap_service, "geocode", _boom)
    from app.services.deepagents.amap_tools import geocode_location

    out = json.loads(await geocode_location.ainvoke({"description": "黄鹤楼"}))
    assert out["ok"] is False
    assert out["error"] == "amap_unavailable"


# ── plan_route 工具 ───────────────────────────────────────────────────────


async def test_plan_route_success(monkeypatch):
    """成功：路径 JSON 透出（distance/duration/steps 指令）。"""
    async def _fake_route(origin, destination, mode):
        assert origin == "114.306967,30.545389"
        assert destination == "114.320,30.560"
        assert mode == "walking"
        return ROUTE_OK

    monkeypatch.setattr(amap_service, "route", _fake_route)
    from app.services.deepagents.amap_tools import plan_route

    out = json.loads(
        await plan_route.ainvoke(
            {"origin": "114.306967,30.545389", "destination": "114.320,30.560", "mode": "walking"}
        )
    )
    assert out["ok"] is True
    assert out["data"]["distance"] == "1262"
    assert out["data"]["steps"][0]["instruction"] == "步行向南出发，进入黄鹤楼东路"


async def test_plan_route_error_code_passthrough(monkeypatch):
    """失败：服务层错误信封原样透出（不抛异常、不吞错误码）。"""
    err = {"ok": False, "error": "amap_route_failed", "message": "未找到可行路线"}

    async def _fake(origin, destination, mode="walking"):
        return err

    monkeypatch.setattr(amap_service, "route", _fake)
    from app.services.deepagents.amap_tools import plan_route

    out = json.loads(
        await plan_route.ainvoke({"origin": "114.3,30.5", "destination": "114.32,30.56"})
    )
    assert out == err


async def test_plan_route_service_exception_wrapped(monkeypatch):
    """服务层意外异常：防御性包装为 amap_unavailable。"""
    async def _boom(origin, destination, mode="walking"):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(amap_service, "route", _boom)
    from app.services.deepagents.amap_tools import plan_route

    out = json.loads(
        await plan_route.ainvoke({"origin": "114.3,30.5", "destination": "114.32,30.56"})
    )
    assert out["ok"] is False
    assert out["error"] == "amap_unavailable"


# ── 工具装配（用户侧有、管理员无） ───────────────────────────────────────


def test_user_side_tools_assembled():
    """用户侧工具集（use_rag 开/关）均含 GPS 两工具。"""
    for use_rag in (True, False):
        names = _tool_names(dg_agent._select_tools(use_rag))
        assert "geocode_location" in names
        assert "plan_route" in names


def test_admin_side_tools_not_assembled():
    """管理员 agent 工具集不含 GPS 两工具（位置/路线是游客侧能力）。"""
    names = _tool_names(ADMIN_TOOLS)
    assert "geocode_location" not in names
    assert "plan_route" not in names
    assert "confirm_operation" in names  # 管理工具集本身完整


def test_amap_tools_not_cached():
    """GPS 工具不参与工具结果缓存包装（实时外部数据，同查询结果可能变化）。

    装配后工具对象与 amap_tools 模块原对象同一（未被 wrap_tool 换执行函数）。
    """
    from app.services.deepagents.amap_tools import geocode_location, plan_route

    by_name = {getattr(t, "name", ""): t for t in dg_agent._select_tools(True)}
    assert by_name["geocode_location"] is geocode_location
    assert by_name["plan_route"] is plan_route


# ── SSE 步骤标签与透出 ───────────────────────────────────────────────────


def test_step_labels_registered():
    """_STEP_LABELS 含 GPS 步骤文案（SSE step 事件透出）。"""
    assert AMAP_STEP_LABELS == {
        "geocode_location": "正在定位…",
        "plan_route": "正在规划路线…",
    }
    assert dg_agent._STEP_LABELS["geocode_location"] == "正在定位…"
    assert dg_agent._STEP_LABELS["plan_route"] == "正在规划路线…"


async def test_stream_step_event_for_amap_tools(monkeypatch):
    """流式对话：plan_route 工具调用产出 step 事件（label 正在规划路线…）。"""
    from langchain_core.messages import AIMessageChunk, ToolMessage

    from app.services import chat_agent

    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()

    class _FakeStreamAgent:
        async def astream(self, inputs, stream_mode="messages", config=None):
            yield (
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": "plan_route", "id": "call_1", "args": "", "index": 0}],
                ),
                {},
            )
            yield ToolMessage(
                content=json.dumps(ROUTE_OK, ensure_ascii=False),
                tool_call_id="call_1",
                name="plan_route",
            ), {}
            yield AIMessageChunk(content="步行约 1262 米。"), {}

    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: _FakeStreamAgent())
    events = [e async for e in dg_agent.chat_stream("从黄鹤楼怎么去武昌火车站？", user="u1")]
    steps = [e for e in events if e["type"] == "step"]
    assert len(steps) == 1
    assert steps[0]["tool"] == "plan_route"
    assert steps[0]["label"] == "正在规划路线…"
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "步行约 1262 米" in answer


def test_system_prompt_guides_amap_usage():
    """系统提示词含 GPS 工具使用原则（先 geocode 后 plan_route 引导）。"""
    assert "geocode_location" in dg_agent.SYSTEM_PROMPT
    assert "plan_route" in dg_agent.SYSTEM_PROMPT
    assert "先 geocode_location 再 plan_route" in dg_agent.SYSTEM_PROMPT
    assert "amap_not_configured" in dg_agent.SYSTEM_PROMPT
