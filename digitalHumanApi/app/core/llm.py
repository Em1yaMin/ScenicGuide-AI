"""
对话 LLM 工厂（LangChain ChatOpenAI）— 阶段1 LLM 接入层。

配置（`LLM_*`，与 Embedding 的 `EMBEDDING_*`、运营分析的 `OPENAI_*` 完全独立）：

- `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`：主端点
  （默认 opencode-go OpenAI 兼容端点 + deepseek-v4-flash，见 `.env.example`）
- `LLM_FALLBACK_API_BASE` / `LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_MODEL`：备用端点
  （默认官方 DeepSeek API），主端点未配置 key 时自动降级
- `LLM_TEMPERATURE`：默认采样温度（可被调用方覆盖）

DeepSeek 思考格式（reasoning_content）兼容性结论（2026-08 冒烟实测，
完整证据见 `doc/llm-adapter-smoke-test.md`）：

- **自动剥离**：langchain-openai 把 `reasoning_content` 挡在模型对象之外，
  `invoke`/`stream` 的 `content` 即最终回答；思考阶段的流式 chunk 为空串；
  思考耗尽导致 content 为空时也不报错 → 无需自定义剥离逻辑
- **多轮回传**：opencode-go 网关不强制历史 assistant 消息携带
  `reasoning_content`（容错）；官方 DeepSeek API 要求回传，LangChain 默认
  丢弃该字段 → 备用通道的多轮对话需在对话链路层自定义消息转换（本层不处理）
- **max_tokens 字段**：langchain-openai 1.4.1 硬编码将 `max_tokens` 重命名为
  `max_completion_tokens` 发送，DeepSeek 系端点不识别该字段并静默忽略
  （实测长度限制完全失效）→ 本工厂把 `max_tokens` 注入 `extra_body`，
  保证长度限制在 DeepSeek 系端点生效
"""

import logging
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.services.usage_tracker import UsageTrackingChatOpenAI

logger = logging.getLogger(__name__)


class LLMConfigError(RuntimeError):
    """对话 LLM 配置不可用（主/备用端点均未配置 API Key）。"""


def _build_extra_body(max_tokens: Optional[int], extra_body: Optional[Dict]) -> Optional[Dict]:
    """合并 max_tokens 到 extra_body。

    langchain-openai 1.4.1 把 ChatOpenAI 的 max_tokens 字段按
    `max_completion_tokens` 发送（OpenAI 新命名），DeepSeek 系端点不识别并
    静默忽略。通过 openai SDK 层的 `extra_body` 直接注入 `max_tokens` 字段，
    实测长度限制严格生效（见冒烟文档）。
    """
    body: Dict[str, Any] = dict(extra_body or {})
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return body or None


def create_chat_llm(
    api_base: str,
    api_key: str,
    model: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.7,
    extra_body: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """按显式参数构造 ChatOpenAI（底层构造，主/备用端点均走此函数）。

    Args:
        api_base: OpenAI 兼容端点基址（如 https://opencode.ai/zen/go/v1）
        api_key: API Key
        model: 模型名
        max_tokens: 输出上限。通过 extra_body 注入 `max_tokens` 字段
            （DeepSeek 系端点不识别 langchain 默认的 max_completion_tokens）
        temperature: 采样温度
        extra_body: 追加到请求体的自定义字段（如思考强度控制）
        **kwargs: 透传给 ChatOpenAI 的其余参数（timeout、http_client 等）
    """
    if not api_key:
        raise LLMConfigError(f"未配置 API Key（endpoint={api_base}）")
    # 注意：不传 ChatOpenAI 的 max_tokens 字段（默认 None）——
    # langchain-openai 1.4.1 会把该字段重命名为 max_completion_tokens 发送，
    # DeepSeek 系端点不识别并静默忽略（长度限制失效）。
    # 长度限制统一经 extra_body 注入（见 _build_extra_body）。
    # 批次7 C5 token 记账：USAGE_TRACKING_ENABLED 时构造 UsageTrackingChatOpenAI
    # 子类（模型调用层统一埋点，见 app/services/usage_tracker.py；关闭时行为
    # 与历史完全一致，零额外开销）
    llm_cls = UsageTrackingChatOpenAI if settings.USAGE_TRACKING_ENABLED else ChatOpenAI
    return llm_cls(
        model=model,
        api_key=api_key,
        base_url=api_base,
        temperature=temperature,
        extra_body=_build_extra_body(max_tokens, extra_body),
        **kwargs,
    )


def resolve_endpoint(*, use_fallback: bool = False) -> Dict[str, str]:
    """解析当前生效的端点配置（主端点缺 key 时自动降级备用端点）。

    Returns:
        形如 {"api_base": ..., "api_key": ..., "model": ...} 的端点配置

    Raises:
        LLMConfigError: 主/备用端点均未配置 API Key
    """
    primary_ready = bool(settings.LLM_API_KEY)
    if use_fallback or not primary_ready:
        if settings.LLM_FALLBACK_API_KEY:
            logger.info(
                "对话 LLM 使用备用端点: base=%s model=%s (use_fallback=%s)",
                settings.LLM_FALLBACK_API_BASE,
                settings.LLM_FALLBACK_MODEL,
                use_fallback,
            )
            return {
                "api_base": settings.LLM_FALLBACK_API_BASE,
                "api_key": settings.LLM_FALLBACK_API_KEY,
                "model": settings.LLM_FALLBACK_MODEL,
            }
        if not primary_ready:
            raise LLMConfigError(
                "LLM_API_KEY 与 LLM_FALLBACK_API_KEY 均未配置，无法创建对话 LLM"
                "（配置见 digitalHumanApi/.env.example）"
            )
        # use_fallback=True 但备用未配置 → 回退主端点（避免强切导致不可用）
        logger.warning("LLM_FALLBACK_API_KEY 未配置，use_fallback=True 时仍使用主端点")
    logger.info(
        "对话 LLM 使用主端点: base=%s model=%s",
        settings.LLM_API_BASE,
        settings.LLM_MODEL,
    )
    return {
        "api_base": settings.LLM_API_BASE,
        "api_key": settings.LLM_API_KEY,
        "model": settings.LLM_MODEL,
    }


def get_chat_llm(
    *,
    use_fallback: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """按 Settings 配置构造对话 LLM（主/备用端点可切换）。

    Args:
        use_fallback: True 时强制使用备用端点（主端点故障时的 failover 入口）
        max_tokens: 输出上限（经 extra_body 注入，见 create_chat_llm）
        temperature: 采样温度，默认取 LLM_TEMPERATURE
        **kwargs: 透传给 create_chat_llm 的其余参数（timeout 等）
    """
    endpoint = resolve_endpoint(use_fallback=use_fallback)
    return create_chat_llm(
        api_base=endpoint["api_base"],
        api_key=endpoint["api_key"],
        model=endpoint["model"],
        max_tokens=max_tokens,
        temperature=(
            settings.LLM_TEMPERATURE if temperature is None else temperature
        ),
        **kwargs,
    )
