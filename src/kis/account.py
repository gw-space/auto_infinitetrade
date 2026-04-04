"""해외주식 계좌 조회 (잔고, 체결내역)."""

import logging
from dataclasses import dataclass
from datetime import datetime

from zoneinfo import ZoneInfo

from src.kis.client import KISClient

# KIS 해외주식 API는 현지시간(US/Eastern) 기준 날짜를 사용
_ET = ZoneInfo("US/Eastern")

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    """보유 종목 정보."""
    symbol: str
    quantity: int
    avg_price: float        # 평균 매입가 (USD)
    current_price: float    # 현재가 (USD)
    eval_amount: float      # 평가금액 (USD)
    pnl_amount: float       # 평가 손익 (USD)
    pnl_pct: float          # 수익률 (%)


@dataclass
class OrderExecution:
    """체결 내역."""
    order_id: str
    symbol: str
    side: str           # "buy" or "sell"
    quantity: int        # 체결 수량
    price: float         # 체결 가격
    amount: float        # 체결 금액
    order_quantity: int  # 주문 수량
    status: str          # "filled", "partial", "pending", "cancelled"


async def get_holdings(client: KISClient, symbol: str | None = None) -> list[Holding]:
    """해외주식 잔고를 조회한다.

    Args:
        client: KIS API 클라이언트
        symbol: 특정 종목만 조회 (None이면 전체)

    Returns:
        보유 종목 목록
    """
    # 실전: TTTS3012R, 모의: VTTS3012R
    tr_id = "VTTS3012R" if client.is_paper else "TTTS3012R"

    params = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": "",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }

    data = await client.get(
        "/uapi/overseas-stock/v1/trading/inquire-balance",
        tr_id=tr_id,
        params=params,
    )

    holdings = []
    for item in data.get("output1", []):
        sym = item.get("ovrs_pdno", "").strip()
        qty = int(float(item.get("ovrs_cblc_qty", "0")))

        if qty <= 0:
            continue
        if symbol and sym != symbol:
            continue

        avg_p = float(item.get("pchs_avg_pric", "0"))
        cur_p = float(item.get("now_pric2", "0") or item.get("ovrs_now_pric1", "0"))
        eval_amt = float(item.get("ovrs_stck_evlu_amt", "0"))
        pnl_amt = float(item.get("frcr_evlu_pfls_amt", "0"))
        pnl_rate = float(item.get("evlu_pfls_rt", "0"))

        holdings.append(Holding(
            symbol=sym,
            quantity=qty,
            avg_price=avg_p,
            current_price=cur_p,
            eval_amount=eval_amt,
            pnl_amount=pnl_amt,
            pnl_pct=pnl_rate,
        ))

    return holdings


async def get_available_cash(client: KISClient, symbol: str = "TQQQ") -> float:
    """해외주식 주문 가능 금액(USD)을 조회한다.

    Args:
        client: KIS API 클라이언트
        symbol: 종목 코드 (실전 API에서 필수)
    """
    # 실전: TTTS3007R, 모의: VTTS3007R
    tr_id = "VTTS3007R" if client.is_paper else "TTTS3007R"

    params = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "OVRS_EXCG_CD": "NASD",
        "OVRS_ORD_UNPR": "0",
        "ITEM_CD": symbol,
    }

    data = await client.get(
        "/uapi/overseas-stock/v1/trading/inquire-psamount",
        tr_id=tr_id,
        params=params,
    )

    output = data.get("output", {})
    cash = float(output.get("ovrs_ord_psbl_amt", "0") or output.get("frcr_ord_psbl_amt1", "0"))
    logger.info(f"주문 가능 금액: ${cash:.2f}")
    return cash


async def get_executions(
    client: KISClient, symbol: str | None = None,
) -> list[OrderExecution]:
    """당일 체결 내역을 조회한다.

    Args:
        client: KIS API 클라이언트
        symbol: 특정 종목만 조회 (None이면 전체)
    """
    # 실전: TTTS3035R, 모의: VTTS3035R
    tr_id = "VTTS3035R" if client.is_paper else "TTTS3035R"

    # KIS 해외주식은 현지시간(ET) 기준 — 프로세스 TZ와 무관하게 ET 날짜 사용
    et_now = datetime.now(_ET)
    date_yyyymmdd = et_now.strftime("%Y%m%d")

    params = {
        "CANO": client.account_prefix,
        "ACNT_PRDT_CD": client.account_suffix,
        "PDNO": symbol or "",
        "ORD_STRT_DT": date_yyyymmdd,
        "ORD_END_DT": date_yyyymmdd,
        "SLL_BUY_DVSN": "00",  # 전체
        "CCLD_NCCS_DVSN": "00",  # 전체
        "OVRS_EXCG_CD": "",
        "SORT_SQN": "DS",
        "ORD_GNO_BRNO": "",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }

    # 모의투자는 추가 필드를 요구
    if client.is_paper:
        params["ORD_DT"] = date_yyyymmdd
        params["ODNO"] = ""

    data = await client.get(
        "/uapi/overseas-stock/v1/trading/inquire-ccnl",
        tr_id=tr_id,
        params=params,
    )

    executions = []
    for item in data.get("output", []):
        sym = item.get("ovrs_pdno", "").strip()
        if symbol and sym != symbol:
            continue

        ord_qty = int(float(item.get("ft_ord_qty", "0")))
        fill_qty = int(float(item.get("ft_ccld_qty", "0")))
        fill_price = float(item.get("ft_ccld_unpr3", "0"))
        fill_amt = float(item.get("ft_ccld_amt", "0"))
        order_id = item.get("odno", "")

        side_code = item.get("sll_buy_dvsn_cd", "")
        side = "buy" if side_code == "02" else "sell"

        if fill_qty >= ord_qty and ord_qty > 0:
            status = "filled"
        elif fill_qty > 0:
            status = "partial"
        else:
            status = "pending"

        executions.append(OrderExecution(
            order_id=order_id,
            symbol=sym,
            side=side,
            quantity=fill_qty,
            price=fill_price,
            amount=fill_amt,
            order_quantity=ord_qty,
            status=status,
        ))

    return executions
