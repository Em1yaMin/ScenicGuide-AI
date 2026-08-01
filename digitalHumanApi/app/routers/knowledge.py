from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.knowledge import (
    KnowledgeCreateRequest,
    KnowledgeImportResponse,
    KnowledgeItem,
    KnowledgeListResponse,
    KnowledgeUpdateRequest,
)
from app.services import knowledge_service

router = APIRouter(prefix="/api/admin/knowledge", tags=["知识库管理"])


async def verify_admin_token(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> str:
    """简易管理员 Token 校验。"""
    expected = settings.ADMIN_API_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员 Token 未配置，请设置 ADMIN_API_TOKEN 环境变量",
        )
    if x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权访问",
        )
    return x_admin_token


@router.get("", response_model=KnowledgeListResponse)
async def list_knowledge(
    category: Optional[str] = Query(None, description="分类筛选"),
    tag: Optional[str] = Query(None, description="标签筛选"),
    keyword: Optional[str] = Query(None, description="标题/内容关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> KnowledgeListResponse:
    return await knowledge_service.list_knowledge(
        db,
        category=category,
        tag=tag,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=KnowledgeItem, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    request: KnowledgeCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> KnowledgeItem:
    return await knowledge_service.create_knowledge(db, request)


@router.put("/{kb_id}", response_model=KnowledgeItem)
async def update_knowledge(
    kb_id: int,
    request: KnowledgeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> KnowledgeItem:
    kb = await knowledge_service.update_knowledge(db, kb_id, request)
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库条目不存在",
        )
    return KnowledgeItem.model_validate(kb)


@router.delete("/{kb_id}")
async def delete_knowledge(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> dict[str, bool]:
    deleted = await knowledge_service.delete_knowledge(db, kb_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库条目不存在",
        )
    return {"success": True}


@router.post("/import", response_model=KnowledgeImportResponse)
async def import_knowledge(
    request: Request,
    file: UploadFile = File(..., description="JSON 或 Markdown 文件"),
    default_category: Optional[str] = Form(None, description="Markdown 导入默认分类（Form 优先，兼容 Query 传参）"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> KnowledgeImportResponse:
    # Form 字段优先；未传时回退 Query 参数（兼容旧调用方），最后用默认值
    category = (
        default_category
        or request.query_params.get("default_category")
        or "景点资料"
    )
    if file.filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少文件名",
        )
    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件编码错误，请使用 UTF-8",
        ) from exc
    try:
        return await knowledge_service.import_knowledge(
            db, content, file.filename, category
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
