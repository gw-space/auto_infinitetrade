"""해외주식 시세 조회."""

import logging

from src.kis.client import KISClient

logger = logging.getLogger(__name__)


async def get_current_price(client: KISClient, symbol: str, exchange: str) -> float:
    """해외주식 현재가를 조회한다.

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드 (예: "TQQQ")
        exchange: 거래소 코드 (예: "NASD", "NYSE", "AMEX")

    Returns:
        현재가 (USD)
    """
    tr_id = "HHDFS00000300"

    params = {
        "AUTH": "",
        "EXCD": _exchange_code(exchange),
        "SYMB": symbol,
    }

    data = await client.get(
        "/uapi/overseas-price/v1/quotations/price",
        tr_id=tr_id,
        params=params,
    )

    output = data.get("output", {})
    price_str = output.get("last", "") or output.get("stck_prpr", "0")

    price = float(price_str)
    if price <= 0:
        raise ValueError(f"{symbol} 현재가 조회 실패: {price_str}")

    logger.info(f"{symbol} 현재가: ${price:.2f}")
    return price


def _exchange_code(exchange: str) -> str:
    """거래소 코드를 KIS API 형식으로 변환."""
    mapping = {
        "NASD": "NAS",
        "NASDAQ": "NAS",
        "NAS": "NAS",
        "NYSE": "NYS",
        "NYS": "NYS",
        "AMEX": "AMS",
        "AMS": "AMS",
    }
    return mapping.get(exchange.upper(), exchange.upper())
