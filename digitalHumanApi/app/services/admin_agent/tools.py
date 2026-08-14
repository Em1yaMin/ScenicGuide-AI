"""管理员 agent 管理工具集（批次6 B4，X-Admin-Token 鉴权后端）。

工具面（全部复用既有 service，不重复实现存储逻辑）：

- **知识库**：query_knowledge / create_knowledge / update_knowledge /
  delete_knowledge（复用 knowledge_service；删除为两阶段二次确认）
- **记忆**：query_user_memories / delete_user_memory（复用 memory_service；
  管理员例外：跨用户可用；删除为两阶段二次确认）
- **公告**：list_announcements / create_announcement / update_announcement /
  set_announcement_status / delete_announcement（复用 announcement_service；
  创建默认草稿、发布后才在首页/访客端可见，删除为两阶段二次确认）
- **统计**：query_statistics（复用 admin_service 聚合 + DB 直查，只读）
- **RAG**：rebuild_rag_index（复用 rag_service 索引重建，documents +
  knowledge 两命名空间）
- **首页数据**：view_home_highlights（复用 home_service，只读）
- **账号管理**（2026-08-13）：list_users / get_user_detail（只读）、
  create_user（写）、enable_user（写，直接执行）、**disable_user /
  reset_user_password / delete_user（敏感操作，两阶段二次确认）**；
  删除/禁用/重置密码全部二次确认、**单目标严禁批量**、系统管理员账号
  （ADMIN_USERNAME）受保护不可禁用/删除；全部敏感操作落库审计
  （admin_audit_logs 表，operator=admin-agent）
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
from app.models.app_setting import (
    DIGITAL_HUMAN_CHARACTERS,
    DIGITAL_HUMAN_CHARACTER_KEY,
)
from app.models.evaluation import Evaluation
from app.models.knowledge import KnowledgeBase
from app.models.user import User
from app.schemas.announcement import (
    AnnouncementCreateRequest,
    AnnouncementItem,
    AnnouncementStatusUpdateRequest,
    AnnouncementUpdateRequest,
)
from app.schemas.knowledge import KnowledgeCreateRequest, KnowledgeItem, KnowledgeUpdateRequest
from app.schemas.user_admin import AdminUserCreateRequest
from app.services import (
    admin_audit_service,
    admin_service,
    announcement_service,
    auth_service,
    home_service,
    knowledge_service,
    dh_character_service,
    memory_service,
    rag_service,
    user_admin_service,
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
async def create_announcement(
    title: str,
    content: str,
    status: str = "draft",
    pinned: bool = False,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """新建公告（写操作，系统自动记录审计日志）。

    参数：title 标题、content 正文、status 状态（draft 草稿默认 /
    published 发布）、pinned 是否置顶（默认否）。**新建默认为草稿，只有发布
    （published）后才在首页/访客端可见**；如需立即上首页，请直接传
    status=published（或创建后调用 set_announcement_status 发布）。"""
    _require_admin(is_admin)
    if not title.strip() or not content.strip():
        return _empty_result("标题与正文不能为空")
    if status not in ("published", "draft"):
        return _empty_result("status 仅支持 published（发布）或 draft（草稿）")
    try:
        request = AnnouncementCreateRequest(
            title=title.strip(),
            content=content.strip(),
            status=status,
            pinned=pinned,
        )
        async with _db_session() as db:
            item = await announcement_service.create_announcement(db, request)
    except Exception as e:
        logger.warning("create_announcement 工具失败: %s", e)
        return _empty_result("公告创建失败")
    _audit_log(
        "create_announcement",
        f"announcement#{item.id}《{item.title}》",
        "create",
        f"ok id={item.id} status={item.status}",
    )
    return _json_dumps({"status": "ok", "item": item.model_dump()})


@tool
async def update_announcement(
    announcement_id: int,
    title: str = "",
    content: str = "",
    status: str = "",
    pinned: Optional[bool] = None,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """更新公告（写操作，部分更新：仅更新传入的非空字段）。

    announcement_id 为目标公告 id；title/content 只更新传入的非空值；
    status 取 published（发布）/draft（草稿）；pinned 传布尔值更新置顶。"""
    _require_admin(is_admin)
    if status and status not in ("published", "draft"):
        return _empty_result("status 仅支持 published（发布）或 draft（草稿）")
    try:
        # 部分更新：仅把传入字段写入（避免显式 None 覆盖既有字段）
        update_data: Dict[str, Any] = {}
        if title.strip():
            update_data["title"] = title.strip()
        if content.strip():
            update_data["content"] = content.strip()
        if status:
            update_data["status"] = status
        if pinned is not None:
            update_data["pinned"] = pinned
        if not update_data:
            return _json_dumps(
                {"status": "error", "message": "没有可更新的字段（title/content/status/pinned 至少提供一个）"}
            )
        request = AnnouncementUpdateRequest.model_validate(update_data)
        async with _db_session() as db:
            item = await announcement_service.update_announcement(
                db, announcement_id, request
            )
    except Exception as e:
        logger.warning("update_announcement 工具失败: %s", e)
        return _empty_result("公告更新失败")
    if item is None:
        return _json_dumps({"status": "error", "message": f"公告 #{announcement_id} 不存在"})
    _audit_log(
        "update_announcement",
        f"announcement#{item.id}《{item.title}》",
        "update",
        f"ok id={item.id}",
    )
    return _json_dumps(
        {"status": "ok", "item": AnnouncementItem.model_validate(item).model_dump()}
    )


@tool
async def delete_announcement(
    announcement_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """删除一条公告（**删除类操作，两阶段二次确认**）。

    首次调用只登记待确认项并返回 operation_id（不执行删除）；确认删除
    请再调用 confirm_operation(operation_id)。operation_id 短时有效
    （默认 120 秒），过期需重新发起。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            item = await announcement_service.get_announcement(db, announcement_id)
    except Exception as e:
        logger.warning("delete_announcement 工具定位目标失败: %s", e)
        return _empty_result("公告查询暂时不可用")
    if item is None:
        return _json_dumps({"status": "error", "message": f"公告 #{announcement_id} 不存在"})
    op = confirm.create_pending(
        "delete_announcement",
        {"announcement_id": announcement_id},
        f"公告 #{item.id}《{item.title}》（状态：{item.status}）",
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "delete_announcement",
            "summary": op.summary,
            "message": "删除为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行删除",
        }
    )


async def _execute_delete_announcement(params: dict) -> str:
    """confirm_operation 派发：实际执行公告删除（重新定位目标 + 审计）。"""
    announcement_id = int(params.get("announcement_id", 0))
    try:
        async with _db_session() as db:
            item = await announcement_service.get_announcement(db, announcement_id)
            if item is None:
                return _json_dumps(
                    {"status": "error", "message": f"公告 #{announcement_id} 已不存在（可能已被其他操作删除）"}
                )
            title = item.title
            deleted = await announcement_service.delete_announcement(db, announcement_id)
    except Exception as e:
        logger.warning("confirm 执行 delete_announcement 失败: %s", e)
        return _empty_result("公告删除失败")
    _audit_log(
        "delete_announcement",
        f"announcement#{announcement_id}《{title}》",
        "delete",
        f"ok deleted={1 if deleted else 0}",
    )
    return _json_dumps({"status": "ok", "deleted": announcement_id, "title": title})


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


# ── 数字人形象工具（2026-08：形象管理后端部分） ────────────────────────


@tool
async def list_character_models(
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询数字人形象列表（只读）。

    返回当前形象与全部可选形象（与 ADH 本地数字人 public/characters
    目录一致）；可用于 set_character_model 前确认可选范围。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            current = await dh_character_service.get_character(db)
    except Exception as e:
        logger.warning("list_character_models 工具失败: %s", e)
        return _empty_result("数字人形象查询暂时不可用")
    return _json_dumps(
        {
            "status": "ok",
            "current": current,
            "available": DIGITAL_HUMAN_CHARACTERS,
            "message": (
                f"当前形象：{current}，可选：{'/'.join(DIGITAL_HUMAN_CHARACTERS)}"
            ),
        }
    )


@tool
async def set_character_model(
    character: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """设置数字人形象（写操作，系统自动记录审计日志）。

    character 为形象名，必须在本工具可选列表内（如 Hiyori/Chitose/…）；
    非法形象名返回错误。设置后访客端/ADH 读取的新形象立即生效。"""
    _require_admin(is_admin)
    name = character.strip()
    if name not in DIGITAL_HUMAN_CHARACTERS:
        return _json_dumps(
            {
                "status": "error",
                "message": (
                    f"形象 {name!r} 不在可选列表内（可选：{'/'.join(DIGITAL_HUMAN_CHARACTERS)}）"
                ),
            }
        )
    try:
        async with _db_session() as db:
            saved = await dh_character_service.set_character(db, name)
    except Exception as e:
        logger.warning("set_character_model 工具失败: %s", e)
        return _empty_result("数字人形象设置失败")
    _audit_log(
        "set_character_model",
        DIGITAL_HUMAN_CHARACTER_KEY,
        "update",
        f"ok character={saved}",
    )
    return _json_dumps(
        {
            "status": "ok",
            "character": saved,
            "available": DIGITAL_HUMAN_CHARACTERS,
            "message": f"数字人形象已设置为 {saved}",
        }
    )


# ── 账号管理工具（2026-08-13：敏感操作二次确认 + 落库审计） ──────────────


@tool
async def list_users(
    keyword: str = "",
    page: int = 1,
    page_size: int = 10,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询注册账号列表（只读）。

    可按用户名/昵称关键词搜索分页；返回账号 JSON 列表（id/username/
    nickname/is_disabled/对话数/记忆条数/注册时间/最后登录时间）。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            result = await user_admin_service.list_users(
                db,
                keyword=keyword.strip() or None,
                page=page,
                page_size=page_size,
            )
    except Exception as e:
        logger.warning("list_users 工具失败: %s", e)
        return _empty_result("账号列表查询暂时不可用")
    return _json_dumps(
        {
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
            "items": [i.model_dump() for i in result.items],
        }
    )


@tool
async def get_user_detail(
    user_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """查询单个账号详情（只读）。

    user_id 为目标账号 id；返回 id/username/nickname/is_disabled/对话数/
    记忆条数/注册时间/最后登录时间。账号不存在返回错误。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            item = await user_admin_service.get_user_item(db, user_id)
    except Exception as e:
        logger.warning("get_user_detail 工具失败: %s", e)
        return _empty_result("账号查询暂时不可用")
    if item is None:
        return _json_dumps({"status": "error", "message": f"账号 #{user_id} 不存在"})
    return _json_dumps({"status": "ok", "user": item.model_dump()})


@tool
async def create_user(
    username: str,
    password: str,
    nickname: str = "",
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """创建账号（写操作，系统自动记录审计日志）。

    参数：username 用户名（3-32 位字母/数字/下划线）、password 初始密码
    （至少 6 位）、nickname 昵称（可选）。用户名重复返回错误。"""
    _require_admin(is_admin)
    if not username.strip() or not password:
        return _json_dumps({"status": "error", "message": "用户名与初始密码不能为空"})
    if len(password) < 6:
        return _json_dumps({"status": "error", "message": "初始密码至少 6 位"})
    try:
        request = AdminUserCreateRequest(
            username=username.strip(),
            password=password,
            nickname=nickname.strip() or None,
        )
        async with _db_session() as db:
            user = await user_admin_service.create_user(db, request)
            await admin_audit_service.record_audit(
                db,
                operator="admin-agent",
                action=admin_audit_service.ACTION_CREATE,
                target_user_id=user.id,
                target_username=user.username,
                detail="ok",
            )
            item = await user_admin_service.get_user_item(db, user.id)
    except auth_service.AuthError as e:
        # 用户名已存在 / 密码超长等业务校验失败：规范错误信封
        return _json_dumps({"status": "error", "message": e.message})
    except Exception as e:
        logger.warning("create_user 工具失败: %s", e)
        return _empty_result("账号创建失败（用户名可能已存在或不符合规则）")
    _audit_log(
        "create_user",
        f"user#{item.id} {item.username}",
        "create",
        f"ok id={item.id}",
    )
    return _json_dumps({"status": "ok", "user": item.model_dump()})


@tool
async def disable_user(
    user_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """禁用账号（**敏感操作，两阶段二次确认**；单目标，禁止批量）。

    首次调用只登记待确认项并返回 operation_id（**并未实际禁用**）；确认
    请再调用 confirm_operation(operation_id)。operation_id 短时有效（默认
    120 秒），过期需重新发起。系统管理员账号（ADMIN_USERNAME）受保护，
    不可禁用。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            user = await db.get(User, user_id)
    except Exception as e:
        logger.warning("disable_user 工具定位目标失败: %s", e)
        return _empty_result("账号查询暂时不可用")
    if user is None:
        return _json_dumps({"status": "error", "message": f"账号 #{user_id} 不存在"})
    if user.is_disabled:
        return _json_dumps({"status": "error", "message": f"账号 {user.username} 已处于禁用状态"})
    if user_admin_service.is_protected_username(user.username):
        return _json_dumps(
            {"status": "error", "message": f"系统管理员账号「{user.username}」受保护，不可禁用"}
        )
    op = confirm.create_pending(
        "disable_user",
        {"user_id": user_id},
        f"账号 #{user.id} {user.username}（禁用）",
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "disable_user",
            "summary": op.summary,
            "message": "禁用为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行禁用",
        }
    )


async def _execute_disable_user(params: dict) -> str:
    """confirm_operation 派发：实际执行账号禁用（重新定位目标 + 落库审计）。"""
    user_id = int(params.get("user_id", 0))
    try:
        async with _db_session() as db:
            user, changed = await user_admin_service.set_user_disabled(db, user_id, True)
            if not changed:
                return _json_dumps(
                    {"status": "error", "message": f"账号 {user.username} 已处于禁用状态"}
                )
            await admin_audit_service.record_audit(
                db,
                operator="admin-agent",
                action=admin_audit_service.ACTION_DISABLE,
                target_user_id=user.id,
                target_username=user.username,
                detail="ok",
            )
    except user_admin_service.UserAdminError as e:
        return _json_dumps({"status": "error", "message": e.message})
    except Exception as e:
        logger.warning("confirm 执行 disable_user 失败: %s", e)
        return _empty_result("账号禁用失败")
    _audit_log("disable_user", f"user#{user_id} {user.username}", "disable", "ok")
    return _json_dumps({"status": "ok", "disabled": user_id, "username": user.username})


@tool
async def enable_user(
    user_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """启用账号（写操作，系统自动记录审计日志；单目标）。

    user_id 为目标账号 id。启用为恢复操作（非破坏性），直接执行；
    账号不存在返回错误。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            user, changed = await user_admin_service.set_user_disabled(db, user_id, False)
            if not changed:
                return _json_dumps(
                    {"status": "error", "message": f"账号 {user.username} 未处于禁用状态"}
                )
            await admin_audit_service.record_audit(
                db,
                operator="admin-agent",
                action=admin_audit_service.ACTION_ENABLE,
                target_user_id=user.id,
                target_username=user.username,
                detail="ok",
            )
    except user_admin_service.UserAdminError as e:
        return _json_dumps({"status": "error", "message": e.message})
    except Exception as e:
        logger.warning("enable_user 工具失败: %s", e)
        return _empty_result("账号启用失败")
    _audit_log("enable_user", f"user#{user_id} {user.username}", "enable", "ok")
    return _json_dumps({"status": "ok", "enabled": user_id, "username": user.username})


@tool
async def reset_user_password(
    user_id: int,
    new_password: str = "",
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """重置账号密码（**敏感操作，两阶段二次确认**；单目标）。

    首次调用只登记待确认项并返回 operation_id（**并未实际重置**）；确认
    请再调用 confirm_operation(operation_id)。new_password 可指定新密码
    （至少 6 位）；留空则由系统生成 12 位临时密码（确认执行后仅返回一次，
    请立即转达管理员）。"""
    _require_admin(is_admin)
    if new_password and len(new_password) < 6:
        return _empty_result("新密码至少 6 位")
    try:
        async with _db_session() as db:
            user = await db.get(User, user_id)
    except Exception as e:
        logger.warning("reset_user_password 工具定位目标失败: %s", e)
        return _empty_result("账号查询暂时不可用")
    if user is None:
        return _json_dumps({"status": "error", "message": f"账号 #{user_id} 不存在"})
    op = confirm.create_pending(
        "reset_user_password",
        {"user_id": user_id, "new_password": new_password},
        f"账号 #{user.id} {user.username}（重置密码）",
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "reset_user_password",
            "summary": op.summary,
            "message": "重置密码为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行",
        }
    )


async def _execute_reset_user_password(params: dict) -> str:
    """confirm_operation 派发：实际执行密码重置（重新定位目标 + 落库审计）。"""
    user_id = int(params.get("user_id", 0))
    new_password = str(params.get("new_password", "") or "")
    try:
        async with _db_session() as db:
            result = await user_admin_service.reset_user_password(
                db, user_id, new_password or None
            )
            user = result["user"]
            await admin_audit_service.record_audit(
                db,
                operator="admin-agent",
                action=admin_audit_service.ACTION_RESET_PASSWORD,
                target_user_id=user.id,
                target_username=user.username,
                detail="ok generated" if result["generated"] else "ok",
            )
    except user_admin_service.UserAdminError as e:
        return _json_dumps({"status": "error", "message": e.message})
    except Exception as e:
        logger.warning("confirm 执行 reset_user_password 失败: %s", e)
        return _empty_result("密码重置失败")
    _audit_log("reset_user_password", f"user#{user_id} {user.username}", "reset_password", "ok")
    return _json_dumps(
        {
            "status": "ok",
            "user_id": user.id,
            "username": user.username,
            "generated": result["generated"],
            "new_password": result["new_password"],
        }
    )


@tool
async def delete_user(
    user_id: int,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """删除账号（**敏感操作，两阶段二次确认**；**单目标，严禁批量**）。

    首次调用只登记待确认项并返回 operation_id 与影响概览（关联对话数/
    记忆条数——删除会**级联清理**该账号的对话、量化评估与长期记忆，
    不可恢复）；确认请再调用 confirm_operation(operation_id)。系统管理员
    账号（ADMIN_USERNAME）受保护，不可删除。"""
    _require_admin(is_admin)
    try:
        async with _db_session() as db:
            user = await db.get(User, user_id)
            item = await user_admin_service.get_user_item(db, user_id) if user else None
    except Exception as e:
        logger.warning("delete_user 工具定位目标失败: %s", e)
        return _empty_result("账号查询暂时不可用")
    if user is None:
        return _json_dumps({"status": "error", "message": f"账号 #{user_id} 不存在"})
    if user_admin_service.is_protected_username(user.username):
        return _json_dumps(
            {"status": "error", "message": f"系统管理员账号「{user.username}」受保护，不可删除"}
        )
    summary = f"账号 #{user.id} {user.username}（对话 {item.dialogue_count} 条 / 记忆 {item.memory_count} 条，删除将级联清理）"
    op = confirm.create_pending(
        "delete_user",
        {"user_id": user_id},
        summary,
    )
    return _json_dumps(
        {
            "status": "pending_confirmation",
            "operation_id": op.operation_id,
            "tool": "delete_user",
            "summary": summary,
            "impact": {
                "dialogues": item.dialogue_count,
                "memories": item.memory_count,
            },
            "message": "删除为两阶段操作：请调用 confirm_operation 工具并传入 operation_id 确认执行删除",
        }
    )


async def _execute_delete_user(params: dict) -> str:
    """confirm_operation 派发：实际执行账号删除（重新定位目标 + 落库审计）。"""
    user_id = int(params.get("user_id", 0))
    try:
        async with _db_session() as db:
            result = await user_admin_service.delete_user(db, user_id)
            if result is None:
                return _json_dumps(
                    {"status": "error", "message": f"账号 #{user_id} 已不存在（可能已被其他操作删除）"}
                )
            await admin_audit_service.record_audit(
                db,
                operator="admin-agent",
                action=admin_audit_service.ACTION_DELETE,
                target_user_id=user_id,
                target_username=result["username"],
                detail=(
                    f"ok dialogues={result['deleted_dialogues']} "
                    f"evaluations={result['deleted_evaluations']} "
                    f"memories={result['deleted_memories']}"
                ),
            )
    except user_admin_service.UserAdminError as e:
        return _json_dumps({"status": "error", "message": e.message})
    except Exception as e:
        logger.warning("confirm 执行 delete_user 失败: %s", e)
        return _empty_result("账号删除失败")
    _audit_log("delete_user", f"user#{user_id} {result['username']}", "delete", "ok")
    return _json_dumps(
        {
            "status": "ok",
            "deleted": user_id,
            "username": result["username"],
            "deleted_dialogues": result["deleted_dialogues"],
            "deleted_evaluations": result["deleted_evaluations"],
            "deleted_memories": result["deleted_memories"],
        }
    )


# ── 二次确认工具（两阶段契约唯一执行入口） ───────────────────────────────


_CONFIRM_EXECUTORS: Dict[str, Callable[[dict], Awaitable[str]]] = {
    "delete_knowledge": _execute_delete_knowledge,
    "delete_user_memory": _execute_delete_user_memory,
    "delete_announcement": _execute_delete_announcement,
    "disable_user": _execute_disable_user,
    "reset_user_password": _execute_reset_user_password,
    "delete_user": _execute_delete_user,
}


@tool
async def confirm_operation(
    operation_id: str,
    is_admin: Annotated[bool, InjectedState("is_admin")] = False,
) -> str:
    """确认并执行一条待确认的敏感操作（两阶段二次确认契约）。

    删除/禁用/重置密码类工具（delete_knowledge / delete_user_memory /
    delete_announcement / disable_user / reset_user_password / delete_user）
    首次调用只返回 pending_confirmation 结果（含 operation_id），**必须**
    再调用本工具传入该 operation_id 才会真正执行；operation_id 短时有效
    （默认 120 秒），过期/重复确认会返回错误，需重新发起。"""
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
    create_announcement,
    update_announcement,
    delete_announcement,
    set_announcement_status,
    query_statistics,
    rebuild_rag_index,
    view_home_highlights,
    list_character_models,
    set_character_model,
    list_users,
    get_user_detail,
    create_user,
    disable_user,
    enable_user,
    reset_user_password,
    delete_user,
    confirm_operation,
]
