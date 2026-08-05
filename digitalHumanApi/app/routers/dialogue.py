import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import resolve_dialogue_user
from app.services import chat_agent
from app.services.rag_service import build_rag_context, search_documents
from app.tasks.polling import poll_once

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dialogue", tags=["对话抓取"])


# --- Request schemas ---

class ChatRequest(BaseModel):
    """RAG 增强对话请求。"""
    query: str = Field(..., description="用户输入", min_length=1, max_length=5000)
    user: str = Field(default="visitor", description="用户标识")
    conversation_id: str = Field(default="", description="会话 ID（LangChain 进程内会话或 Dify 会话；留空则新建）")
    use_rag: bool = Field(default=True, description="是否启用本地 RAG 检索")
    rag_top_k: int = Field(default=3, description="RAG 检索结果数", ge=1, le=10)
    streaming: bool = Field(default=False, description="是否流式返回")


class ChatResponse(BaseModel):
    """对话响应。"""
    answer: str = Field(default="", description="回复内容")
    conversation_id: str = Field(default="", description="会话 ID（LangChain 进程内会话或 Dify 会话）")
    rag_context: str = Field(default="", description="注入的 RAG 上下文（调试用）")
    rag_sources: list = Field(default_factory=list, description="RAG 检索来源")


class RagContextRequest(BaseModel):
    """RAG 上下文检索请求（供 ADH 调用）。"""
    query: str = Field(..., description="用户输入", min_length=1, max_length=5000)
    top_k: int = Field(default=3, description="返回结果数", ge=1, le=10)


class RagContextResponse(BaseModel):
    """RAG 上下文响应。"""
    context: str = Field(default="", description="拼接后的上下文字符串")
    sources: list = Field(default_factory=list, description="检索来源")


# --- Endpoints ---

@router.post("/sync-now")
async def sync_now(user: Optional[str] = Query(None)) -> Dict[str, Any]:
    """手动触发一次对话轮询。

    Args:
        user: 可选，指定同步的虚拟用户。不传则同步所有配置用户。
    """
    count = await poll_once(user_filter=user)
    return {"status": "ok", "synced": count}


@router.post("/chat", response_model=ChatResponse)
async def chat_with_rag(
    request: ChatRequest,
    authorization: Optional[str] = Header(
        default=None,
        description="Bearer token（可选）：登录用户/游客 token 优先于请求体 user 字段；"
        "无 token 时向后兼容，使用请求体 user（默认 visitor）",
    ),
) -> ChatResponse:
    """RAG 增强的对话接口（双轨：LangChain agent 主链路，Dify 兜底）。

    流程（DIALOGUE_ENGINE=langchain，默认）:
    1. 走 LangChain 对话 agent（app/services/chat_agent.py）：结构化工具
       直查景点/路线（零幻觉）+ search_knowledge 统一检索入口查知识库
       + recommend_routes 规则推荐，进程内多轮上下文
    2. agent 链路异常（LLM 未配置/网络/API 故障）时自动回落 Dify：
       先本地 RAG 预检索注入 query，再调用 Dify Chat Agent

    DIALOGUE_ENGINE=dify 时强制走 Dify（原链路）。
    RAG 作为补充：即使 RAG 无结果，仍会正常调用 Dify。
    """
    # 用户标识来源：优先 Authorization Bearer token（登录用户 → 用户 id；
    # 游客 token → 游客 id；无效 token 降级为 "visitor" 并告警，见
    # security.resolve_dialogue_user 决策注释）；无 token 时回退请求体 user
    # 字段（默认 "visitor"），保持向后兼容。
    effective_user = resolve_dialogue_user(authorization) or request.user

    # Step A: LangChain 对话 agent 主链路（默认引擎；异常自动回落 Dify）
    if settings.DIALOGUE_ENGINE != "dify":
        try:
            result = await chat_agent.chat(
                query=request.query,
                user=effective_user,
                conversation_id=request.conversation_id,
                use_rag=request.use_rag,
                rag_top_k=request.rag_top_k,
            )
            return ChatResponse(
                answer=result.answer,
                conversation_id=result.conversation_id,
                rag_context=result.rag_context,
                rag_sources=result.rag_sources,
            )
        except Exception as e:
            # 故障回落：agent 链路任何异常（含 LLM 未配置）都不阻断对话，
            # 自动降级到 Dify 原链路（演示不翻车）
            logger.exception(
                "LangChain 对话链路失败，自动回落 Dify (engine=%s): %s",
                settings.DIALOGUE_ENGINE,
                e,
            )

    # Step B: Dify 链路（原逻辑，LangChain 回落路径 / DIALOGUE_ENGINE=dify）
    rag_context = ""
    rag_sources = []
    modified_query = request.query

    # Step B1: RAG 检索
    if request.use_rag:
        try:
            results = await search_documents(
                query=request.query,
                top_k=request.rag_top_k,
            )
            if results:
                rag_context = build_rag_context(results)
                rag_sources = [
                    {"source": r["source"], "score": r["score"]}
                    for r in results
                ]
                # 将 RAG 上下文注入 query
                modified_query = (
                    f"{rag_context}\n\n"
                    f"【用户问题】\n{request.query}\n\n"
                    f"请基于以上知识库内容回答用户问题。"
                    f"如果知识库内容不足以回答，请根据你的知识诚实告知。"
                )
                logger.info("RAG context injected, %d sources", len(rag_sources))
        except Exception as e:
            logger.warning("RAG retrieval failed, proceeding without RAG: %s", e)

    # Step B2: 调用 Dify Chat Agent
    # 会话 ID 移交：LangChain 链路生成的进程内 uuid 对 Dify 未知（透传会
    # 触发 400 conversation_not_exist），回落时传空串让 Dify 新建会话；
    # 仅透传 Dify 自己生成的 id，保证跨引擎切换后多轮对话可继续。
    fallback_conversation_id = request.conversation_id
    if chat_agent.has_conversation(request.conversation_id):
        fallback_conversation_id = ""
    try:
        dify_response = await _call_dify_chat(
            query=modified_query,
            user=effective_user,
            conversation_id=fallback_conversation_id,
        )
        answer = dify_response.get("answer", "")
        conversation_id = dify_response.get("conversation_id", "")
    except Exception:
        logger.exception("Dify chat call failed")
        answer = "抱歉，AI 服务暂时不可用，请稍后再试。"
        conversation_id = request.conversation_id

    return ChatResponse(
        answer=answer,
        conversation_id=conversation_id,
        rag_context=rag_context,
        rag_sources=rag_sources,
    )


@router.post("/rag-context", response_model=RagContextResponse)
async def get_rag_context(request: RagContextRequest) -> RagContextResponse:
    """获取 RAG 检索上下文（供 ADH 等下游调用）。

    ADH 可在调用 Dify 之前调用此接口获取本地知识库上下文，
    然后自行拼接 prompt 后发送给 Dify。
    """
    try:
        results = await search_documents(
            query=request.query,
            top_k=request.top_k,
        )
        context = build_rag_context(results)
        sources = [
            {"source": r["source"], "score": r["score"]}
            for r in results
        ]
        logger.info(
            "RAG context retrieved: query='%s', found=%d",
            request.query[:80],
            len(results),
        )
        return RagContextResponse(context=context, sources=sources)
    except Exception:
        logger.exception("RAG context retrieval failed")
        return RagContextResponse(context="", sources=[])


# --- Helpers ---

async def _call_dify_chat(
    query: str,
    user: str = "visitor",
    conversation_id: str = "",
) -> Dict[str, Any]:
    """调用 Dify Chat Agent 的 /chat-messages 接口。

    Args:
        query: 用户消息（可能已包含 RAG 上下文）
        user: Dify 用户标识
        conversation_id: 会话 ID（空字符串表示新会话）

    Returns:
        {"answer": str, "conversation_id": str}
    """
    if not settings.DIFY_API_URL or not settings.DIFY_API_KEY:
        raise RuntimeError("Dify API not configured")

    base_url = settings.DIFY_API_URL.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "user": user,
        "conversation_id": conversation_id,
    }

    max_retries = 3
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(60.0),
            ) as client:
                response = await client.post("/chat-messages", json=payload)
                response.raise_for_status()
                data = response.json()
                return {
                    "answer": data.get("answer", ""),
                    "conversation_id": data.get("conversation_id", ""),
                }
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "Dify chat failed (attempt %d/%d): %s; retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                await asyncio.sleep(wait)
        except Exception:
            raise

    raise last_error  # type: ignore[misc]
