"""管理员 agent 管理工具集（批次6 B4，X-Admin-Token 鉴权后端）。

工具面（全部复用既有 service，不重复实现存储逻辑）：

- **知识库**：query_knowledge / create_knowledge / update_knowledge /
  delete_knowledge（复用 knowledge_service；删除为两阶段二次确认）
- **记忆**：query_user_memories / delete_user_memory（复用 memory_service；
  管理员例外：跨用户可用；删除为两阶段二次确认）
- **公告**：list_announcements / set_announcement_status（复用
  announcement_service，发布/下架）
- **统计**：query_statistics（复用 admin_service 聚合 + DB 直查，只读）
- **RAG**：rebuild_rag_index（复用 rag_service 索引重建，documents +
  knowledge 两命名空间）
- **首页数据**：view_home_highlights（复用 home_service，只读）
- **二次确认**：confirm_operation（两阶段契约唯一执行入口，见 confirm.py）

**admin 上下文校验**：每个工具首个参数为 `Annotated[bool,
InjectedState("is_admin")]`——langgraph 从图状态注入（模型侧 schema 自动
隐藏该参数），由路由层 X-Admin-Token 鉴权后置 True；工具调用前一律
`_require_admin` 校验，非管理员上下文直接抛错（图会把错误作为
ToolMessage 回给模型，不越权执行）。

**两阶段删除契约**（删除类工具）：
1. 首次调用：只读定位目标 → `confirm.create_pending` 登记待确认项（生成
   operation_id + 目标摘要），返回 `{"status": "pending_confirmation", ...}`
   且**不执行删除**——模型必须把 operation_id 展示给管理员并调用
   confirm_operation 确认
2. `confirm_operation(operation_id)`：`confirm.consume` 取出待确认项
   （TTL 过期/重复确认返回错误）→ 经 `_CONFIRM_EXECUTORS` 派发实际执行
   （执行前重新定位目标，已被他人删除则如实报告）→ 审计日志记录最终执行

**审计**：全部写操作（增/删/改/状态切换/索引重建）经 `_audit_log` 记
logger（operator=admin-agent + 工具名 + 目标 + 结果），与 B5 记忆管理
审计风格一致（DB 审计表未引入，日志先行）。
"""
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.evaluation import Evaluation
from app.models.knowledge import KnowledgeBase
from app.models.user import User
from app.schemas.announcement import AnnouncementStatusUpdateRequest
from app.schemas.knowledge import KnowledgeCreateRequest, KnowledgeItem, KnowledgeUpdateRequest
from app.services import (
    admin_service,
    announcement_service,
    home_service,
    knowledge_service,
    memory_service,
    rag_service,
)
from app.services.admin_agent import confirm

logger = logging.getLogger(__name__)


# ── 工具公共辅助 ──────────────────────────────────────────────────────────


def _require_admin(is_admin: bool) -> None:
    """工具调用前 admin 上下文校验（图状态注入；非管理员直接抛错拒绝）。"""
    if not is_admin:
        raise PermissionError("管理员上下文缺失：本工具仅限管理员 agent 调用")


def _audit_log(tool: str, target: str, action: str, detail: str = "") -> None:
    """写操作审计日志（operator=admin-agent，与 B5 记忆管理审计风格一致）。

    Args:
        tool: 工具名（如 create_knowledge）
        target: 目标标识（如 "knowledge#3《灵山简介》" / "memory:<id>"）
        action: 动作（create/update/delete/publish/unpublish/rebuild）
        detail: 结果摘要（如 "ok deleted=1" / "not_found"）
    """
    logger.info(
        "管理员Agent写操作: operator=admin-agent tool=%s target=%s action=%s result=%s",
        tool,
        target,
        action,
        detail,
    )


def _json_dumps(data: Any) -> str:
    """工具返回值 JSON 序列化（datetime 等转字符串兜底，中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def _empty_result(message: str) -> str:
    """通用空结果（工具失败/无数据时返回，模型据此如实回答）。"""
    return _json_dumps({"message": message})


@asynccontextmanager
async def _db_session():
    """工具私有数据库会话（提交/回滚/关闭由本上下文统一管理）。

    每个工具调用独立会话（与路由层 get_db 隔离）：写操作成功自动提交，
    异常自动回滚并上抛（图捕获为 ToolMessage 回给模型）。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── 知识库工具 ────────────────────────────────────────────────────────────


@tool
async def query_knowledge(
    keyword: str = "",
    category: str = "",
    page: int = 1,
    page_size: int = 10,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询知识库条目（讲解词/FAQ/景点资料，只读）。

    可按标题/内容关键词、分类筛选分页查询；返回条目 JSON 列表
    （id/title/category/content/tags）。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            result = await knowledge_service.list_knowledge(
                db,
                category=category or None,
                keyword=keyword or None,
                page=page,
                page_size=page_size,
            )
    except Exception as e:
        logger.warning("query_knowledge 工具失败: %s", e)
        return _empty_result("知识库查询暂时不可用")
    items = [i.model_dump() for i in result.items]
    return _json_dumps(
        {
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
            "items": items,
        }
    )


@tool
async def create_knowledge(
    title: str,
    category: str,
    content: str,
    tags: str = "",
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """新增一条知识库条目（写操作，系统自动记录审计日志）。

    参数：title 标题、category 分类（如 景点资料/FAQ）、content 正文、
    tags 可选标签（逗号分隔）。创建后自动同步向量索引。"""
    _require_admin(is_admin)
    if not title.strip() or not content.strip():
        return _empty_result("标题与正文不能为空")
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        request = KnowledgeCreateRequest(
            title=title.strip(),
            category=category.strip() or "景点资料",
            content=content.strip(),
            tags=tag_list,
        )
        async with _db_session() as db:
            item = await knowledge_service.create_knowledge(db, request)
    except Exception as e:
        logger.warning("create_knowledge 工具失败: %s", e)
        return _empty_result("知识条目创建失败")
    _audit_log(
        "create_knowledge",
        f"knowledge#{item.id}《{item.title}》",
        "create",
        f"ok id={item.id}",
    )
    return _json_dumps({"status": "ok", "item": item.model_dump()})


@tool
async def update_knowledge(
    kb_id: int,
    title: str = "",
    category: str = "",
    content: str = "",
    tags: str = "",
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """更新一条知识库条目（写操作，部分更新：仅更新非空字段）。

    kb_id 为目标条目 id；title/category/content/tags 只更新传入的非空值，
    tags 传空字符串表示不更新标签。更新后自动同步向量索引。"""
    _require_admin(is_admin)
    try:
        # 部分更新：仅把非空字段传入（避免显式 None 覆盖既有字段）
        update_data: Dict[str, Any] = {}
        if title.strip():
            update_data["title"] = title.strip()
        if category.strip():
            update_data["category"] = category.strip()
        if content.strip():
            update_data["content"] = content.strip()
        if tags:
            update_data["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if not update_data:
            return _json_dumps({"status": "error", "message": "没有可更新的字段（title/category/content/tags 至少提供一个非空值）"})
        request = KnowledgeUpdateRequest.model_validate(update_data)
        async with _db_session() as db:
            kb = await knowledge_service.update_knowledge(db, kb_id, request)
    except Exception as e:
        logger.warning("update_knowledge 工具失败: %s", e)
        return _empty_result("知识条目更新失败")
    if kb is None:
        return _json_dumps({"status": "error", "message": f"知识条目 #{kb_id} 不存在"})
    _audit_log(
        "update_knowledge",
        f"knowledge#{kb.id}《{kb.title}》",
        "update",
        f"ok id={kb.id}",
    )
    return _json_dumps(
        {
            "status": "ok",
            "item": KnowledgeItem.model_validate(kb).model_dump(),
        }
    )


@tool
async def delete_knowledge(
    kb_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """删除一条知识库条目（**删除类操作，两阶段二次确认**）。

    首次调用只登记待确认项并返回 operation_id（不执行删除）；确认删除
    请再调用 confirm_operation(operation_id)。operation_id 短时有效
    （默认 120 秒），过期需重新发起。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            kb = await knowledge_service.get_knowledge(db, kb_id)
    except Exception as e:
        logger.warning("delete_knowledge 工具定位目标失败: %s", e)
        return _empty_result("知识条目查询暂时不可用")
    if kb is None:
        return _json_dumps({"status": "error", "message": f"知识条目 #{kb_id} 不存在"})
    op = confirm.create_pending(
        "delete_knowledge",
        {"kb_id": kb_id},
        f"知识条目 #{kb.id}《{kb.title}》（分类：{kb.category}）",
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "delete_knowledge",
            "summary": op.summary,
            "message": "删除为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行删除",
        }
    )


async def _execute_delete_knowledge(params: dict) -> str:
    """confirm_operation 派发：实际执行知识条目删除（重新定位目标 + 审计）。"""
    kb_id = int(params.get("kb_id", 0))
    try:
        async with _db_session() as db:
            kb = await knowledge_service.get_knowledge(db, kb_id)
            if kb is None:
                return _json_dumps(
                    {"status": "error", "message": f"知识条目 #{kb_id} 已不存在（可能已被其他操作删除）"}
                )
            title = kb.title
            deleted = await knowledge_service.delete_knowledge(db, kb_id)
    except Exception as e:
        logger.warning("confirm 执行 delete_knowledge 失败: %s", e)
        return _empty_result("知识条目删除失败")
    _audit_log(
        "delete_knowledge",
        f"knowledge#{kb_id}《{title}》",
        "delete",
        f"ok deleted={1 if deleted else 0}",
    )
    return _json_dumps({"status": "ok", "deleted": kb_id, "title": title})


# ── 记忆工具（管理员例外：跨用户可用） ────────────────────────────────────


@tool
async def query_user_memories(
    user: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询某注册用户的长期记忆列表（只读，管理员例外：跨用户可用）。

    user 为注册用户 id（数字字符串）；游客/匿名用户无长期记忆（返回空）。
    返回条目 JSON 列表（memory_id/fact_type/content/timestamp），
    memory_id 可用于 delete_user_memory。"""
    _require_admin(is_admin)
    if not user.strip():
        return _empty_result("请提供用户 id")
    try:
        entries = await memory_service.list_user_memories(user.strip())
    except Exception as e:
        logger.warning("query_user_memories 工具失败: %s", e)
        return _empty_result("记忆查询暂时不可用")
    if not entries:
        return _json_dumps({"user": user.strip(), "total": 0, "items": []})
    items = [
        {
            "memory_id": memory_service.memory_id_of(e),
            "fact_type": e.fact_type,
            "content": e.content,
            "timestamp": e.timestamp,
        }
        for e in entries
    ]
    return _json_dumps({"user": user.strip(), "total": len(items), "items": items})


@tool
async def delete_user_memory(
    memory_id: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """删除一条用户长期记忆（**删除类操作，两阶段二次确认**；管理员例外）。

    首次调用只登记待确认项并返回 operation_id（不执行删除）；确认删除
    请再调用 confirm_operation(operation_id)。operation_id 短时有效
    （默认 120 秒），过期需重新发起。"""
    _require_admin(is_admin)
    if not memory_id.strip():
        return _empty_result("请提供要删除的记忆 id")
    try:
        entry = await memory_service.find_memory_by_id(memory_id.strip())
    except Exception as e:
        logger.warning("delete_user_memory 工具定位目标失败: %s", e)
        return _empty_result("记忆查询暂时不可用")
    if entry is None:
        return _json_dumps({"status": "error", "message": f"记忆 {memory_id} 不存在"})
    op = confirm.create_pending(
        "delete_user_memory",
        {"memory_id": memory_id.strip()},
        f"用户 {entry.user} 的记忆（{entry.fact_type}）：{entry.content[:50]}",
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "delete_user_memory",
            "summary": op.summary,
            "message": "删除为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行删除",
        }
    )


async def _execute_delete_user_memory(params: dict) -> str:
    """confirm_operation 派发：实际执行记忆删除（重新定位目标 + 审计）。"""
    memory_id = str(params.get("memory_id", ""))
    try:
        entry = await memory_service.find_memory_by_id(memory_id)
        if entry is None:
            return _json_dumps(
                {"status": "error", "message": f"记忆 {memory_id} 已不存在（可能已被其他操作删除）"}
            )
        user = entry.user
        content = entry.content
        result = await memory_service.delete_user_memory(user, memory_id)
    except Exception as e:
        logger.warning("confirm 执行 delete_user_memory 失败: %s", e)
        return _empty_result("记忆删除失败")
    _audit_log(
        "delete_user_memory",
        f"memory:{memory_id} (user={user})",
        "delete",
        f"ok deleted={result.get('deleted', 0)}",
    )
    return _json_dumps(
        {
            "status": "ok",
            "deleted": result.get("deleted", 0),
            "user": user,
            "content": content[:50],
        }
    )


# ── 公告工具 ──────────────────────────────────────────────────────────────


@tool
async def list_announcements(
    status: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 10,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询公告列表（只读）。

    可按状态（published 已发布 / draft 草稿）与标题/内容关键词筛选分页；
    返回条目 JSON 列表（id/title/content/status/pinned/created_at/updated_at）。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            result = await announcement_service.list_announcements(
                db,
                status=status or None,
                keyword=keyword or None,
                page=page,
                page_size=page_size,
            )
    except Exception as e:
        logger.warning("list_announcements 工具失败: %s", e)
        return _empty_result("公告查询暂时不可用")
    return _json_dumps(
        {
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
            "items": [i.model_dump() for i in result.items],
        }
    )


@tool
async def set_announcement_status(
    announcement_id: int,
    status: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """发布/下架公告（写操作，系统自动记录审计日志）。

    status 取 published（发布）或 draft（下架为草稿）。"""
    _require_admin(is_admin)
    if status not in ("published", "draft"):
        return _empty_result("status 仅支持 published（发布）或 draft（下架）")
    try:
        request = AnnouncementStatusUpdateRequest(status=status)
        async with _db_session() as db:
            item = await announcement_service.set_announcement_status(
                db, announcement_id, request
            )
    except Exception as e:
        logger.warning("set_announcement_status 工具失败: %s", e)
        return _empty_result("公告状态更新失败")
    if item is None:
        return _json_dumps({"status": "error", "message": f"公告 #{announcement_id} 不存在"})
    action = "publish" if status == "published" else "unpublish"
    _audit_log(
        "set_announcement_status",
        f"announcement#{item.id}《{item.title}》",
        action,
        f"ok status={status}",
    )
    return _json_dumps(
        {
            "status": "ok",
            "id": item.id,
            "title": item.title,
            "new_status": item.status,
        }
    )


# ── 统计工具（只读） ──────────────────────────────────────────────────────


@tool
async def query_statistics(
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询系统运营数据（只读）：对话量/注册用户量/量化评估分布。

    返回：total_dialogues（对话总量）、total_users（注册用户量）、
    total_evaluations（已评估对话量）、sentiment_distribution（情感分布
    positive/negative/neutral）、satisfaction_distribution（满意度 1-5 分布）、
    hot_issues（热点关注 TOP10）、spot_heatmap（景点热度 TOP10）。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            dashboard = await admin_service.get_dashboard_stats(db)
            report = await admin_service.get_report_data(db)
            user_count = (
                await db.execute(select(func.count(User.id)))
            ).scalar_one()
            eval_count = (
                await db.execute(select(func.count(Evaluation.id)))
            ).scalar_one()
    except Exception as e:
        logger.warning("query_statistics 工具失败: %s", e)
        return _empty_result("统计数据查询暂时不可用")
    return _json_dumps(
        {
            "total_dialogues": dashboard.total_conversations,
            "total_users": user_count,
            "total_evaluations": eval_count,
            "sentiment_distribution": {
                item.label.lower(): item.count for item in report.sentiment_stats
            },
            "satisfaction_distribution": {
                str(k): v for k, v in dashboard.satisfaction_distribution.items()
            },
            "hot_issues": [i.model_dump() for i in dashboard.hot_issues],
            "spot_heatmap": [i.model_dump() for i in dashboard.spot_heatmap],
        }
    )


# ── RAG 重建索引工具 ──────────────────────────────────────────────────────


@tool
async def rebuild_rag_index(
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """重建 RAG 知识库向量索引（写操作，系统自动记录审计日志）。

    全量重建 documents 命名空间（doc/景区知识库/ 文档）与 knowledge
    命名空间（数据库知识条目），使向量索引与当前数据一致；
    索引重建后知识检索缓存自动失效。"""
    _require_admin(is_admin)
    try:
        doc_result = await rag_service.index_documents(namespace="documents")
        async with _db_session() as db:
            rows = (
                await db.execute(
                    select(
                        KnowledgeBase.id,
                        KnowledgeBase.title,
                        KnowledgeBase.category,
                        KnowledgeBase.content,
                    )
                )
            ).all()
        entries = [
            {"id": r.id, "title": r.title, "category": r.category, "content": r.content}
            for r in rows
        ]
        kb_result = await rag_service.index_knowledge_entries(entries)
    except Exception as e:
        logger.warning("rebuild_rag_index 工具失败: %s", e)
        return _empty_result("索引重建失败")
    _audit_log(
        "rebuild_rag_index",
        "rag:documents+knowledge",
        "rebuild",
        f"ok docs={doc_result.get('documents', 0)} knowledge={kb_result.get('documents', 0)}",
    )
    return _json_dumps(
        {
            "status": "ok",
            "documents": {
                "status": doc_result.get("status"),
                "documents": doc_result.get("documents", 0),
                "chunks": doc_result.get("chunks", 0),
                "embedding_backend": doc_result.get("embedding_backend"),
            },
            "knowledge": {
                "status": kb_result.get("status"),
                "documents": kb_result.get("documents", 0),
                "embedding_backend": kb_result.get("embedding_backend"),
            },
        }
    )


# ── 首页数据工具（只读） ──────────────────────────────────────────────────


@tool
async def view_home_highlights(
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查看首页热门景点与推荐路线（只读）。

    数据来自 app/data/spots.json 与 routes.json（首页活数据源）；
    返回景点/路线 JSON 列表。"""
    _require_admin(is_admin)
    try:
        result = await home_service.get_home_highlights(
            spots_limit=5, routes_limit=5
        )
    except Exception as e:
        logger.warning("view_home_highlights 工具失败: %s", e)
        return _empty_result("首页数据读取暂时不可用")
    return _json_dumps(
        {
            "spots": [s.model_dump() for s in result.spots],
            "routes": [r.model_dump() for r in result.routes],
        }
    )


# ── 二次确认工具（两阶段契约唯一执行入口） ───────────────────────────────


_CONFIRM_EXECUTORS: Dict[str, Callable[[dict], Awaitable[str]]] = {
    "delete_knowledge": _execute_delete_knowledge,
    "delete_user_memory": _execute_delete_user_memory,
}


@tool
async def confirm_operation(
    operation_id: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """确认并执行一条待确认的删除操作（两阶段二次确认契约）。

    删除类工具（delete_knowledge / delete_user_memory）首次调用只返回
    pending_confirmation 结果（含 operation_id），**必须**再调用本工具
    传入该 operation_id 才会真正执行删除；operation_id 短时有效
    （默认 120 秒），过期/重复确认会返回错误，需重新发起删除。"""
    _require_admin(is_admin)
    if not operation_id.strip():
        return _empty_result("请提供 operation_id")
    op = confirm.consume(operation_id.strip())
    if op is None:
        return _json_dumps(
            {
                "status": "error",
                "message": "待确认操作不存在或已过期（未确认的操作已自动失效，请重新发起删除请求）",
            }
        )
    executor = _CONFIRM_EXECUTORS.get(op.tool)
    if executor is None:
        logger.error("待确认操作无执行器: operation_id=%s tool=%s", op.operation_id, op.tool)
        return _json_dumps({"status": "error", "message": f"未知操作类型 {op.tool}"})
    try:
        result = await executor(op.params)
    except Exception as e:
        logger.warning("confirm_operation 执行失败: operation_id=%s tool=%s err=%s",
                       op.operation_id, op.tool, e)
        return _empty_result("操作执行失败")
    return result


# 工具全量列表（agent 装配用，见 agent.py）
ADMIN_TOOLS: List[Any] = [
    query_knowledge,
    create_knowledge,
    update_knowledge,
    delete_knowledge,
    query_user_memories,
    delete_user_memory,
    list_announcements,
    set_announcement_status,
    query_statistics,
    rebuild_rag_index,
    view_home_highlights,
    confirm_operation,
]
