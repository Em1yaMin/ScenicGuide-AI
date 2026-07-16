"""Tests for app/tasks/recommend_questions.py"""
from app.tasks.recommend_questions import get_cached_questions


class TestGetCachedQuestions:
    """Cache getter tests."""

    def test_returns_list(self):
        result = get_cached_questions()
        assert isinstance(result, list)

    def test_cache_is_module_level_list(self):
        """The cache is a list (may be empty or populated depending on test order)."""
        import app.tasks.recommend_questions as mod
        assert isinstance(mod._recommend_cache, list)
