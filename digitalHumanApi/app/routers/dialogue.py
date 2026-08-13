import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import resolve_dialogue_user
from app.models.user import User
from app.services import chat_agent, deepagents
from app.services.dify_stream import stream_dify_chat as _call_dify_chat_stream
from app.services.rag_service import build_rag_context, search_documents
from app.tasks.polling import poll_once

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dialogue", tags=["对话抓取"])


async def _ensure_user_active(db: AsyncSession, user: str) -> None:
    """对话用户门禁（2026-08-13 账号管理：对话受控）。

    仅对「注册用户 id」形态（全数字）的用户标识校验：用户在 users 表中
    不存在（已删除）或被软禁用 → 403 阻断对话。游客（visitor / guest-*）
    与请求体自定义标识不校验（维持既有行为）。
    """
    if not user.isdigit():
        return
    try:
        u = await db.get(User, int(user))
    except (TypeError, ValueError):
        return
    if u is None or u.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用或注销，请联系管理员",
        )


# --- Request schemas ---

class ChatRequest(BaseModel):
    """RAG 增强对话请求。"""
    query: str = Field(..., description="用户输入", min_length=1, max_length=5000)
    user: str = Field(default="visitor", description="用户标识")
    conversation_id: str = Field(default="", description="会话 ID（LangChain 会话或 Dify 会话；留空则新建）")
    use_rag: bool = Field(default=True, description="是否启用本地 RAG 检索")
    rag_top_k: int = Field(default=3, description="RAG 检索结果数", ge=1, le=10)
    streaming: bool = Field(default=False, description="是否流式返回")


class ChatResponse(BaseModel):
    """对话响应。"""
    answer: str = Field(default="", description="回复内容")
    conversation_id: str = Field(default="", description="会话 ID（LangChain 会话或 Dify 会话）")
    rag_context: str = Field(default="", description="注入的 RAG 上下文（调试用）")
    rag_sources: list = Field(default_factory=list, description="RAG 检索来源")
    route_data: Optional[dict] = Field(default=None, description="路线规划结果（plan_route 工具信封原样透出；None=本轮未规划路线，ADH 前端据此渲染路线卡片）")


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
    db: AsyncSession = Depends(get_db),
) -> Any:
    """RAG 增强的对话接口（双轨：deepagents 装配层主链路，Dify 兜底）。

    流程（DIALOGUE_ENGINE=langchain，默认）:
    1. 走 deepagents 装配层（app/services/deepagents/agent.py，A3 主链路）：
       LangChain 系 agent（结构化工具直查景点/路线零幻觉 + search_knowledge
       统一检索入口查知识库 + recommend_routes 规则推荐 + 记忆四工具），
       多轮上下文（短期记忆持久化：SQLite 落盘，重启可恢复）、意图分类
       双模式（复杂任务注入规划指令）、轻量用户摘要注入、对话后自动
       抽取长期记忆写入（按 user 隔离，游客不写）
    2. 主链路异常（LLM 未配置/网络/API 故障）时自动回落 Dify：
       先本地 RAG 预检索注入 query，再调用 Dify Chat Agent

    DIALOGUE_ENGINE=dify 时强制走 Dify（原链路）。
    RAG 作为补充：即使 RAG 无结果，仍会正常调用 Dify。

    streaming=true 时返回 SSE（text/event-stream），事件格式见 _stream_chat；
    streaming=false（默认）返回 ChatResponse JSON，行为与历史版本一致。
    """
    # 用户标识来源：优先 Authorization Bearer token（登录用户 → 用户 id；
    # 游客 token → 游客 id；无效 token 降级为 "visitor" 并告警，见
    # security.resolve_dialogue_user 决策注释）；无 token 时回退请求体 user
    # 字段（默认 "visitor"），保持向后兼容。
    effective_user = resolve_dialogue_user(authorization) or request.user

    # 软禁用/已删除账号门禁（2026-08-13 账号管理）：注册用户 id 形态的
    # 用户标识在 users 表不存在或被禁用时 403 阻断（登录/对话受控）
    await _ensure_user_active(db, effective_user)


    # 流式模式：SSE（agent 步骤 → 回答 token → done；错误回落 Dify 时带标记）
    if request.streaming:
        return StreamingResponse(
            _stream_chat(request=request, user=effective_user, db=db),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # 禁用 nginx 缓冲，保证事件逐帧到达前端（Vite dev proxy 原生透传）
                "X-Accel-Buffering": "no",
            },
        )

    # Step A: deepagents 装配层主链路（默认引擎；异常自动回落 Dify）
    if settings.DIALOGUE_ENGINE != "dify":
        try:
            result = await deepagents.chat(
                query=request.query,
                user=effective_user,
                conversation_id=request.conversation_id,
                use_rag=request.use_rag,
                rag_top_k=request.rag_top_k,
            )
            # 本地对话落库（dialogues 表，管理后台大屏/评估数据源；
            # 游客/登录用户均写入，visitor 记录按需 upsert；失败仅告警不阻断）
            try:
                await chat_agent.record_dialogue(
                    db,
                    user=effective_user,
                    conversation_id=result["conversation_id"],
                    query=request.query,
                    answer=result["answer"],
                )
            except Exception as e:
                # 双保险：落库意外异常（record_dialogue 内部已吞 DB 错误）
                # 也绝不影响对话响应（演示不翻车）
                logger.warning("对话落库失败（仅告警，不阻断对话）: %s", e)
            return ChatResponse(
                answer=result["answer"],
                conversation_id=result["conversation_id"],
                rag_context=result["rag_context"],
                rag_sources=result["rag_sources"],
                route_data=result.get("route_data"),
            )
        except Exception as e:
            # 故障回落：主链路任何异常（含 LLM 未配置）都不阻断对话，
            # 自动降级到 Dify 原链路（演示不翻车）
            logger.exception(
                "对话主链路失败，自动回落 Dify (engine=%s): %s",
                settings.DIALOGUE_ENGINE,
                e,
            )

    # Step B: Dify 链路（原逻辑，LangChain 回落路径 / DIALOGUE_ENGINE=dify）
    rag_context, rag_sources, modified_query = await _prepare_rag_context(
        query=request.query,
        use_rag=request.use_rag,
        rag_top_k=request.rag_top_k,
    )

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

def _sse(event: Dict[str, Any]) -> str:
    """序列化为 SSE data 帧（text/event-stream）。

    事件格式（type 字段与 ChatResponse 字段对齐）：

    - {"type": "step", "tool": str, "label": str}：agent 工具调用步骤
      （正在检索知识库/查询景点/推荐路线…，LangChain 链路）
    - {"type": "token", "text": str}：最终回答增量（逐字输出）
    - {"type": "done", "conversation_id": str, "rag_context": str,
       "rag_sources": list, "route_data": dict|null}：流式完成（与
       ChatResponse 字段一致；route_data 为 plan_route 工具结果，
       无路线规划时为 null，批次8 B3）
    - {"type": "error", "message": str, "fallback": bool}：链路错误；
      fallback=true 表示已切换 Dify 兜底继续输出，false 表示双链路均失败
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _stream_chat(
    request: ChatRequest,
    user: str,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """流式对话事件生成器（双轨：deepagents 装配层主链路，Dify 兜底）。

    事件序列：主链路 step → token → done；链路异常时先发
    error(fallback=true) 事件（前端据此清空部分回答）再切换 Dify 流式
    输出；双链路均失败时发 error(fallback=false) 礼貌报错并结束。

    本地对话落库（dialogues 表）：主链路 token 事件累积回答文本，
    done 事件处写入（yield done 前 await——客户端收到 done 即关闭连接
    也不丢落库；与会话存储 _append_history 的 done 前落盘同策略）。
    """
    # Step A: deepagents 装配层主链路（默认引擎；异常自动回落 Dify）
    if settings.DIALOGUE_ENGINE != "dify":
        answer_parts: List[str] = []
        try:
            async for event in deepagents.chat_stream(
                query=request.query,
                user=user,
                conversation_id=request.conversation_id,
                use_rag=request.use_rag,
                rag_top_k=request.rag_top_k,
            ):
                if event.get("type") == "token":
                    answer_parts.append(event.get("text", ""))
                elif event.get("type") == "done":
                    # 流式完成：done 事件处落库（用户消息 + 完整回答；
                    # 失败仅告警不阻断；异常回落 Dify 时主链路无 done 不落库）
                    try:
                        await chat_agent.record_dialogue(
                            db,
                            user=user,
                            conversation_id=event.get("conversation_id", ""),
                            query=request.query,
                            answer="".join(answer_parts),
                        )
                    except Exception as e:
                        # 双保险：落库意外异常（record_dialogue 内部已吞
                        # DB 错误）也绝不影响流式响应
                        logger.warning("对话落库失败（仅告警，不阻断对话）: %s", e)
                yield _sse(event)
            return
        except Exception as e:
            # 故障回落：主链路任何异常（含 LLM 未配置）都不阻断对话，
            # 自动降级到 Dify 原链路（演示不翻车）；error 事件告知前端
            logger.exception(
                "对话主链路失败，自动回落 Dify (engine=%s): %s",
                settings.DIALOGUE_ENGINE,
                e,
            )
            yield _sse(
                {
                    "type": "error",
                    "message": "主链路异常，已自动切换备用服务。",
                    "fallback": True,
                }
            )
            yield _sse(
                {
                    "type": "step",
                    "tool": "fallback",
                    "label": "正在连接备用服务…",
                }
            )

    # Step B: Dify 流式链路（LangChain 回落路径 / DIALOGUE_ENGINE=dify）
    rag_context, rag_sources, modified_query = await _prepare_rag_context(
        query=request.query,
        use_rag=request.use_rag,
        rag_top_k=request.rag_top_k,
    )
    # 会话 ID 移交：LangChain 链路生成的进程内 uuid 对 Dify 未知（透传会
    # 触发 400 conversation_not_exist），回落时传空串让 Dify 新建会话；
    # 仅透传 Dify 自己生成的 id，保证跨引擎切换后多轮对话可继续。
    fallback_conversation_id = request.conversation_id
    if chat_agent.has_conversation(request.conversation_id):
        fallback_conversation_id = ""
    try:
        async for event in _call_dify_chat_stream(
            query=modified_query,
            user=user,
            conversation_id=fallback_conversation_id,
        ):
            if event.get("type") == "done":
                # 与 ChatResponse 契约对齐：透出 RAG 来源信息
                event = {
                    **event,
                    "rag_context": rag_context,
                    "rag_sources": rag_sources,
                }
            yield _sse(event)
    except Exception:
        logger.exception("Dify 流式对话失败")
        yield _sse(
            {
                "type": "error",
                "message": "抱歉，AI 服务暂时不可用，请稍后再试。",
                "fallback": False,
            }
        )


async def _prepare_rag_context(
    query: str,
    use_rag: bool,
    rag_top_k: int,
) -> Tuple[str, List[Dict[str, Any]], str]:
    """RAG 预检索：返回 (rag_context, rag_sources, modified_query)。

    RAG 检索失败或未启用时返回原始 query（不阻断对话）；检索成功时将
    知识库上下文注入 query 供 Dify 回答。流式与非流式链路共用。
    """
    if not use_rag:
        return "", [], query
    try:
        results = await search_documents(query=query, top_k=rag_top_k)
    except Exception as e:
        logger.warning("RAG retrieval failed, proceeding without RAG: %s", e)
        return "", [], query
    if not results:
        return "", [], query
    rag_context = build_rag_context(results)
    rag_sources = [
        {"source": r["source"], "score": r["score"]}
        for r in results
    ]
    # 将 RAG 上下文注入 query
    modified_query = (
        f"{rag_context}\n\n"
        f"【用户问题】\n{query}\n\n"
        f"请基于以上知识库内容回答用户问题。"
        f"如果知识库内容不足以回答，请根据你的知识诚实告知。"
    )
    logger.info("RAG context injected, %d sources", len(rag_sources))
    return rag_context, rag_sources, modified_query


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
