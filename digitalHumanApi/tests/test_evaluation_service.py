"""Tests for app/services/evaluation_service.py

阶段4（2026-08-06 船长决策方案A）：Dify Workflow 量化评估本地化。覆盖：
- 输出解析（think 标签/代码围栏/JSON/别名/类型归一，兼容旧 Dify 输出格式）
- 本地 LLM 评估（注入 fake LLM，输出契约不变）
- 失败降级（LLM 异常/未配置/解析失败 → None，不抛错）
- 与 tasks/analysis.py 的 evaluate_dialogue 集成（Evaluation 表写入）
"""
import pytest

from app.models.evaluation import Evaluation
from app.services.evaluation_service import (
    _parse_evaluation_output,
    evaluate_content,
)
from app.tasks.analysis import evaluate_dialogue


class TestParseEvaluationOutput:
    """本地 LLM 输出解析（容错：think 标签/代码围栏/JSON/别名/类型归一）。"""

    def test_valid_json(self):
        raw = (
            '{"sentiment_label": "positive", "satisfaction_score": 5,'
            ' "concern_tags": ["景点介绍"], "mentioned_spots": ["日光岩"],'
            ' "suggestions": ["游客询问位置时无方位描述，建议介绍中增加方位信息"]}'
        )
        parsed = _parse_evaluation_output(raw)
        assert parsed["sentiment_label"] == "positive"
        assert parsed["satisfaction_score"] == 5
        assert parsed["concern_tags"] == ["景点介绍"]
        assert parsed["mentioned_spots"] == ["日光岩"]
        assert parsed["suggestions"] == ["游客询问位置时无方位描述，建议介绍中增加方位信息"]

    def test_think_tag_stripping(self):
        raw = (
            "<think>游客很开心</think>\n"
            '{"sentiment_label": "positive", "satisfaction_score": 5,'
            ' "concern_tags": [], "mentioned_spots": []}'
        )
        parsed = _parse_evaluation_output(raw)
        assert parsed["sentiment_label"] == "positive"
        assert parsed["satisfaction_score"] == 5

    def test_markdown_code_fence(self):
        raw = (
            '```json\n{"sentiment_label": "neutral", "satisfaction_score": 3,'
            ' "concern_tags": ["票务咨询"], "mentioned_spots": []}\n```'
        )
        parsed = _parse_evaluation_output(raw)
        assert parsed["sentiment_label"] == "neutral"
        assert parsed["concern_tags"] == ["票务咨询"]

    def test_alias_fallback(self):
        """旧 Dify Workflow 字段别名（sentiment/concerns/spots）仍可识别。"""
        raw = (
            '{"sentiment": "negative", "satisfaction_score": "2",'
            ' "concerns": ["投诉建议"], "spots": ["皓月园"]}'
        )
        parsed = _parse_evaluation_output(raw)
        assert parsed["sentiment_label"] == "negative"
        assert parsed["satisfaction_score"] == 2  # 字符串数字转 int
        assert parsed["concern_tags"] == ["投诉建议"]
        assert parsed["mentioned_spots"] == ["皓月园"]

    def test_sentiment_alias_normalized(self):
        """happy/confused 等旧 Workflow 取值归一为三分类。"""
        assert _parse_evaluation_output(
            '{"sentiment_label": "happy", "satisfaction_score": 5}'
        )["sentiment_label"] == "positive"
        assert _parse_evaluation_output(
            '{"sentiment_label": "confused", "satisfaction_score": 3}'
        )["sentiment_label"] == "neutral"

    def test_score_clamped_to_range(self):
        """越界分数钳制到 1-5（LLM 偶发 0/6）；分数不可解析且无其他字段 → None。"""
        assert _parse_evaluation_output(
            '{"satisfaction_score": 0}'
        )["satisfaction_score"] == 1
        assert _parse_evaluation_output(
            '{"satisfaction_score": 6}'
        )["satisfaction_score"] == 5
        # 分数不可解析 + 无其他字段 → 全空判定返回 None
        assert _parse_evaluation_output('{"satisfaction_score": "abc"}') is None
        # 分数不可解析但有关注点 → 保留有效字段
        parsed = _parse_evaluation_output(
            '{"satisfaction_score": "abc", "concern_tags": ["票务咨询"]}'
        )
        assert parsed is not None
        assert parsed["satisfaction_score"] is None
        assert parsed["concern_tags"] == ["票务咨询"]

    def test_empty_input_returns_none(self):
        assert _parse_evaluation_output("") is None
        assert _parse_evaluation_output(None) is None

    def test_invalid_json_returns_none(self):
        assert _parse_evaluation_output("not json") is None

    def test_dict_input_passthrough(self):
        raw = {"sentiment_label": "neutral", "satisfaction_score": 4}
        parsed = _parse_evaluation_output(raw)
        assert parsed["sentiment_label"] == "neutral"
        assert parsed["satisfaction_score"] == 4

    def test_non_dict_json_returns_none(self):
        assert _parse_evaluation_output('["not", "a", "dict"]') is None

    def test_all_empty_fields_returns_none(self):
        """LLM 输出空 JSON（字段全缺/全空）→ 视为解析失败返回 None（不写垃圾行）。"""
        assert _parse_evaluation_output("{}") is None
        assert _parse_evaluation_output('{"foo": "bar"}') is None
        assert _parse_evaluation_output(
            '{"sentiment_label": null, "satisfaction_score": null}'
        ) is None

    def test_deduplication_and_strip(self):
        raw = (
            '{"concern_tags": [" 景点介绍 ", "景点介绍", "票务咨询", "", "  "],'
            ' "mentioned_spots": ["日光岩", "日光岩"],'
            ' "suggestions": [" 建议一 ", "建议一", "建议二", "", 123]}'
        )
        parsed = _parse_evaluation_output(raw)
        assert parsed["concern_tags"] == ["景点介绍", "票务咨询"]
        assert parsed["mentioned_spots"] == ["日光岩"]
        # suggestions 同样归一化：strip/去重/剔除空串与非字符串
        assert parsed["suggestions"] == ["建议一", "建议二"]

    def test_suggestions_empty_or_missing_returns_none(self):
        """suggestions 空列表/缺失 → None（可选字段，不参与全空判定）。"""
        parsed = _parse_evaluation_output(
            '{"sentiment_label": "positive", "satisfaction_score": 5,'
            ' "concern_tags": [], "suggestions": []}'
        )
        assert parsed["suggestions"] is None
        parsed = _parse_evaluation_output(
            '{"sentiment_label": "positive", "satisfaction_score": 5}'
        )
        assert parsed["suggestions"] is None

    def test_suggestions_only_output_still_invalid(self):
        """仅 suggestions 无其他评估字段 → 仍视为解析失败（None，不写垃圾行）。"""
        assert _parse_evaluation_output(
            '{"suggestions": ["建议优化路线指引"]}'
        ) is None


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


@pytest.mark.asyncio
async def test_evaluate_content_uses_local_llm():
    """本地 LLM 评估：注入 fake LLM 返回 JSON → 归一化结果（输出契约不变）。"""
    fake = _FakeLLM(
        '{"sentiment_label": "positive", "satisfaction_score": 5,'
        ' "concern_tags": ["景点介绍", "亲子游玩"], "mentioned_spots": ["日光岩"],'
        ' "suggestions": ["游客多次询问亲子设施，建议在介绍中补充相关信息"]}'
    )
    result = await evaluate_content("介绍一下日光岩", llm=fake)

    assert fake.invoked
    assert result["sentiment_label"] == "positive"
    assert result["satisfaction_score"] == 5
    assert result["concern_tags"] == ["景点介绍", "亲子游玩"]
    assert result["mentioned_spots"] == ["日光岩"]
    assert result["suggestions"] == [
        "游客多次询问亲子设施，建议在介绍中补充相关信息"
    ]
    # 提示词携带对话内容（HumanMessage content）
    from langchain_core.messages import HumanMessage

    assert any(
        "日光岩" in m.content
        for m in fake.prompt
        if isinstance(m, HumanMessage)
    )


@pytest.mark.asyncio
async def test_evaluate_content_suggestions_in_prompt():
    """System 提示词包含改进建议（suggestions）分析维度与输出格式（§5）。"""
    fake = _FakeLLM(
        '{"sentiment_label": "neutral", "satisfaction_score": 4,'
        ' "concern_tags": [], "mentioned_spots": [], "suggestions": []}'
    )
    await evaluate_content("随便聊聊", llm=fake)

    from langchain_core.messages import SystemMessage

    system_text = next(
        m.content for m in fake.prompt if isinstance(m, SystemMessage)
    )
    assert "## 5. 改进建议 (suggestions)" in system_text
    assert '"suggestions": ["建议1"]' in system_text
    assert "返回空数组 []" in system_text


@pytest.mark.asyncio
async def test_evaluate_content_empty_skips_llm():
    """空对话内容 → 跳过 LLM 调用，返回 None。"""
    fake = _FakeLLM("should not be called")
    assert await evaluate_content("", llm=fake) is None
    assert await evaluate_content("   ", llm=fake) is None
    assert not fake.invoked


@pytest.mark.asyncio
async def test_evaluate_content_llm_exception_returns_none():
    """LLM 调用异常 → 返回 None（不抛错，analysis_loop 跳过该对话）。"""

    class _BoomLLM:
        def invoke(self, prompt):
            raise RuntimeError("LLM down")

    assert await evaluate_content("你好", llm=_BoomLLM()) is None


@pytest.mark.asyncio
async def test_evaluate_content_parse_failure_returns_none():
    """LLM 输出不可解析 → 返回 None（不抛错）。"""
    fake = _FakeLLM("这不是 JSON 输出")
    assert await evaluate_content("你好", llm=fake) is None
    assert fake.invoked


@pytest.mark.asyncio
async def test_evaluate_content_llmconfig_error_returns_none(monkeypatch):
    """LLM 未配置（LLMConfigError）→ 返回 None（不抛错）。"""
    from app.core.llm import LLMConfigError

    def _raise_config_error(**kwargs):
        raise LLMConfigError("LLM_API_KEY 未配置")

    monkeypatch.setattr(
        "app.services.evaluation_service.get_chat_llm", _raise_config_error
    )
    assert await evaluate_content("你好") is None


# ── 与 tasks/analysis.py 集成（Evaluation 表写入） ──


class _DialogueStub:
    """最小 Dialogue 桩（仅 evaluate_dialogue 需要的字段）。"""

    def __init__(self, id, content):
        self.id = id
        self.content = content


@pytest.mark.asyncio
async def test_evaluate_dialogue_maps_evaluation(monkeypatch):
    """evaluate_dialogue 用本地 LLM 评估结果构造 Evaluation（字段映射兼容）。"""
    parsed = {
        "sentiment_label": "neutral",
        "satisfaction_score": 4,
        "concern_tags": ["路线规划"],
        "mentioned_spots": ["日光岩", "日光岩"],  # 去重交给 _normalize_string_list
    }

    async def _fake_evaluate(content, llm=None):
        return parsed

    monkeypatch.setattr(
        "app.tasks.analysis.evaluation_service.evaluate_content",
        _fake_evaluate,
    )
    dlg = _DialogueStub(id=7, content="怎么去日光岩")
    evaluation = await evaluate_dialogue(db=None, dialogue=dlg)

    assert isinstance(evaluation, Evaluation)
    assert evaluation.dialogue_id == 7
    assert evaluation.sentiment_label == "neutral"
    assert evaluation.satisfaction_score == 4
    assert evaluation.concern_tags == ["路线规划"]
    assert evaluation.mentioned_spots == ["日光岩"]  # 去重
    assert evaluation.raw_result == parsed


@pytest.mark.asyncio
async def test_evaluate_dialogue_failure_returns_none(monkeypatch):
    """评估失败（返回 None）→ evaluate_dialogue 返回 None（不抛错、不写库）。"""

    async def _fake_evaluate(content, llm=None):
        return None

    monkeypatch.setattr(
        "app.tasks.analysis.evaluation_service.evaluate_content",
        _fake_evaluate,
    )
    dlg = _DialogueStub(id=8, content="随便聊聊")
    assert await evaluate_dialogue(db=None, dialogue=dlg) is None
