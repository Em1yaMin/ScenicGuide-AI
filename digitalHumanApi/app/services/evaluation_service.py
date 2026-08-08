"""本地 LLM 量化评估服务（阶段4：Dify Workflow 量化评估本地化，船长决策 2026-08-06 方案A）。

从「调用 Dify Workflow 量化评估」改为「本地 LLM 评估」：复用 `app/core/llm.py` 工厂
（`LLM_API_*` 配置 opencode-go），prompt 语义对齐原 Dify 量化工作流
（见 `doc/dify配置/quantitative-workflow-量化工作流.md` —— 现为历史存档）——
输出情感（positive/neutral/negative）、满意度评分（1-5）、关注点标签、涉及景点、
改进建议（suggestions），Evaluation 表写入与管理后台消费不变。

失败降级：LLM 未配置（LLMConfigError）/调用异常/解析失败均返回 None（不抛错、
不阻断 `analysis_loop`），该对话本轮跳过、下轮自动重试。
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.llm import LLMConfigError, get_chat_llm
from app.services.usage_tracker import CHAIN_EVALUATION, usage_context

logger = logging.getLogger(__name__)

# 单条评估 LLM 调用参数（对齐阶段3 分析链路的超时经验，见 doc/phase3-analytics-verification.md：
# opencode-go 延迟高且波动大，超时预算需放宽；评估 prompt 短于运营分析，故上限较其收紧）
_EVAL_MAX_TOKENS = 512
_EVAL_TIMEOUT = 120  # httpx 请求超时（秒）
_EVAL_TEMPERATURE = 0.1  # 低温度保证 JSON 格式稳定（对齐原 Workflow temperature=0.1）

_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)
_MARKDOWN_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

# 评估 System 提示词（语义移植自原 Dify 量化工作流 LLM 节点提示词，
# 见 doc/dify配置/quantitative-workflow-量化工作流.md §3.1，输出字段不变）
_EVALUATION_SYSTEM_PROMPT = """\
你是一个景区导览对话质量分析专家。你的任务是对一段游客与数字人导游的对话进行量化评估，输出结构化的 JSON 分析结果。

# 分析维度

## 1. 情感分析 (sentiment_label)
判断游客在对话中表现出的整体情感倾向：
- "positive"：游客表达满意、感谢、赞赏、期待等积极情绪
- "negative"：游客表达不满、抱怨、失望、愤怒等消极情绪
- "neutral"：游客仅进行中性提问或陈述，无明显情绪倾向

注意：
- 只看游客的话语，不看 AI 导游的回复
- 如果对话中游客有情绪变化，以整体倾向为准
- 纯景点询问（如"介绍一下XX景点"）通常为 neutral

## 2. 满意度评分 (satisfaction_score)
根据对话内容，评估游客对本次服务的满意度，打分 1-5：
- 5分：游客明确表达高度满意（如"太好了""谢谢你们""非常棒"）
- 4分：对话顺利，游客得到了想要的答案，无明显不满
- 3分：游客得到了基本答案，但略显失望或需要追问
- 2分：游客表达不满、答案不满意，或 AI 未能解决问题
- 1分：游客愤怒投诉、AI 完全答非所问、或对话中断

注意：
- 如果对话仅 1-2 轮且游客未表态，默认给 4 分
- 如果游客追问多次才得到答案，适当降分

## 3. 关注点标签 (concern_tags)
从对话中提取游客关注的话题标签，字符串数组。
常见标签示例：
- "票务咨询" — 询问门票价格、优惠政策、购票方式
- "路线规划" — 询问游览路线、行程安排
- "景点介绍" — 了解景点信息、历史背景
- "设施指引" — 询问卫生间、停车场、餐饮、Wi-Fi 等设施
- "开放时间" — 询问景区或景点的开放/关闭时间
- "交通出行" — 询问到达方式、停车、景区内交通
- "投诉建议" — 游客投诉或提出改进建议
- "亲子游玩" — 涉及带孩子游玩、亲子设施
- "老人出游" — 涉及老人出游、无障碍设施
- "拍照打卡" — 询问拍照地点、网红打卡点

规则：
- 每个标签 2-6 个字，使用中文
- 2-5 个标签为宜，不要超过 8 个
- 标签应概括对话主题，不是逐句提取关键词

## 4. 提及景点 (mentioned_spots)
从对话中提取游客提及或询问的景区景点名称，字符串数组。
规则：
- 只提取具体的景点名称（如「日光岩」「菽庄花园」）
- 不提取景区总称（如「鼓浪屿」整体）
- 若不涉及具体景点，返回空数组 []
- 去重（同一景点只出现一次）

## 5. 改进建议 (suggestions)
如果对话反映出可改进的服务点，提出 1-3 条具体建议，字符串数组。
规则：
- 针对具体问题提建议，不使用套话（如「提升服务质量」这种空泛建议不要）
- 建议格式：问题现象 + 改进方向，如「游客多次询问XX景点位置，建议在介绍中增加方位描述」
- 如果对话顺利无明显问题，返回空数组 []
- 最多 3 条

# 输出格式
只输出一个 JSON 对象，不要有任何解释文字。JSON 格式如下：
{
  "sentiment_label": "positive|negative|neutral",
  "satisfaction_score": 1-5的整数,
  "concern_tags": ["标签1", "标签2"],
  "mentioned_spots": ["景点1"],
  "suggestions": ["建议1"]  // 无建议时为空数组[]
}

注意：
- 严格输出 JSON，不要添加 ```json 标记
- 所有字符串使用中文
- satisfaction_score 必须是数字，不是字符串
"""

# 情感别名归一：兼容旧 Dify Workflow 可能的 happy/confused 取值
_SENTIMENT_ALIASES = {
    "happy": "positive",
    "satisfied": "positive",
    "angry": "negative",
    "confused": "neutral",
}


def normalize_string_list(raw: Any) -> Optional[List[str]]:
    """归一化字符串列表：strip 空白、去重、剔除空串与非字符串项。

    空输入/非列表/全空 → None（与 tasks/analysis.py 旧 `_normalize_string_list`
    语义一致，analysis 侧复用本助手）。
    """
    if not isinstance(raw, list):
        return None
    cleaned: List[str] = []
    seen: set = set()
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped and stripped not in seen:
                cleaned.append(stripped)
                seen.add(stripped)
    return cleaned if cleaned else None


def _parse_evaluation_output(text: Any) -> Optional[Dict[str, Any]]:
    """解析本地 LLM 评估输出的 JSON 文本（容错解析，行为对齐旧 Dify 输出解析）。

    三层防御：
    1. 剥离 <think>...</think> 推理标签
    2. 提取 ```json ... ``` 代码围栏内容
    3. json.loads 解码后归一字段（别名兜底：sentiment/concerns/spots；
       suggestions 无别名，缺失/空列表 → None）
    4. 类型校验（satisfaction_score 转 int，列表类字段过滤非法项）

    任何失败返回 None（调用方降级，不抛错）。
    """
    if text is None:
        return None
    if isinstance(text, dict):
        parsed = text
    elif isinstance(text, str):
        cleaned = _THINK_PATTERN.sub("", text).strip()
        fence_match = _MARKDOWN_FENCE_PATTERN.search(cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            logger.warning("评估 LLM 输出非 JSON，忽略: %r", text[:200])
            return None
    else:
        return None

    if not isinstance(parsed, dict):
        return None

    # 字段别名兜底（兼容旧 Dify Workflow 输出变体）
    sentiment_raw = parsed.get("sentiment_label") or parsed.get("sentiment")
    sentiment: Optional[str] = None
    if isinstance(sentiment_raw, str):
        label = sentiment_raw.strip().lower()
        sentiment = _SENTIMENT_ALIASES.get(label, label) or None

    satisfaction_score: Optional[int] = None
    raw_score = parsed.get("satisfaction_score")
    if raw_score is not None:
        try:
            score = int(raw_score)
            # 原口径 1-5；越界值钳制到合法区间（LLM 偶发 0/6）
            satisfaction_score = min(5, max(1, score))
        except (ValueError, TypeError):
            satisfaction_score = None

    concern_tags = normalize_string_list(
        parsed.get("concern_tags") or parsed.get("concerns")
    )
    mentioned_spots = normalize_string_list(
        parsed.get("mentioned_spots") or parsed.get("spots")
    )
    # suggestions 为可选字段（原 Workflow §1.3 第5项）：空列表/缺失 → None
    # （不参与全空判定，管理后台报告「改进建议」区按需消费非空列表）
    suggestions = normalize_string_list(parsed.get("suggestions"))

    # 全空输出（如 LLM 返回 {} / 空 JSON 但字段全缺）视为解析失败 → None，
    # 由调用方本轮跳过、下轮重试（不写全 NULL 垃圾评估行）
    if (
        sentiment is None
        and satisfaction_score is None
        and concern_tags is None
        and mentioned_spots is None
    ):
        logger.debug("评估 LLM 输出无可解析字段，忽略: %r", str(text)[:200])
        return None

    return {
        "sentiment_label": sentiment,
        "satisfaction_score": satisfaction_score,
        "concern_tags": concern_tags,
        "mentioned_spots": mentioned_spots,
        "suggestions": suggestions,
    }


def _invoke_evaluation_llm(llm: Any, messages: List[Any]) -> Any:
    """量化评估 LLM 调用（线程内执行，标注记账链路）。"""
    with usage_context(CHAIN_EVALUATION):
        return llm.invoke(messages)


async def evaluate_content(
    content: str, llm: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """本地 LLM 评估单条对话内容，返回归一化后的结构化评估结果。

    Args:
        content: 对话内容（Dialogue.content）
        llm: 可注入的 ChatOpenAI 实例（测试用）；默认走 llm.py 工厂
            （get_chat_llm，LLM_* 配置 opencode-go，max_tokens 经 extra_body 注入）

    Returns:
        归一化评估 dict（sentiment_label/satisfaction_score/concern_tags/
        mentioned_spots/suggestions）；任何失败（LLM 未配置/调用异常/解析失败）
        返回 None，不抛错——analysis_loop 本轮跳过该对话，下轮自动重试。
    """
    if not content or not content.strip():
        logger.debug("评估跳过：对话内容为空")
        return None

    try:
        llm = llm or get_chat_llm(
            max_tokens=_EVAL_MAX_TOKENS,
            temperature=_EVAL_TEMPERATURE,
            timeout=_EVAL_TIMEOUT,
        )
        # 批量评估需 asyncio 非阻塞：LLM invoke 为同步阻塞调用，经 to_thread 派发
        # 到线程池（参考 #30 recommend_service 的 asyncio.to_thread 模式），
        # 保证 analysis_loop 事件循环不被长延迟端点卡死
        response = await asyncio.to_thread(
            _invoke_evaluation_llm,
            llm,
            [
                SystemMessage(content=_EVALUATION_SYSTEM_PROMPT),
                HumanMessage(content=f"请分析以下游客与景区AI导游的对话：\n\n{content}"),
            ],
        )
        raw = getattr(response, "content", response)
        if not isinstance(raw, str):
            raw = str(raw or "")
        return _parse_evaluation_output(raw)
    except LLMConfigError as e:
        logger.warning("量化评估：LLM 未配置（降级返回 None，本轮跳过）: %s", e)
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("量化评估失败（降级返回 None，下轮重试）: %s", e)
        return None
