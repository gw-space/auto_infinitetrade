"""해외주식 주문 (LOC 매수, 지정가 매수/매도)."""

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

    # 모의투자: LOC 미지원 → 지정가 주문으로 대체
    ord_dvsn = "00" if client.is_paper else "34"
    order_label = "지정가 매수(LOC 대체)" if client.is_paper else "LOC 매수"

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": f"{price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": ord_dvsn,
    }

    logger.info(f"{order_label}: {symbol} {quantity}주 @ ${price:.2f}")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
            no_retry=True,
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
            no_retry=True,
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


async def place_immediate_buy(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
    current_price: float = 0.0,
) -> OrderResult:
    """현재가 지정가 매수 (1회차 콜드 스타트용).

    미국 해외주식 매수에는 시장가(ORD_DVSN=31) 코드가 없으므로
    현재가로 지정가 매수를 사용한다.

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드
        exchange: 거래소 코드
        quantity: 주문 수량
        current_price: 현재가 (지정가 매수 가격)
    """
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="주문 수량이 0입니다.",
            symbol=symbol, side="buy", order_type="LIMIT",
            quantity=0, price=0.0,
        )

    if current_price <= 0:
        return OrderResult(
            success=False, order_id="", message="현재가가 0입니다.",
            symbol=symbol, side="buy", order_type="LIMIT",
            quantity=0, price=0.0,
        )

    tr_id = "VTTT1002U" if client.is_paper else "TTTT1002U"

    # 현재가 +2%로 지정가 설정 (즉시 체결 보장)
    order_price = round(current_price * 1.02, 2)

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": f"{order_price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가
    }

    logger.info(f"지정가 매수 (1회차): {symbol} {quantity}주 @ ${order_price:.2f} (현재가 ${current_price:.2f} +2%)")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
            no_retry=True,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"지정가 매수 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="지정가 매수 주문 완료",
            symbol=symbol, side="buy", order_type="LIMIT",
            quantity=quantity, price=current_price,
        )
    except Exception as e:
        logger.error(f"지정가 매수 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="buy", order_type="LIMIT",
            quantity=quantity, price=current_price,
        )


async def place_immediate_sell(
    client: KISClient,
    symbol: str,
    exchange: str,
    quantity: int,
    current_price: float = 0.0,
) -> OrderResult:
    """현재가 지정가 매도 (/sell 강제 매도, quarter 매도, full_exit 매도용).

    미국 해외주식 매도에서 MOC(33)도 가능하지만
    현재가 지정가가 더 확실하므로 지정가로 통일한다.
    """
    if quantity <= 0:
        return OrderResult(
            success=False, order_id="", message="매도 수량이 0입니다.",
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=0, price=0.0,
        )

    if current_price <= 0:
        return OrderResult(
            success=False, order_id="", message="현재가가 0입니다.",
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=0, price=0.0,
        )

    tr_id = "VTTT1006U" if client.is_paper else "TTTT1006U"

    # 현재가 -2%로 지정가 설정 (즉시 체결 보장)
    order_price = round(current_price * 0.98, 2)

    body = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": exchange,
        "PDNO": symbol,
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": f"{order_price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가
    }

    logger.info(f"지정가 매도 (강제): {symbol} {quantity}주 @ ${order_price:.2f} (현재가 ${current_price:.2f} -2%)")

    try:
        data = await client.post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id=tr_id,
            body=body,
            no_retry=True,
        )
        output = data.get("output", {})
        order_id = output.get("ODNO", "") or output.get("odno", "")

        logger.info(f"지정가 매도 주문 성공: 주문번호 {order_id}")
        return OrderResult(
            success=True, order_id=order_id,
            message="지정가 매도 주문 완료",
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=quantity, price=current_price,
        )
    except Exception as e:
        logger.error(f"지정가 매도 주문 실패: {e}")
        return OrderResult(
            success=False, order_id="", message=str(e),
            symbol=symbol, side="sell", order_type="LIMIT",
            quantity=quantity, price=current_price,
        )
