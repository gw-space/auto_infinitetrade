"""해외주식 주문 (LOC 매수, 지정가 매도)."""

import logging
from dataclasses import dataclass

from src.kis.client import KISClient

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """주문 결과."""
    success: bool
    order_id: str
    message: str
    symbol: str
    side: str           # "buy" or "sell"
    order_type: str     # "LOC" or "LIMIT"
    quantity: int
    price: float


async def place_loc_buy(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
    price: float,
) -> OrderResult:
    """LOC(Limit On Close) 매수 주문.

    종가가 지정가 이하일 때 종가로 체결된다.

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드
        exchange: 거래소 코드
        quantity: 주문 수량
        price: LOC 지정가
    """
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="주문 수량이 0입니다.",
            symbol=symbol, side="buy", order_type="LOC",
            quantity=0, price=price,
        )

    # 실전: TTTT1002U, 모의: VTTT1002U
    tr_id = "VTTT1002U" if client.is_paper else "TTTT1002U"

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": f"{price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "34",  # LOC 주문
    }

    logger.info(f"LOC 매수 주문: {symbol} {quantity}주 @ ${price:.2f}")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"LOC 매수 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="LOC 매수 주문 완료",
            symbol=symbol, side="buy", order_type="LOC",
            quantity=quantity, price=price,
        )
    except Exception as e:
        logger.error(f"LOC 매수 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="buy", order_type="LOC",
            quantity=quantity, price=price,
        )


async def place_limit_sell(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
    price: float,
) -> OrderResult:
    """지정가 매도 주문 (Day Order).

    지정가 이상에서 체결된다.

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드
        exchange: 거래소 코드
        quantity: 주문 수량 (전량)
        price: 지정가 (= 목표 익절가)
    """
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="매도 수량이 0입니다.",
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=0, price=price,
        )

    # 실전: TTTT1006U, 모의: VTTT1006U
    tr_id = "VTTT1006U" if client.is_paper else "TTTT1006U"

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": f"{price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가 주문
    }

    logger.info(f"지정가 매도 주문: {symbol} {quantity}주 @ ${price:.2f}")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"지정가 매도 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="지정가 매도 주문 완료",
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=quantity, price=price,
        )
    except Exception as e:
        logger.error(f"지정가 매도 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=quantity, price=price,
        )


async def place_market_buy(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
) -> OrderResult:
    """시장가 매수 주문 (1회차 콜드 스타트용).

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드
        exchange: 거래소 코드
        quantity: 주문 수량
    """
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="주문 수량이 0입니다.",
            symbol=symbol, side="buy", order_type="MARKET",
            quantity=0, price=0.0,
        )

    # 실전: TTTT1002U, 모의: VTTT1002U
    tr_id = "VTTT1002U" if client.is_paper else "TTTT1002U"

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": "0",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "31",  # 시장가
    }

    logger.info(f"시장가 매수 주문: {symbol} {quantity}주")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"시장가 매수 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="시장가 매수 주문 완료",
            symbol=symbol, side="buy", order_type="MARKET",
            quantity=quantity, price=0.0,
        )
    except Exception as e:
        logger.error(f"시장가 매수 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="buy", order_type="MARKET",
            quantity=quantity, price=0.0,
        )


async def place_market_sell(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
) -> OrderResult:
    """시장가 매도 주문 (/sell 강제 매도용)."""
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="매도 수량이 0입니다.",
            symbol=symbol, side="sell", order_type="MARKET",
            quantity=0, price=0.0,
        )

    tr_id = "VTTT1006U" if client.is_paper else "TTTT1006U"

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": "0",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "31",  # 시장가
    }

    logger.info(f"시장가 매도 주문: {symbol} {quantity}주")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"시장가 매도 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="시장가 매도 주문 완료",
            symbol=symbol, side="sell", order_type="MARKET",
            quantity=quantity, price=0.0,
        )
    except Exception as e:
        logger.error(f"시장가 매도 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="sell", order_type="MARKET",
            quantity=quantity, price=0.0,
        )
