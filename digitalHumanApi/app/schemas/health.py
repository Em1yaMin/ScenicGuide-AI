from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    # 运行环境标识（批次8 C7 多环境配置）：dev/staging/prod，供健康检查/
    # 监控/部署验证区分当前环境；默认 "dev" 兼容直接构造（仅 main.py 使用）
    app_env: str = "dev"
