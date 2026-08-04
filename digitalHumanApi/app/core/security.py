"""安全工具：密码哈希 + JWT 签发/校验 + 对话接口用户身份解析。

选型（轻量、Python 3.9 兼容，见 requirements.txt 注释）:
- 密码哈希: pwdlib[bcrypt]（FastAPI 官方推荐，纯 Python 封装 bcrypt；
  bcrypt 输入上限 72 字节，注册/改密时在 service 层显式校验）
- JWT: PyJWT（纯 Python，无 C 扩展）
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

logger = logging.getLogger(__name__)

# --- JWT 配置 ---

ALGORITHM = "HS256"

# JWT_SECRET 未配置时生成随机临时密钥（仅进程内有效）：
# 保证默认配置下注册/登录/游客 token 开箱可用；重启后已签发 token 全部失效。
# 生产环境必须在 .env 中设置 JWT_SECRET，否则 token 无法跨重启/多实例复用。
_JWT_SECRET = settings.JWT_SECRET or secrets.token_urlsafe(48)
if not settings.JWT_SECRET:
    logger.warning(
        "JWT_SECRET 未配置，已生成随机临时密钥（重启后已签发 token 失效）。"
        "生产环境请设置 JWT_SECRET（如 `python -c \"import secrets; "
        "print(secrets.token_urlsafe(48))\"`）。"
    )

# --- 密码哈希 ---

# 显式指定 bcrypt 后端（PasswordHash.recommended() 会优先选 argon2，需额外依赖）
_password_hasher = PasswordHash([BcryptHasher()])


def hash_password(plain: str) -> str:
    """计算密码哈希。绝不将明文密码落库/落日志。"""
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码；哈希格式异常时返回 False（不抛出）。"""
    try:
        return _password_hasher.verify(plain, hashed)
    except Exception:
        logger.warning("密码校验失败：数据库中的哈希格式异常")
        return False


# --- JWT 签发/校验 ---

def create_access_token(
    *,
    subject: str,
    token_type: str = "user",
    expires_minutes: Optional[int] = None,
) -> Tuple[str, int]:
    """签发 JWT，返回 (token, 有效期秒数)。

    token 载荷:
        sub: 用户 id（type=user）或游客 id（type=guest，形如 guest-xxx）
        typ: token 类型（user / guest）
    """
    if expires_minutes is None:
        expires_minutes = settings.JWT_EXPIRES_MINUTES
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": expire,
    }
    token = jwt.encode(payload, _JWT_SECRET, algorithm=ALGORITHM)
    return token, expires_minutes


def decode_access_token(token: str) -> Optional[dict]:
    """校验并解码 JWT；无效/过期/签名错误返回 None。"""
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None


def decode_bearer(authorization: str) -> Optional[dict]:
    """从 Authorization 头提取并校验 Bearer token；缺失或无效返回 None。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    if not token:
        return None
    return decode_access_token(token)


# --- 对话接口用户身份解析 ---

def resolve_dialogue_user(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization 头解析对话接口（/chat）的 Dify user 标识。

    决策说明（任务要求明确）:
    - 对话接口对「无效/过期 token」采取**降级为游客并告警**（而非 401）：
      对话是游客兜底可用能力（免登录即可使用），token 失效不应阻断对话主链路；
      但错误 token 意味着调用方配置问题，必须留告警日志便于排查。
    - 认证接口（/auth/me、/auth/login 等）保持**严格 401**，见 routers/auth.py。

    返回:
        None   无 Authorization 头 → 调用方回退请求体 user 字段（向后兼容）
        str    有效 token → 用户 id（user token）或游客 id（guest token）
        "visitor"  token 无效/过期 → 降级为默认游客标识
    """
    if not authorization:
        return None
    payload = decode_bearer(authorization)
    if payload is None:
        logger.warning(
            "对话请求携带无效/过期 token，已降级为游客身份（user=visitor）"
        )
        return "visitor"
    return payload.get("sub") or "visitor"
