"""系统提示词（chat_agent.SYSTEM_PROMPT，deepagents 装配层复用同一来源）单测。

覆盖（sg-prompt-optimize 任务验收：断言 prompt 包含关键约束）：
1. 回答风格：TTS 友好（禁用 markdown/emoji）、2-4 句简洁、先结论再展开、口语化「你」
2. 边界处理：景区无关问题引导、敏感话题拒绝转移、投诉记录、紧急情况求助电话
3. 主动引导：顺带推荐不强行推销
4. 推荐策略：年龄段/同行人员针对性建议、路线考虑地理位置顺序
5. 知识使用：知识库不够详细注明「建议到现场游客中心确认」、完全没有诚实告知
6. 保留面：角色名称不变、工具使用原则（零幻觉/诚实告知/安全边界）原样保留、
   deepagents 装配层复用同一来源（记忆工具/GPS 原则不受影响）
"""

from app.services import chat_agent
from app.services.deepagents import agent as dg_agent


# ── 角色与保留面 ──────────────────────────────────────────────────────────


def test_role_name_unchanged():
    """角色名称保持现有设定（AI 导览员 Neuro），不改名。"""
    assert "「景区导览」的 AI 导览员" in chat_agent.SYSTEM_PROMPT
    assert "名字叫「Neuro」" in chat_agent.SYSTEM_PROMPT


def test_tool_principles_preserved():
    """工具使用原则保留：结构化数据零幻觉/诚实告知/安全边界预订支付不办理。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "禁止凭空编造景点或路线" in prompt          # 结构化数据零幻觉
    assert "search_spots" in prompt and "get_spot_detail" in prompt
    assert "search_routes" in prompt and "get_route_detail" in prompt
    assert "filter_by_tags" in prompt
    assert "search_knowledge" in prompt
    assert "诚实告知" in prompt                          # 诚实告知原则
    assert "预订、购票" in prompt                        # 安全边界
    assert "支付" in prompt
    assert "人工客服" in prompt


# ── 回答风格（TTS 友好） ──────────────────────────────────────────────────


def test_style_tts_friendly():
    """回答风格：TTS 友好，禁用 markdown 语法与 emoji。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "TTS" in prompt
    assert "markdown" in prompt
    assert "emoji" in prompt
    assert "不要使用 markdown 语法" in prompt


def test_style_concise_and_conclusion_first():
    """简洁优先：2-4 句（约 50-120 字），先结论再问是否展开。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "2-4 句" in prompt
    assert "50-120 字" in prompt
    assert "先给出结论" in prompt
    assert "是否需要展开" in prompt


def test_style_colloquial():
    """口语化：用「你」称呼游客。"""
    assert "用「你」称呼游客" in chat_agent.SYSTEM_PROMPT


# ── 边界处理 ──────────────────────────────────────────────────────────────


def test_edge_unrelated_questions():
    """景区无关问题：礼貌引导回到游览话题。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "景区无关问题" in prompt
    assert "只负责景区导览" in prompt
    assert "引导回到游览话题" in prompt


def test_edge_sensitive_topics():
    """敏感话题：委婉拒绝并转移。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "敏感话题" in prompt
    assert "委婉拒绝" in prompt
    assert "转移话题" in prompt


def test_edge_complaints():
    """投诉与建议：认真倾听、记录反馈、尽力提供替代帮助。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "投诉与建议" in prompt
    assert "认真倾听" in prompt
    assert "记录下来反馈" in prompt


def test_edge_emergency():
    """紧急情况：告知拨打景区紧急电话或联系工作人员。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "紧急情况" in prompt
    assert "景区紧急电话" in prompt
    assert "最近的工作人员" in prompt


# ── 主动引导与推荐策略 ────────────────────────────────────────────────────


def test_proactive_guidance():
    """主动引导：回答后可顺带推荐相关景点/活动，不强行推销。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "顺带推荐相关景点或活动" in prompt
    assert "不要强行推销" in prompt


def test_recommendation_by_audience():
    """推荐策略：游客提及年龄段/同行人员时用推荐数据针对性建议。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "年龄段或同行人员" in prompt
    assert "带老人" in prompt and "带孩子" in prompt
    assert "针对性建议" in prompt


def test_recommendation_route_geo_order():
    """推荐策略：推荐路线考虑地理位置顺序。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "地理位置顺序" in prompt
    assert "游览动线" in prompt


# ── 知识使用 ──────────────────────────────────────────────────────────────


def test_knowledge_insufficient_note():
    """知识库不够详细：注明「建议到现场游客中心确认」。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "建议到现场游客中心确认" in prompt
    assert "合理补充" in prompt


def test_knowledge_none_honest():
    """完全没有相关信息：诚实告知并建议咨询现场工作人员。"""
    prompt = chat_agent.SYSTEM_PROMPT
    assert "完全没有相关信息" in prompt
    assert "咨询现场工作人员" in prompt


# ── deepagents 装配层复用（单一来源传播） ─────────────────────────────────


def test_deepagents_reuses_main_prompt():
    """deepagents 装配层复用 chat_agent.SYSTEM_PROMPT：关键约束一并生效。"""
    assert dg_agent.SYSTEM_PROMPT.startswith(chat_agent.SYSTEM_PROMPT)
    # 复用面：风格/边界/引导/知识使用关键约束透出
    for keyword in (
        "TTS",
        "markdown",
        "emoji",
        "先给出结论",
        "景区紧急电话",
        "不要强行推销",
        "建议到现场游客中心确认",
    ):
        assert keyword in dg_agent.SYSTEM_PROMPT


def test_deepagents_appended_sections_kept():
    """装配层追加段（记忆工具/GPS 原则）不受主链路 prompt 改动影响。"""
    prompt = dg_agent.SYSTEM_PROMPT
    assert "recall_user_memory" in prompt       # 记忆工具使用原则
    assert "save_user_memory" in prompt
    assert "geocode_location" in prompt          # GPS/高德位置/路线原则
    assert "plan_route" in prompt
    assert "amap_not_configured" in prompt
