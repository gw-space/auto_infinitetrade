"""라오어 무한매수법 핵심 전략 로직."""

import logging
import math
from dataclasses import dataclass

from src.strategy.state import CycleState

logger = logging.getLogger(__name__)


@dataclass
class DailyAction:
    """매일 수행할 주문 액션."""
    # 주문 정보
    is_cold_start: bool = False     # 1회차 (시장가 매수)
    cold_start_qty: int = 0         # 1회차 시장가 매수 수량

    loc_buy_avg_qty: int = 0        # LOC 매수(평단) 수량
    loc_buy_avg_price: float = 0.0  # LOC 매수(평단) 가격

    loc_buy_high_qty: int = 0       # LOC 매수(고가) 수량
    loc_buy_high_price: float = 0.0 # LOC 매수(고가) 가격

    limit_sell_qty: int = 0         # 지정가 매도 수량 (기존 보유분)
    limit_sell_price: float = 0.0   # 지정가 매도 가격

    # 40회차 전략 전용
    over40_action: str = ""         # "quarter_sell", "lower_target", "hold", "full_exit"
    quarter_sell_qty: int = 0       # quarter 전략: 1/4 매도 수량
    full_exit_qty: int = 0          # full_exit 전략: 전량 매도 수량

    should_skip: bool = False       # 주문 스킵 (40회차 소진 등)
    skip_reason: str = ""

    # 참고 정보
    avg_price: float = 0.0          # 현재 평균단가
    current_price: float = 0.0
    return_pct: float = 0.0         # 현재 수익률
    splits_used: float = 0.0
    total_shares: int = 0


def calculate_daily_action(
    state: CycleState,
    current_price: float,
    existing_shares: int,
) -> DailyAction:
    """오늘 수행할 주문을 계산한다.

    Args:
        state: 현재 사이클 상태
        current_price: 현재 주가
        existing_shares: KIS에서 조회한 실제 보유 수량

    Returns:
        DailyAction 오늘의 주문 정보
    """
    action = DailyAction(
        current_price=current_price,
        avg_price=state.avg_price,
        total_shares=existing_shares,
        splits_used=state.splits_used,
    )

    # 수익률 계산
    if state.avg_price > 0 and existing_shares > 0:
        action.return_pct = (current_price - state.avg_price) / state.avg_price * 100

    # 일시 중지 상태
    if state.is_paused:
        action.should_skip = True
        action.skip_reason = "매매 일시 중지 상태"
        return action

    # 40회차 소진 상태 처리
    if state.pending_sell:
        return _handle_over40(state, action, current_price, existing_shares)

    # 1회차: 시장가 매수 (평균단가 없으므로)
    if state.splits_used == 0 and existing_shares == 0:
        one_round_amount = state.split_amount  # 1회차 전체
        qty = math.floor(one_round_amount / current_price)

        if qty <= 0:
            action.should_skip = True
            action.skip_reason = f"1회차 매수 불가: 주가(${current_price:.2f})가 1회차 금액(${one_round_amount:.2f})보다 높음"
            return action

        action.is_cold_start = True
        action.cold_start_qty = qty
        return action

    # 남은 분할 수 확인 — 1회차 미만이면 40회차 소진
    remaining_splits = state.num_splits - state.splits_used
    if remaining_splits < 1.0:
        state.pending_sell = True
        return _handle_over40(state, action, current_price, existing_shares)

    # 일반 주문: LOC 매수(평단) 0.5회차 + LOC 매수(고가) 0.5회차
    half_round_amount = state.split_amount * 0.5
    target_price = round(state.avg_price * (1 + state.profit_target_pct), 2)

    # LOC 매수(평단) 수량 계산 (내림)
    if state.avg_price > 0:
        action.loc_buy_avg_qty = math.floor(half_round_amount / state.avg_price)
        action.loc_buy_avg_price = round(state.avg_price, 2)

    # LOC 매수(고가) 수량 계산 (내림)
    if target_price > 0:
        action.loc_buy_high_qty = math.floor(half_round_amount / target_price)
        action.loc_buy_high_price = target_price

    # 지정가 매도: 기존 보유분 전량 (당일 LOC 매수분 제외)
    if existing_shares > 0:
        action.limit_sell_qty = existing_shares
        action.limit_sell_price = target_price

    # 0주 경고
    if action.loc_buy_avg_qty == 0:
        logger.warning(f"{state.symbol}: LOC 평단 매수 0주 (주가 ${state.avg_price:.2f} > 0.5회차 ${half_round_amount:.2f})")

    if action.loc_buy_high_qty == 0:
        logger.warning(f"{state.symbol}: LOC 고가 매수 0주 (주가 ${target_price:.2f} > 0.5회차 ${half_round_amount:.2f})")

    return action


def _handle_over40(
    state: CycleState,
    action: DailyAction,
    current_price: float,
    existing_shares: int,
) -> DailyAction:
    """40회차 소진 시 전략별 처리.

    Args:
        state: 사이클 상태
        action: 현재 액션 (수익률 등 참고 정보 이미 세팅됨)
        current_price: 현재가
        existing_shares: 실제 보유 수량
    """
    strategy = state.over40_strategy

    if existing_shares <= 0:
        action.should_skip = True
        action.skip_reason = "40회차 소진 - 보유 수량 없음"
        return action

    target_price = round(state.avg_price * (1 + state.profit_target_pct), 2)

    if strategy == "quarter":
        if state.quarter_used:
            # quarter 이미 1회 사용 → full_exit 전환
            action.over40_action = "full_exit"
            action.full_exit_qty = existing_shares
            action.skip_reason = "40회차 재소진 - quarter 1회 소진, 전량 매도 전환"
            logger.info(f"{state.symbol}: quarter 이미 사용, full_exit 전환")
            return action

        # 1/4 매도 → 시드 재확보 → 매수 재개
        if not state.over40_executed:
            quarter_qty = max(1, existing_shares // 4)
            action.over40_action = "quarter_sell"
            action.quarter_sell_qty = quarter_qty
            action.limit_sell_qty = existing_shares
            action.limit_sell_price = target_price
            action.skip_reason = f"40회차 소진 - quarter 전략: {quarter_qty}주(1/4) 매도 실행"
        else:
            action.limit_sell_qty = existing_shares
            action.limit_sell_price = target_price
            action.skip_reason = "40회차 소진 - quarter 매도 완료, 매수 재개 대기"
        return action

    elif strategy == "lower_target":
        # 목표 수익률 5%로 하향
        if not state.over40_executed:
            state.profit_target_pct = 0.05
            state.over40_executed = True
            action.over40_action = "lower_target"
            logger.info(f"{state.symbol}: 목표 수익률 5%로 하향 조정")

        # 하향된 목표가로 지정가 매도 유지
        new_target = round(state.avg_price * (1 + state.profit_target_pct), 2)
        action.limit_sell_qty = existing_shares
        action.limit_sell_price = new_target
        action.skip_reason = "40회차 소진 - lower_target 전략: 목표 수익률 5%"
        return action

    elif strategy == "hold":
        # 매수 중단, 지정가 매도만 유지
        action.over40_action = "hold"
        action.limit_sell_qty = existing_shares
        action.limit_sell_price = target_price
        action.skip_reason = "40회차 소진 - hold 전략: 지정가 매도만 유지 (/sell로 강제 매도 가능)"
        return action

    elif strategy == "full_exit":
        # 전량 매도 → 새 사이클
        if not state.over40_executed:
            action.over40_action = "full_exit"
            action.full_exit_qty = existing_shares
            action.skip_reason = "40회차 소진 - full_exit 전략: 전량 매도 실행"
        else:
            action.should_skip = True
            action.skip_reason = "40회차 소진 - full_exit 매도 완료 대기"
        return action

    else:
        # 알 수 없는 전략 → hold로 폴백
        action.over40_action = "hold"
        action.limit_sell_qty = existing_shares
        action.limit_sell_price = target_price
        action.skip_reason = f"40회차 소진 - 알 수 없는 전략 '{strategy}', hold로 폴백"
        return action


def apply_quarter_sell_result(state: CycleState, sold_qty: int, sold_amount: float) -> None:
    """quarter 전략 1/4 매도 후 상태를 업데이트한다.

    매도 금액만큼 splits_used를 차감하여 시드를 재확보한다.
    """
    # 매도 손익 반영
    cost_basis = state.avg_price * sold_qty
    pnl = sold_amount - cost_basis
    state.realized_pnl += pnl

    # 보유 수량/투자금 차감
    state.total_shares -= sold_qty
    state.total_invested = state.avg_price * state.total_shares

    # 매도 금액만큼 splits_used 차감 (시드 재확보)
    if state.split_amount > 0:
        reclaimed_splits = sold_amount / state.split_amount
        state.splits_used = max(0, state.splits_used - reclaimed_splits)

    state.over40_executed = True
    state.quarter_used = True
    state.pending_sell = False  # 매수 재개

    logger.info(
        f"{state.symbol}: quarter 매도 {sold_qty}주 완료, "
        f"splits_used {state.splits_used:.1f}/{state.num_splits}로 복원, 매수 재개 "
        f"(quarter 1회 소진, 재소진 시 full_exit)"
    )


def update_state_after_fill(
    state: CycleState,
    filled_qty: int,
    filled_price: float,
    filled_amount: float,
    side: str,
) -> None:
    """체결 후 상태를 업데이트한다.

    Args:
        state: 사이클 상태
        filled_qty: 체결 수량
        filled_price: 체결 가격
        filled_amount: 체결 금액
        side: "buy" or "sell"
    """
    if side == "buy" and filled_qty > 0:
        state.total_shares += filled_qty
        state.total_invested += filled_amount
        if state.total_shares > 0:
            state.avg_price = state.total_invested / state.total_shares

        # 실제 체결 금액 기준으로 splits_used 업데이트
        if state.split_amount > 0:
            state.splits_used += filled_amount / state.split_amount

        state.last_action = "buy"
        logger.info(
            f"{state.symbol}: 매수 체결 {filled_qty}주 @ ${filled_price:.2f} "
            f"(누적 {state.splits_used:.1f}/{state.num_splits}분할)"
        )

    elif side == "sell" and filled_qty > 0:
        # 매도 수익 계산
        sell_proceeds = filled_amount
        cost_basis = state.avg_price * filled_qty
        pnl = sell_proceeds - cost_basis
        state.realized_pnl += pnl

        state.total_shares -= filled_qty
        if state.total_shares <= 0:
            # 전량 매도 완료
            state.total_shares = 0
            state.total_invested = 0.0
        else:
            # 부분 매도: 투자금 비례 차감
            state.total_invested = state.avg_price * state.total_shares

        state.last_action = "sell"
        logger.info(
            f"{state.symbol}: 매도 체결 {filled_qty}주 @ ${filled_price:.2f} "
            f"(손익: ${pnl:.2f}, 잔여: {state.total_shares}주)"
        )
