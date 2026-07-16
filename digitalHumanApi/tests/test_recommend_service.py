"""Tests for app/services/recommend_service.py"""
import pytest
from app.services.recommend_service import _parse_recommend_output, _aggregate_dashboard_7d


class TestParseRecommendOutput:
    """Unit tests for Dify workflow output parsing."""

    def test_valid_json_with_questions(self):
        raw = '{"questions": ["问句1", "问句2"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1", "问句2"]

    def test_think_tag_stripping(self):
        raw = '<think>reasoning...</think>\n{"questions": ["问句1"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]

    def test_markdown_code_fence(self):
        raw = '```json\n{"questions": ["问句1"]}\n```'
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]

    def test_alias_fallback_recommended_questions(self):
        raw = '{"recommended_questions": ["问句1"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]

    def test_alias_fallback_suggestions(self):
        raw = '{"suggestions": ["问句1"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]

    def test_empty_input_returns_none(self):
        assert _parse_recommend_output("") is None
        assert _parse_recommend_output(None) is None

    def test_invalid_json_returns_none(self):
        assert _parse_recommend_output("not json") is None

    def test_deduplication(self):
        raw = '{"questions": ["问句1", "问句1", "问句2"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1", "问句2"]

    def test_strips_whitespace(self):
        raw = '{"questions": ["  问句1  ", "问句2"]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1", "问句2"]

    def test_filters_empty_strings(self):
        raw = '{"questions": ["", "问句1", "   "]}'
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]

    def test_non_list_questions_returns_none(self):
        raw = '{"questions": "not a list"}'
        result = _parse_recommend_output(raw)
        assert result is None

    def test_dict_input_passthrough(self):
        raw = {"questions": ["问句1"]}
        result = _parse_recommend_output(raw)
        assert result == ["问句1"]


@pytest.mark.asyncio
async def test_aggregate_dashboard_7d_empty_db(db_session):
    """_aggregate_dashboard_7d returns None when DB has no data."""
    result = await _aggregate_dashboard_7d(db_session)
    # Empty DB → no evaluations → None
    assert result is None
