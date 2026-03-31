"""USD/KRW 환율 조회."""

import logging

import httpx

logger = logging.getLogger(__name__)


async def get_usd_krw_rate() -> float:
    """USD/KRW 환율을 외부 API에서 조회한다.

    KIS API 환율 조회가 실패할 경우 대체 소스로 사용한다.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 한국은행 환율 정보 (대안: exchangerate-api)
            resp = await client.get(
                "https://open.er-api.com/v6/latest/USD"
            )
            resp.raise_for_status()
            data = resp.json()
            rate = data.get("rates", {}).get("KRW", 0.0)
            if 900 <= rate <= 2000:
                logger.info(f"USD/KRW 환율: {rate:.2f}")
                return rate
            elif rate > 0:
                logger.warning(f"환율 이상값 무시: {rate}")
    except Exception as e:
        logger.warning(f"환율 조회 실패: {e}")

    return 0.0
