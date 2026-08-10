"""Tests for app/services/recommend_agent.py — LangChain Tool Calling 推荐Agent。"""
import json
import pytest
from app.services.recommend_agent import (
    _extract_keywords,
    _score_route,
    _generate_summary,
    detect_tour_preference,
    filter_by_tags,
    get_route_detail,
    get_spot_detail,
    recommend,
    search_routes,
    search_spots,
)


class TestExtractKeywords:
    """关键词提取测试。"""

    def test_natural_scenery(self):
        scores = _extract_keywords("喜欢自然风光")
        assert "自然风光" in scores
        assert scores["自然风光"] >= 10

    def test_elderly_and_child(self):
        scores = _extract_keywords("带老人和小孩")
        assert any(t in scores for t in ["老年人", "带老人"])
        assert any(t in scores for t in ["亲子", "带小孩"])

    def test_photography(self):
        scores = _extract_keywords("想拍照打卡，出片好看")
        assert "拍照打卡" in scores
        assert scores["拍照打卡"] >= 10

    def test_couple_romantic(self):
        scores = _extract_keywords("情侣约会，要浪漫")
        assert "情侣" in scores
        assert "浪漫" in scores

    def test_empty_preference(self):
        scores = _extract_keywords("")
        assert scores == {}

    def test_no_match(self):
        scores = _extract_keywords("随便看看")
        assert scores == {}

    def test_multi_aspect(self):
        scores = _extract_keywords("喜欢自然风光和历史人文，带老人小孩，想拍照")
        assert len(scores) >= 4  # 至少匹配4个维度


class TestScoreRoute:
    """路线评分测试。"""

    def test_perfect_match(self):
        route = {
            "tags": ["亲子", "休闲"],
            "suitable_for": ["亲子家庭", "带小孩"],
        }
        tag_scores = {"亲子": 10, "带小孩": 10}
        score = _score_route(route, tag_scores)
        assert score == 100

    def test_partial_match(self):
        route = {
            "tags": ["亲子", "挑战"],
            "suitable_for": ["年轻人"],
        }
        tag_scores = {"亲子": 10, "带小孩": 10}
        score = _score_route(route, tag_scores)
        assert 0 < score < 100

    def test_no_match(self):
        route = {"tags": ["冒险"], "suitable_for": ["年轻人"]}
        tag_scores = {"亲子": 10, "老年人": 10}
        score = _score_route(route, tag_scores)
        assert score == 0

    def test_empty_scores(self):
        route = {"tags": ["自然风光"], "suitable_for": []}
        score = _score_route(route, {})
        assert score == 0


class TestGenerateSummary:
    """推荐摘要生成测试。"""

    def test_with_recommendations(self):
        from app.schemas.recommend import RouteRecommendation

        recs = [
            RouteRecommendation(
                route_id="route_001",
                route_name="测试路线",
                description="测试描述",
                spots=[],
                tags=[],
                suitable_for=[],
                total_duration_minutes=60,
                difficulty="轻松",
                recommend_reason="这是一条很好的路线",
                match_score=95,
            )
        ]
        summary = _generate_summary("自然风光", recs, {"自然风光": 10})
        assert "自然风光" in summary
        assert "测试路线" in summary
        assert "1条" in summary

    def test_empty_recommendations(self):
        summary = _generate_summary("不存在的偏好", [], {"测试": 5})
        assert "未找到" in summary


class TestDetectTourPreference:
    """游览偏好检测测试。"""

    def test_clear_preference(self):
        result = detect_tour_preference("我想去自然风光的景点，有推荐吗")
        assert result is not None

    def test_with_elderly(self):
        result = detect_tour_preference("带老人怎么玩比较好")
        assert result is not None

    def test_insufficient_keywords(self):
        result = detect_tour_preference("你好")
        assert result is None

    def test_single_keyword(self):
        result = detect_tour_preference("推荐")
        assert result is None  # 单个关键词不触发

    def test_empty_text(self):
        result = detect_tour_preference("")
        assert result is None


class TestTools:
    """LangChain Tool 测试。"""

    def test_search_spots_found(self):
        result = search_spots.invoke({"query": "自然"})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any("自然" in json.dumps(s, ensure_ascii=False) for s in data)

    def test_search_spots_not_found(self):
        result = search_spots.invoke({"query": "不存在的景点xyz"})
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "message" in data

    def test_search_spots_empty_query(self):
        result = search_spots.invoke({"query": ""})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 5

    def test_search_routes_found(self):
        result = search_routes.invoke({"query": "亲子"})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_search_routes_empty_query(self):
        result = search_routes.invoke({"query": ""})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 3

    def test_filter_by_tags(self):
        result = filter_by_tags.invoke({"tags": "亲子,轻松"})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1
        for route in data:
            tags = [t.lower() for t in route.get("tags", [])]
            suitable = [s.lower() for s in route.get("suitable_for", [])]
            all_tags = tags + suitable
            assert "亲子" in all_tags or "轻松" in all_tags

    def test_filter_by_tags_empty(self):
        result = filter_by_tags.invoke({"tags": ""})
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 3

    def test_get_route_detail_valid(self):
        result = get_route_detail.invoke({"route_id": "route_001"})
        data = json.loads(result)
        assert data["name"] == "经典自然风光一日游"
        assert "spots_detail" in data

    def test_get_route_detail_invalid(self):
        result = get_route_detail.invoke({"route_id": "route_nonexist"})
        data = json.loads(result)
        assert "message" in data

    def test_get_spot_detail_valid(self):
        result = get_spot_detail.invoke({"spot_id": "spot_001"})
        data = json.loads(result)
        assert data["name"] == "翠微峰"

    def test_get_spot_detail_invalid(self):
        result = get_spot_detail.invoke({"spot_id": "spot_nonexist"})
        data = json.loads(result)
        assert "message" in data


class TestSearchQueryFallback:
    """问题式 query 降级搜索测试（船长反馈：问"有哪些值得去的景点"曾返回未找到）。"""

    def test_search_spots_question_style(self):
        """问题式 query（有哪些值得去的景点/景点/值得去）应返回景点列表而非未找到。"""
        for q in ["有哪些值得去的景点", "景点", "值得去"]:
            data = json.loads(search_spots.invoke({"query": q}))
            assert isinstance(data, list), f"search_spots({q!r}) 应返回列表而非未找到"
            assert len(data) >= 5
            assert all("name" in s for s in data)

    def test_search_routes_question_style(self):
        """问题式 query（有什么路线/路线）应返回路线列表而非未找到。"""
        for q in ["有什么路线", "路线"]:
            data = json.loads(search_routes.invoke({"query": q}))
            assert isinstance(data, list), f"search_routes({q!r}) 应返回列表而非未找到"
            assert len(data) >= 3
            assert all("name" in r for r in data)

    def test_question_style_strips_to_tags(self):
        """去停用词后命中标签：应返回匹配子集（而非未找到或全部数据）。"""
        data = json.loads(search_spots.invoke({"query": "帮我推荐拍照的景点"}))
        assert isinstance(data, list)
        assert len(data) >= 1
        assert all("拍照" in json.dumps(s, ensure_ascii=False) for s in data)

    def test_specific_nonexistent_name_not_found(self):
        """具体不存在的名称（黄鹤楼）仍返回未找到语义。"""
        for tool in (search_spots, search_routes):
            data = json.loads(tool.invoke({"query": "黄鹤楼"}))
            assert isinstance(data, dict)
            assert "message" in data

    def test_specific_existing_name_still_matches(self):
        """具体存在的名称/标签/ID 精确匹配行为不变。"""
        data = json.loads(search_spots.invoke({"query": "翠微峰"}))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "翠微峰"
        # 标签匹配不变
        data = json.loads(search_spots.invoke({"query": "自然"}))
        assert isinstance(data, list)
        assert len(data) >= 1
        assert all("自然" in json.dumps(s, ensure_ascii=False) for s in data)


@pytest.mark.asyncio
class TestRecommend:
    """完整推荐流程测试。"""

    async def test_recommend_natural_with_elderly(self):
        resp = await recommend("喜欢自然风光，带老人")
        assert resp.total >= 1
        assert resp.recommendations[0].match_score > 0
        assert "自然风光" in resp.summary or "老人" in resp.summary

    async def test_recommend_family_with_kids(self):
        resp = await recommend("带小孩出去玩")
        assert resp.total >= 1
        top = resp.recommendations[0]
        assert "亲子" in top.tags or "亲子" in top.suitable_for

    async def test_recommend_photography(self):
        resp = await recommend("想拍照打卡")
        assert resp.total >= 1
        # At least one recommendation should have photography-related tags
        all_tags = []
        for r in resp.recommendations:
            all_tags.extend(r.tags)
            all_tags.extend(r.suitable_for)
        assert any("拍照" in t for t in all_tags)

    async def test_recommend_couple(self):
        resp = await recommend("情侣约会浪漫")
        assert resp.total >= 1
        top = resp.recommendations[0]
        assert "情侣" in top.suitable_for or "浪漫" in top.tags

    async def test_recommend_empty_preference(self):
        resp = await recommend("")
        assert resp.total == 0
        assert "请描述" in resp.summary

    async def test_recommend_response_structure(self):
        resp = await recommend("自然风光")
        assert resp.preference == "自然风光"
        assert isinstance(resp.total, int)
        for rec in resp.recommendations:
            assert rec.route_id.startswith("route_")
            assert rec.route_name
            assert len(rec.spots) >= 1
            assert 0 <= rec.match_score <= 100
            assert rec.recommend_reason

    async def test_recommend_complex_preference(self):
        """综合偏好应在多条路线间做出合理排序。"""
        resp = await recommend("喜欢自然风光和历史人文，带老人和小孩，想拍照好看")
        assert resp.total >= 1
        # 首推应该是综合匹配度最高的
        top_score = resp.recommendations[0].match_score
        for rec in resp.recommendations[1:]:
            assert rec.match_score <= top_score
