"""Tests for app/services/recommend_service.py

阶段3 T3：推荐问句改为本地 LLM 生成（脱离 Dify）。覆盖：
- 输出解析（think 标签/代码围栏/JSON/别名/去重，兼容旧格式）
- 提示词构造（含聚合 Dashboard 数据）
- 本地 LLM 生成（注入 fake LLM，输出契约不变）
- 失败降级（LLM 异常/未配置 → None，不抛错）
- 无评估数据降级（本地景点/路线数据模板构造推荐问句，不依赖 LLM，非空）
"""
import contextlib

import pytest

from app.services.recommend_service import (
    _aggregate_dashboard_7d,
    _build_fallback_questions,
    _build_recommend_prompt,
    _parse_questions_output,
    fetch_recommended_questions,
)


class TestParseQuestionsOutput:
    """本地 LLM 输出解析（容错：think 标签/代码围栏/JSON/别名/去重）。"""

    def test_valid_json_with_questions(self):
        raw = '{"questions": ["问句1", "问句2"]}'
        assert _parse_questions_output(raw) == ["问句1", "问句2"]

    def test_think_tag_stripping(self):
        raw = "<think>reasoning...</think>\n{\"questions\": [\"问句1\"]}"
        assert _parse_questions_output(raw) == ["问句1"]

    def test_markdown_code_fence(self):
        raw = '```json\n{"questions": ["问句1"]}\n```'
        assert _parse_questions_output(raw) == ["问句1"]

    def test_alias_fallback(self):
        assert _parse_questions_output(
            '{"recommended_questions": ["问句1"]}'
        ) == ["问句1"]
        assert _parse_questions_output('{"suggestions": ["问句1"]}') == [
            "问句1"
        ]

    def test_empty_input_returns_none(self):
        assert _parse_questions_output("") is None
        assert _parse_questions_output(None) is None

    def test_invalid_json_returns_none(self):
        assert _parse_questions_output("not json") is None

    def test_deduplication_and_strip(self):
        raw = '{"questions": ["  问句1  ", "问句1", "问句2", "", "   "]}'
        assert _parse_questions_output(raw) == ["问句1", "问句2"]

    def test_non_list_questions_returns_none(self):
        assert _parse_questions_output('{"questions": "not a list"}') is None

    def test_dict_input_passthrough(self):
        raw = {"questions": ["问句1"]}
        assert _parse_questions_output(raw) == ["问句1"]


def test_build_recommend_prompt_contains_dashboard_data():
    """提示词携带聚合 Dashboard 数据（保证问句与真实数据相关）。"""
    inputs = {
        "concern_tags": '[{"tag": "景点介绍", "count": 25}]',
        "mentioned_spots": '[{"spot": "日光岩", "count": 22}]',
        "sentiment_summary": "positive:45, negative:12, neutral:30",
        "total_dialogues": "87",
    }
    prompt = _build_recommend_prompt(inputs)
    assert "景点介绍" in prompt
    assert "日光岩" in prompt
    assert "positive:45" in prompt
    assert "87" in prompt


@pytest.mark.asyncio
async def test_aggregate_dashboard_7d_empty_db(db_session):
    """_aggregate_dashboard_7d 无数据时返回 None（跳过 LLM 生成）。"""
    assert await _aggregate_dashboard_7d(db_session) is None


class _FakeLLM:
    """可注入的 fake LLM：记录调用，返回固定 content。"""

    def __init__(self, content):
        self._content = content
        self.invoked = False
        self.prompt = None

    def invoke(self, prompt):
        self.invoked = True
        self.prompt = prompt
        return type("FakeResponse", (), {"content": self._content})()


def _human_content(prompt) -> str:
    """从 messages 列表中提取 HumanMessage 的 content（含聚合数据的那条）。"""
    from langchain_core.messages import HumanMessage

    for m in prompt:
        if isinstance(m, HumanMessage):
            return m.content
    return ""


@contextlib.asynccontextmanager
async def _fake_session_factory():
    yield None


@pytest.mark.asyncio
async def test_fetch_recommended_questions_uses_local_llm(monkeypatch):
    """本地 LLM 生成：注入 fake LLM 返回 questions 数组 → 输出契约不变。"""
    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d",
        _agg_with_data,
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )
    fake = _FakeLLM('{"questions": ["日光岩几点去最好看？", "推荐一条亲子路线"]}')

    questions = await fetch_recommended_questions(llm=fake)

    assert fake.invoked
    # 提示词携带聚合数据（HumanMessage content，非 repr）
    assert "日光岩" in _human_content(fake.prompt)
    assert questions == ["日光岩几点去最好看？", "推荐一条亲子路线"]


async def _agg_with_data(db) -> dict:
    return {
        "concern_tags": '[{"tag": "景点介绍", "count": 25}]',
        "mentioned_spots": '[{"spot": "日光岩", "count": 22}]',
        "sentiment_summary": "positive:45, negative:12, neutral:30",
        "total_dialogues": "87",
    }


@pytest.mark.asyncio
async def test_fetch_recommended_questions_full_pipeline(monkeypatch, db_session):
    """完整链路：真实聚合 DB 评估数据 → 本地 LLM → questions 数组（输出契约不变）。"""
    from datetime import datetime, timedelta, timezone

    from app.models.dialogue import Dialogue, DialogueRole
    from app.models.evaluation import Evaluation
    from app.models.visitor import Visitor

    now = datetime.now(timezone.utc)
    db_session.add(Visitor(id="v-recommend-test"))
    await db_session.flush()
    d = Dialogue(
        visitor_id="v-recommend-test",
        content="日光岩几点去最好？",
        role=DialogueRole.user,
        timestamp=now - timedelta(hours=2),
    )
    db_session.add(d)
    await db_session.flush()
    db_session.add(
        Evaluation(
            dialogue_id=d.id,
            concern_tags=["景点介绍", "路线规划"],
            mentioned_spots=["日光岩", "皓月园"],
            sentiment_label="positive",
            satisfaction_score=5,
        )
    )
    await db_session.commit()

    @contextlib.asynccontextmanager
    async def fake_factory():
        yield db_session

    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory", fake_factory
    )
    fake = _FakeLLM('{"questions": ["日光岩几点去最好看？", "皓月园怎么走？"]}')

    questions = await fetch_recommended_questions(llm=fake)

    assert fake.invoked
    # 聚合数据（景点/关注点）进入 LLM 上下文（JSON ensure_ascii=True → 中文转义为 \uXXXX）
    human = _human_content(fake.prompt)
    assert "\\u65e5\\u5149\\u5ca9" in human  # 日光岩
    assert "\\u7693\\u6708\\u56ed" in human  # 皓月园
    assert "positive:1" in human
    assert questions == ["日光岩几点去最好看？", "皓月园怎么走？"]


# 与真实数据源结构一致的样本（测试不依赖真实文件内容）
SAMPLE_SPOTS = [
    {"id": "spot_001", "name": "灵山大佛", "tags": ["历史人文", "祈福"]},
    {"id": "spot_002", "name": "九龙灌浴", "tags": ["历史人文", "演艺"]},
    {"id": "spot_003", "name": "祥符禅寺", "tags": ["历史人文"]},
    {"id": "spot_004", "name": "曼飞龙塔", "tags": ["自然风光"]},
]

SAMPLE_ROUTES = [
    {"id": "route_001", "name": "经典一日游", "tags": ["自然风光", "一日游"]},
    {"id": "route_002", "name": "亲子半日游", "tags": ["亲子", "半日游"]},
    {"id": "route_003", "name": "摄影线", "tags": ["拍照打卡"]},
]


class TestBuildFallbackQuestions:
    """无评估数据降级生成（本地景点/路线数据模板构造，不依赖 LLM）。"""

    def test_with_data_returns_questions(self):
        questions = _build_fallback_questions(SAMPLE_SPOTS, SAMPLE_ROUTES)
        assert questions
        assert len(questions) <= 8
        # 兜底通用问句（优先，保证始终存在）
        assert "有哪些值得去的景点？" in questions
        assert "推荐一条经典游玩路线" in questions
        # 景点问句（热门景点名入问句）
        assert "灵山大佛有什么特色？" in questions
        assert "九龙灌浴有什么特色？" in questions
        assert "祥符禅寺有什么特色？" in questions
        # 路线标签问句（命中标签即产出对应问句；样例候选超上限，仅断言前部命中）
        assert "帮我规划一条半日游路线" in questions
        assert "帮我规划一条一日游路线" in questions
        assert "适合拍照的路线推荐" in questions

    def test_route_tag_questions_all_match(self):
        """路线标签全部命中（候选不超过上限时不截断）。"""
        questions = _build_fallback_questions(
            [{"name": "灵山大佛"}], SAMPLE_ROUTES
        )
        assert "帮我规划一条半日游路线" in questions
        assert "帮我规划一条一日游路线" in questions
        assert "适合拍照的路线推荐" in questions
        assert "带小孩适合走哪条路线？" in questions

    def test_dedup_and_cap(self):
        """去重 + 条数上限截断。"""
        questions = _build_fallback_questions(
            SAMPLE_SPOTS, SAMPLE_ROUTES, max_questions=4
        )
        assert len(questions) == 4
        assert len(set(questions)) == len(questions)

    def test_empty_data_returns_none(self):
        assert _build_fallback_questions([], []) is None

    def test_only_generic_when_no_routes(self):
        """无路线数据时仍有景点问句 + 兜底通用问句（≥3 条）。"""
        questions = _build_fallback_questions(SAMPLE_SPOTS, [])
        assert questions
        assert len(questions) >= 3
        assert "灵山大佛有什么特色？" in questions
        assert "有哪些值得去的景点？" in questions

    def test_missing_files_returns_none(self, monkeypatch):
        """本地数据文件缺失（_load_json 返回空）→ None（保持 None 契约）。"""
        monkeypatch.setattr(
            "app.services.recommend_service._load_json", lambda name: []
        )
        assert _build_fallback_questions() is None

    def test_spot_order_respected(self):
        """景点问句按数据顺序产出（前 _FALLBACK_SPOT_LIMIT 个）。"""
        questions = _build_fallback_questions(
            [{"name": "景点甲"}, {"name": "景点乙"}, {"name": "景点丙"}],
            [],
        )
        assert "景点甲有什么特色？" in questions
        assert questions.index("景点甲有什么特色？") < questions.index(
            "景点乙有什么特色？"
        )
        assert "景点丙有什么特色？" in questions


@pytest.mark.asyncio
async def test_fetch_recommended_questions_no_data_uses_fallback(monkeypatch):
    """无评估数据 → 不调 LLM，降级生成推荐问句（非空，不再返回空态）。"""

    async def _agg_none(db):
        return None

    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d", _agg_none
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )
    # 本地数据缺失路径由 TestBuildFallbackQuestions 专项覆盖；
    # 这里固定降级结果，验证 fetch 走降级分支且不调 LLM
    monkeypatch.setattr(
        "app.services.recommend_service._build_fallback_questions",
        lambda: ["灵山大佛有什么特色？", "帮我规划一条半日游路线"],
    )
    fake = _FakeLLM('{"questions": ["不会用到"]}')

    questions = await fetch_recommended_questions(llm=fake)

    assert questions == ["灵山大佛有什么特色？", "帮我规划一条半日游路线"]
    assert not fake.invoked


@pytest.mark.asyncio
async def test_fetch_recommended_questions_empty_db_fallback_nonempty(monkeypatch):
    """验收场景：空库（无评估数据）+ 真实本地数据文件 → 返回非空降级问句。"""

    async def _agg_none(db):
        return None

    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d", _agg_none
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )

    questions = await fetch_recommended_questions()

    assert questions
    assert len(questions) >= 3


@pytest.mark.asyncio
async def test_fetch_recommended_questions_no_data_fallback_failure_returns_none(
    monkeypatch,
):
    """无评估数据且降级生成失败（本地数据缺失）→ 返回 None（保持 None 契约）。"""

    async def _agg_none(db):
        return None

    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d", _agg_none
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )
    monkeypatch.setattr(
        "app.services.recommend_service._build_fallback_questions",
        lambda: None,
    )

    assert await fetch_recommended_questions() is None


@pytest.mark.asyncio
async def test_fetch_recommended_questions_llm_exception_returns_none(monkeypatch):
    """LLM 调用异常 → 返回 None（不抛错，调用方保留旧缓存）。"""
    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d",
        _agg_with_data,
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )

    class _BoomLLM:
        def invoke(self, prompt):
            raise RuntimeError("LLM down")

    assert await fetch_recommended_questions(llm=_BoomLLM()) is None


@pytest.mark.asyncio
async def test_fetch_recommended_questions_parse_failure_returns_none(monkeypatch):
    """LLM 输出不可解析 → 返回 None（不抛错）。"""
    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d",
        _agg_with_data,
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )
    fake = _FakeLLM("这不是 JSON 输出")

    assert await fetch_recommended_questions(llm=fake) is None


@pytest.mark.asyncio
async def test_fetch_recommended_questions_llmconfig_error_returns_none(monkeypatch):
    """LLM 未配置（LLMConfigError）→ 返回 None（不抛错）。"""
    from app.core.llm import LLMConfigError

    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d",
        _agg_with_data,
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )

    def _raise_config_error(**kwargs):
        raise LLMConfigError("LLM_API_KEY 未配置")

    monkeypatch.setattr(
        "app.services.recommend_service.get_chat_llm", _raise_config_error
    )

    assert await fetch_recommended_questions() is None
