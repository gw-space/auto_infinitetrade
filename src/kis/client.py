"""한국투자증권 Open API HTTP 클라이언트."""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class KISAPIError(Exception):
    """KIS API 오류."""

    def __init__(self, message: str, code: str = "", detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(message)


class KISClient:
    """한국투자증권 Open API HTTP 클라이언트.

    공통 헤더 관리, 재시도 로직, 응답 파싱을 담당한다.
    """

    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        account_number: str,
        is_paper: bool = True,
    ):
        self.base_url = base_url
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_number = account_number
        self.account_prefix = account_number.split("-")[0] if "-" in account_number else account_number[:8]
        self.account_suffix = account_number.split("-")[1] if "-" in account_number else account_number[8:]
        self.is_paper = is_paper
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._client = httpx.AsyncClient(timeout=30.0)
        self._min_interval = 10.0  # 호출 간 최소 10초 간격
        self._last_request_at: float = 0.0

    @property
    def access_token(self) -> str:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str):
        self._access_token = value

    @property
    def token_expires_at(self) -> float:
        return self._token_expires_at

    @token_expires_at.setter
    def token_expires_at(self, value: float):
        self._token_expires_at = value

    def is_token_valid(self) -> bool:
        """토큰이 유효한지 (만료 1시간 전 기준) 확인."""
        if not self._access_token:
            return False
        return time.time() < (self._token_expires_at - 3600)

    def _build_headers(self, tr_id: str, extra: dict | None = None) -> dict:
        """API 요청 공통 헤더를 생성한다."""
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            headers.update(extra)
        return headers

    async def get(
        self,
        path: str,
        tr_id: str,
        params: dict | None = None,
        max_retries: int = 3,
    ) -> dict:
        """GET 요청. 재시도 포함."""
        url = f"{self.base_url}{path}"
        headers = self._build_headers(tr_id)

        for attempt in range(max_retries):
            try:
                await self._throttle()
                resp = await self._client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                self._check_response(data)
                return data
            except (httpx.HTTPStatusError, httpx.ConnectError) as e:
                if attempt == max_retries - 1:
                    raise KISAPIError(
                        f"GET {path} 실패 (재시도 {max_retries}회): "
                        f"status={getattr(getattr(e, 'response', None), 'status_code', 'N/A')}"
                    )
                wait = 2 ** attempt
                logger.warning(f"GET {path} 재시도 {attempt + 1}/{max_retries} ({wait}s 후)")
                await self._async_sleep(wait)

        raise KISAPIError(f"GET {path} 최대 재시도 초과")

    async def post(
        self,
        path: str,
        tr_id: str,
        body: dict | None = None,
        max_retries: int = 3,
        no_retry: bool = False,
    ) -> dict:
        """POST 요청.

        Args:
            no_retry: True면 재시도 없이 1회만 시도 (주문 엔드포인트용).
        """
        url = f"{self.base_url}{path}"
        headers = self._build_headers(tr_id)
        attempts = 1 if no_retry else max_retries

        for attempt in range(attempts):
            try:
                await self._throttle()
                resp = await self._client.post(url, headers=headers, json=body or {})
                resp.raise_for_status()
                data = resp.json()
                self._check_response(data)
                return data
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt == attempts - 1:
                    raise KISAPIError(
                        f"POST {path} 실패: "
                        f"status={getattr(getattr(e, 'response', None), 'status_code', 'N/A')}"
                    )
                wait = 2 ** attempt
                logger.warning(f"POST {path} 재시도 {attempt + 1}/{attempts} ({wait}s 후)")
                await self._async_sleep(wait)

        raise KISAPIError(f"POST {path} 최대 재시도 초과")

    async def post_no_auth(self, path: str, body: dict) -> dict:
        """인증 불필요 POST (토큰 발급 등)."""
        url = f"{self.base_url}{path}"
        headers = {"content-type": "application/json; charset=utf-8"}
        await self._throttle()
        resp = await self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    def _check_response(self, data: dict) -> None:
        """KIS 응답의 rt_cd를 확인하여 오류 시 예외 발생."""
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg = data.get("msg1", "알 수 없는 오류")
            code = data.get("msg_cd", "")
            raise KISAPIError(f"KIS API 오류: [{code}] {msg}", code=code, detail=msg)

    async def _throttle(self) -> None:
        """API 호출 간 최소 간격을 유지한다 (초당 3건 제한 대응)."""
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    async def _async_sleep(self, seconds: float):
        await asyncio.sleep(seconds)

    async def close(self):
        await self._client.aclose()
