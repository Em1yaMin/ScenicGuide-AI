import json
import logging
import re
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBase
from app.schemas.knowledge import (
    KnowledgeCreateRequest,
    KnowledgeImportResponse,
    KnowledgeItem,
    KnowledgeListResponse,
    KnowledgeUpdateRequest,
)
from app.services.rag_service import index_knowledge_entries

logger = logging.getLogger(__name__)


async def _sync_knowledge_index(db: AsyncSession) -> None:
    """知识库变更后同步重建 knowledge 命名空间向量索引。

    以当前 DB 全量条目重建（与向量库全量重建机制一致），
    索引失败仅告警日志，不阻断 CRUD 主流程（下次变更/手动索引会自愈）。
    """
    try:
        result = await db.execute(select(KnowledgeBase))
        entries = [
            {
                "id": kb.id,
                "title": kb.title,
                "category": kb.category,
                "content": kb.content,
            }
            for kb in result.scalars().all()
        ]
        sync_result = await index_knowledge_entries(entries)
        logger.info(
            "知识库向量索引已同步: namespace=%s, documents=%d",
            sync_result.get("namespace"),
            sync_result.get("documents", 0),
        )
    except Exception as e:
        logger.warning("知识库向量索引同步失败（不阻断 CRUD）: %s", e)


async def list_knowledge(
    db: AsyncSession,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> KnowledgeListResponse:
    """分页查询知识库条目，支持分类、标签和关键词搜索。"""
    stmt = select(KnowledgeBase)
    count_stmt = select(func.count(KnowledgeBase.id))

    if category:
        stmt = stmt.where(KnowledgeBase.category == category)
        count_stmt = count_stmt.where(KnowledgeBase.category == category)
    if tag:
        stmt = stmt.where(KnowledgeBase.tags.like(f"%{tag}%"))
        count_stmt = count_stmt.where(KnowledgeBase.tags.like(f"%{tag}%"))
    if keyword:
        like = f"%{keyword}%"
        keyword_filter = or_(
            KnowledgeBase.title.ilike(like),
            KnowledgeBase.content.ilike(like),
        )
        stmt = stmt.where(keyword_filter)
        count_stmt = count_stmt.where(keyword_filter)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = (
        stmt.order_by(KnowledgeBase.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = [KnowledgeItem.model_validate(kb) for kb in result.scalars().all()]

    return KnowledgeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_knowledge(db: AsyncSession, kb_id: int) -> Optional[KnowledgeBase]:
    """按 ID 查询知识库条目。"""
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    return result.scalar_one_or_none()


async def create_knowledge(
    db: AsyncSession,
    request: KnowledgeCreateRequest,
    sync_index: bool = True,
) -> KnowledgeItem:
    """创建知识库条目，并同步更新 knowledge 命名空间向量索引。

    Args:
        db: 数据库会话
        request: 创建请求
        sync_index: 是否同步向量索引（批量导入时由调用方统一同步一次）
    """
    kb = KnowledgeBase(
        title=request.title,
        category=request.category,
        content=request.content,
        tags=request.tags or [],
    )
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    if sync_index:
        await _sync_knowledge_index(db)
    return KnowledgeItem.model_validate(kb)


async def update_knowledge(
    db: AsyncSession, kb_id: int, request: KnowledgeUpdateRequest
) -> Optional[KnowledgeBase]:
    """更新知识库条目，并同步更新向量索引；不存在时返回 None。"""
    kb = await get_knowledge(db, kb_id)
    if kb is None:
        return None

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(kb, field, value)

    await db.flush()
    await db.refresh(kb)
    await _sync_knowledge_index(db)
    return kb


async def delete_knowledge(db: AsyncSession, kb_id: int) -> bool:
    """删除知识库条目，并同步更新向量索引；成功返回 True，不存在返回 False。"""
    kb = await get_knowledge(db, kb_id)
    if kb is None:
        return False
    await db.delete(kb)
    await db.flush()
    await _sync_knowledge_index(db)
    return True


async def import_knowledge(
    db: AsyncSession,
    content: str,
    filename: str,
    default_category: str = "景点资料",
) -> KnowledgeImportResponse:
    """从 JSON 或 Markdown 文件批量导入知识库条目，导入完成后统一同步向量索引。"""
    imported = 0
    skipped = 0
    lower_name = filename.lower()

    if lower_name.endswith(".json"):
        items = json.loads(content)
        if isinstance(items, dict):
            items = [items]
        elif not isinstance(items, list):
            raise ValueError("JSON 文件内容格式无效，期望数组或对象")
        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            entry = _build_create_request(item, default_category)
            await create_knowledge(db, entry, sync_index=False)
            imported += 1
        if imported == 0 and skipped > 0:
            raise ValueError("JSON 文件中未找到有效的知识库条目，所有元素均不是对象格式")
    elif lower_name.endswith((".md", ".markdown")):
        entries = _parse_markdown(content, default_category)
        for entry in entries:
            await create_knowledge(db, entry, sync_index=False)
        imported += len(entries)
    else:
        raise ValueError("不支持的文件格式，仅支持 .json 和 .md")

    # 批量导入统一在完成后同步一次向量索引，避免逐条全量重建
    if imported > 0:
        await _sync_knowledge_index(db)

    return KnowledgeImportResponse(imported_count=imported, skipped_count=skipped)


def _build_create_request(
    data: dict, default_category: str
) -> KnowledgeCreateRequest:
    """从字典构建创建请求。"""
    tags = data.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return KnowledgeCreateRequest(
        title=str(data.get("title", "未命名")),
        category=str(data.get("category", default_category)),
        content=str(data.get("content", "")),
        tags=tags if isinstance(tags, list) else None,
    )


def _parse_markdown(
    content: str, default_category: str
) -> list[KnowledgeCreateRequest]:
    """解析 Markdown 内容：按一级标题切分，每个标题对应一条知识库条目。"""
    sections = re.split(r"\n(?=# )", content.strip())
    entries: list[KnowledgeCreateRequest] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.splitlines()
        if lines[0].startswith("# "):
            title = lines[0].lstrip("#").strip()
            body = "\n".join(lines[1:]).strip()
        else:
            title = "未命名"
            body = section
        entries.append(
            KnowledgeCreateRequest(
                title=title,
                category=default_category,
                content=body,
                tags=None,
            )
        )
    return entries
