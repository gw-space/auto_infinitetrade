"""KIS OAuth 토큰 관리."""

import json
import logging
import os
import stat
import time
from pathlib import Path

from src.kis.client import KISClient

logger = logging.getLogger(__name__)

TOKEN_CACHE_PATH = Path("data/token.json")  # 기본값, set_token_path()로 변경


def set_token_path(env_name: str) -> None:
    """환경에 따라 토큰 캐시 파일 경로를 분리한다."""
    global TOKEN_CACHE_PATH
    if env_name and env_name != "default":
        TOKEN_CACHE_PATH = Path(f"data/token_{env_name}.json")
    logger.info(f"토큰 캐시: {TOKEN_CACHE_PATH}")


async def ensure_token(client: KISClient) -> None:
    """유효한 토큰이 없으면 캐시에서 로드하거나 새로 발급한다."""
    # 메모리에 유효한 토큰이 있으면 사용
    if client.is_token_valid():
        return

    # 캐시 파일에서 로드 시도
    if _load_cached_token(client):
        logger.info("캐시된 토큰 로드 성공")
        return

    # 새 토큰 발급
    await _issue_new_token(client)


async def _issue_new_token(client: KISClient) -> None:
    """KIS OAuth 토큰을 새로 발급받는다."""
    logger.info("KIS OAuth 토큰 발급 요청")

    body = {
        "grant_type": "client_credentials",
        "appkey": client.app_key,
        "appsecret": client.app_secret,
    }

    data = await client.post_no_auth("/oauth2/tokenP", body)

    token = data.get("access_token", "")
    expires_in = int(data.get("expires_in", 86400))  # 기본 24시간

    if not token:
        raise RuntimeError("토큰 발급 실패: access_token이 비어있습니다.")

    client.access_token = token
    client.token_expires_at = time.time() + expires_in

    _save_cached_token(client)
    logger.info(f"토큰 발급 성공 (만료: {expires_in}초 후)")


def _load_cached_token(client: KISClient) -> bool:
    """캐시 파일에서 토큰을 로드한다. 유효하면 True."""
    if not TOKEN_CACHE_PATH.exists():
        return False

    try:
        with open(TOKEN_CACHE_PATH) as f:
            cached = json.load(f)

        token = cached.get("access_token", "")
        expires_at = cached.get("expires_at", 0.0)

        if not token or time.time() >= (expires_at - 3600):
            return False

        client.access_token = token
        client.token_expires_at = expires_at
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def _save_cached_token(client: KISClient) -> None:
    """토큰을 캐시 파일에 저장한다 (atomic write)."""
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = TOKEN_CACHE_PATH.with_suffix(".tmp")

    data = {
        "access_token": client.access_token,
        "expires_at": client.token_expires_at,
    }

    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)

    tmp_path.rename(TOKEN_CACHE_PATH)
    TOKEN_CACHE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
