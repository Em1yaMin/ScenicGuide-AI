import json
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
    db: AsyncSession, request: KnowledgeCreateRequest
) -> KnowledgeItem:
    """创建知识库条目。"""
    kb = KnowledgeBase(
        title=request.title,
        category=request.category,
        content=request.content,
        tags=request.tags or [],
    )
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return KnowledgeItem.model_validate(kb)


async def update_knowledge(
    db: AsyncSession, kb_id: int, request: KnowledgeUpdateRequest
) -> Optional[KnowledgeBase]:
    """更新知识库条目，返回更新后的 ORM 对象；不存在时返回 None。"""
    kb = await get_knowledge(db, kb_id)
    if kb is None:
        return None

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(kb, field, value)

    await db.flush()
    await db.refresh(kb)
    return kb


async def delete_knowledge(db: AsyncSession, kb_id: int) -> bool:
    """删除知识库条目，成功返回 True，不存在返回 False。"""
    kb = await get_knowledge(db, kb_id)
    if kb is None:
        return False
    await db.delete(kb)
    await db.flush()
    return True


async def import_knowledge(
    db: AsyncSession,
    content: str,
    filename: str,
    default_category: str = "景点资料",
) -> KnowledgeImportResponse:
    """从 JSON 或 Markdown 文件批量导入知识库条目。"""
    imported = 0
    lower_name = filename.lower()

    if lower_name.endswith(".json"):
        items = json.loads(content)
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = _build_create_request(item, default_category)
            await create_knowledge(db, entry)
            imported += 1
    elif lower_name.endswith((".md", ".markdown")):
        entries = _parse_markdown(content, default_category)
        for entry in entries:
            await create_knowledge(db, entry)
        imported += len(entries)
    else:
        raise ValueError("不支持的文件格式，仅支持 .json 和 .md")

    return KnowledgeImportResponse(imported_count=imported)


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
