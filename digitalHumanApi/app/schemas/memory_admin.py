"""记忆管理模块（批次5 B5）的 Pydantic 请求/响应模型（管理端 X-Admin-Token 鉴权）。

契约权威文档：doc/memory-admin-api.md；变更 schema 必须同步该文档。
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# 事实类型取值（preference/fact/habit），与记忆 schema 契约（doc/long-term-memory.md
# 与 memory_service.FACT_TYPES）保持一致；Pydantic Literal 校验非法值返回 422
MemoryFactType = Literal["preference", "fact", "habit"]

# 记忆内容长度上限：与 memory_service._MEMORY_MAX_CONTENT_LEN 契约一致（≤200 字符）
MEMORY_CONTENT_MAX_LEN = 200


class AdminMemoryItem(BaseModel):
    """单条记忆条目（管理端查看/新增/修改响应）。

    memory_id 为记忆条目对外标识（新条目 uuid；legacy 条目派生 id
    `legacy:{归一化 content}`，见 memory_service.memory_id_of）。
    """

    memory_id: str
    user: str  # 归属用户标识（注册用户为 str(id)）
    fact_type: str  # preference / fact / habit
    content: str  # 自然语言记忆句（≤200 字符）
    source_conversation_id: str  # 来源会话 ID（溯源用，可为空串）
    timestamp: float  # 提取/更新时间（Unix 秒）


class AdminMemoryListResponse(BaseModel):
    """按用户查看记忆列表。"""

    user: str
    total: int
    items: list[AdminMemoryItem]


class AdminMemoryCreateRequest(BaseModel):
    """管理端新增记忆请求（管理员例外：可为任意注册用户新增）。"""

    user: str = Field(
        ..., min_length=1, max_length=64, description="目标注册用户 id（字符串形式）"
    )
    fact_type: MemoryFactType = "fact"
    content: str = Field(
        ..., min_length=1, max_length=MEMORY_CONTENT_MAX_LEN, description="记忆句（1-200 字符）"
    )


class AdminMemoryUpdateRequest(BaseModel):
    """管理端修改记忆请求（部分更新：content 与 fact_type 至少提供其一）。"""

    fact_type: Optional[MemoryFactType] = None
    content: Optional[str] = Field(
        None, min_length=1, max_length=MEMORY_CONTENT_MAX_LEN, description="记忆句（1-200 字符）"
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "AdminMemoryUpdateRequest":
        if self.fact_type is None and self.content is None:
            raise ValueError("content 与 fact_type 至少提供一个")
        return self


class AdminMemoryDeleteResponse(BaseModel):
    """删除记忆响应。"""

    success: bool
    user: str  # 被删记忆的归属用户
    memory_id: str
    deleted: int  # 实际删除条数（1）


class AdminUserItem(BaseModel):
    """管理端用户列表条目（记忆管理页选择用户用）。"""

    id: int
    username: str
    nickname: Optional[str] = None
    memory_count: int  # 该用户长期记忆条数
    created_at: datetime
    last_login_at: Optional[datetime] = None


class AdminUserListResponse(BaseModel):
    """管理端用户分页列表。"""

    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int
