"""管理员 agent 对话路由（批次6 B4，X-Admin-Token 鉴权）。

- `POST /api/admin/agent/chat` — 非流式对话（JSON：answer/conversation_id）
- `POST /api/admin/agent/chat/stream` — 流式对话（SSE，事件契约与用户侧
  一致：step → token → done；管理端页面步骤可视化复用同一契约）

鉴权：两个端点均依赖 `verify_admin_token`（X-Admin-Token 请求头）——
admin 上下文由路由层鉴权后置 True 注入 agent 图状态（工具侧仍有独立
校验双保险，见 app/services/admin_agent/tools.py）。

错误处理：LLM 未配置/主链路异常——非流式返回 503（管理端明确报错，
无 Dify 兜底）；流式先发 error(fallback=false) 事件后结束。
"""
import json
import logging
from typing import Any, AsyncIterator, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.llm import LLMConfigError
from app.routers.deps import verify_admin_token
from app.services import admin_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["管理员Agent"])


class AdminAgentChatRequest(BaseModel):
    """管理 agent 对话请求。"""

    query: str = Field(..., description="管理员指令", min_length=1, max_length=5000)
    conversation_id: str = Field(
        default="", description="会话 ID（透传回显；留空自动生成）"
    )


def _sse(event: Dict[str, Any]) -> str:
    """序列化为 SSE data 帧（text/event-stream，契约同对话路由）。

    - {"type": "step", "tool": str, "label": str}：工具调用步骤
    - {"type": "token", "text": str}：回答增量
    - {"type": "done", "conversation_id": str}：流式完成
    - {"type": "error", "message": str, "fallback": bool}：链路错误
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def admin_agent_chat(
    request: AdminAgentChatRequest,
    _: str = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """管理 agent 非流式对话（X-Admin-Token 鉴权）。

    管理端页面调用：向 agent 下达管理指令（查询/新增/修改/删除知识库、
    查询/删除用户记忆、发布/下架公告、统计查询、RAG 重建、首页数据查看），
    删除类操作按两阶段二次确认契约执行（首次返回待确认，确认后真正删除）。
    """
    try:
        result = await admin_agent.chat(
            query=request.query,
            conversation_id=request.conversation_id,
        )
    except LLMConfigError as e:
        logger.error("管理员Agent对话失败（LLM 未配置）: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI 模型服务未配置，管理员 Agent 不可用",
        )
    except Exception as e:
        logger.exception("管理员Agent对话异常: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"管理员 Agent 服务暂时不可用: {e}",
        )
    return result


@router.post("/chat/stream")
async def admin_agent_chat_stream(
    request: AdminAgentChatRequest,
    _: str = Depends(verify_admin_token),
) -> StreamingResponse:
    """管理 agent 流式对话（SSE，X-Admin-Token 鉴权）。

    事件契约与用户侧一致（step/token/done），管理端页面可直接复用用户侧
    步骤可视化组件（🔍 工具调用步骤气泡 + 逐字输出）；done 携带
    conversation_id。链路异常时发 error(fallback=false) 事件后结束
    （管理端无 Dify 兜底，明确报错）。
    """
    return StreamingResponse(
        _stream_admin_chat(request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 禁用 nginx 缓冲，保证事件逐帧到达前端（与对话路由一致）
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_admin_chat(
    request: AdminAgentChatRequest,
) -> AsyncIterator[str]:
    """流式对话事件生成器（主链路异常发 error 事件后结束）。"""
    try:
        async for event in admin_agent.chat_stream(
            query=request.query,
            conversation_id=request.conversation_id,
        ):
            yield _sse(event)
    except LLMConfigError as e:
        logger.error("管理员Agent流式对话失败（LLM 未配置）: %s", e)
        yield _sse(
            {
                "type": "error",
                "message": "AI 模型服务未配置，管理员 Agent 不可用。",
                "fallback": False,
            }
        )
    except Exception as e:
        logger.exception("管理员Agent流式对话异常: %s", e)
        yield _sse(
            {
                "type": "error",
                "message": f"管理员 Agent 服务暂时不可用: {e}",
                "fallback": False,
            }
        )
