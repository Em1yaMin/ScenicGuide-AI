"""Tests for app/services/recommend_service.py

阶段3 T3：推荐问句改为本地 LLM 生成（脱离 Dify）。覆盖：
- 输出解析（think 标签/代码围栏/JSON/别名/去重，兼容旧格式）
- 提示词构造（含聚合 Dashboard 数据）
- 本地 LLM 生成（注入 fake LLM，输出契约不变）
- 失败降级（LLM 异常/未配置 → None，不抛错）
"""
import contextlib

import pytest

from app.services.recommend_service import (
    _aggregate_dashboard_7d,
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


@pytest.mark.asyncio
async def test_fetch_recommended_questions_no_data_skips_llm(monkeypatch):
    """无评估数据 → 跳过 LLM 调用，返回 None。"""

    async def _agg_none(db):
        return None

    monkeypatch.setattr(
        "app.services.recommend_service._aggregate_dashboard_7d", _agg_none
    )
    monkeypatch.setattr(
        "app.services.recommend_service.async_session_factory",
        _fake_session_factory,
    )
    fake = _FakeLLM('{"questions": ["不会用到"]}')

    assert await fetch_recommended_questions(llm=fake) is None
    assert not fake.invoked


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
