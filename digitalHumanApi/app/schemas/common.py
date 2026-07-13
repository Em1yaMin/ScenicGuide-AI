from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """统一错误响应格式，供全局异常处理器使用。"""
    code: int
    message: str
