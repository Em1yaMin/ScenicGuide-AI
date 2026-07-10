import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel


_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path, override=True)


def _parse_cors_origins(value: Optional[str]) -> List[str]:
    if not value:
        return ["http://localhost:5173"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


class Settings(BaseModel):
    APP_NAME: str = os.environ.get("APP_NAME", "景区导览AI数字人")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./digital_human.db"
    )
    CORS_ORIGINS: List[str] = _parse_cors_origins(os.environ.get("CORS_ORIGINS"))

    DIFY_API_URL: str = os.environ.get("DIFY_API_URL", "")
    DIFY_API_KEY: str = os.environ.get("DIFY_API_KEY", "")

    SSH_HOST: str = os.environ.get("SSH_HOST", "")
    SSH_PORT: int = int(os.environ.get("SSH_PORT", "22"))
    SSH_USER: str = os.environ.get("SSH_USER", "root")
    SSH_PASSWORD: str = os.environ.get("SSH_PASSWORD", "")


settings = Settings()
