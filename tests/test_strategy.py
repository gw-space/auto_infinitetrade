"""무한매수법 전략 로직 테스트."""

import pytest

from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState, reset_cycle


def make_state(**kwargs) -> CycleState:
    """테스트용 기본 상태를 생성한다."""
    defaults = {
        "symbol": "TQQQ",
        "cycle_number": 1,
        "total_capital": 10000.0,
        "split_amount": 250.0,  # 10000 / 40
        "num_splits": 40,
        "splits_used": 0.0,
        "total_shares": 0,
        "total_invested": 0.0,
        "avg_price": 0.0,
        "realized_pnl": 0.0,
        "profit_target_pct": 0.10,
    }
    defaults.update(kwargs)
    return CycleState(**defaults)


class TestColdStart:
    """1회차 (콜드 스타트) 테스트."""

    def test_first_round_market_buy(self):
        """1회차: 시장가 매수로 1회차 전체 금액."""
        state = make_state()
        action = calculate_daily_action(state, current_price=50.0, existing_shares=0)

        assert action.is_cold_start is True
        assert action.cold_start_qty == 5  # floor(250 / 50)

    def test_first_round_price_too_high(self):
        """1회차: 주가가 1회차 금액보다 높으면 스킵."""
        state = make_state(split_amount=100.0)
        action = calculate_daily_action(state, current_price=150.0, existing_shares=0)

        assert action.should_skip is True
        assert "매수 불가" in action.skip_reason


class TestNormalOrder:
    """일반 주문 (2~40회차) 테스트."""

    def test_loc_buy_quantities(self):
        """LOC 매수(평단) 0.5회차 + LOC 매수(고가) 0.5회차."""
        state = make_state(
            splits_used=5.0,
            total_shares=100,
            total_invested=5000.0,
            avg_price=50.0,
        )
        action = calculate_daily_action(state, current_price=48.0, existing_shares=100)

        # LOC 평단: floor(125 / 50) = 2주
        assert action.loc_buy_avg_qty == 2
        assert action.loc_buy_avg_price == 50.0

        # LOC 고가: floor(125 / 55) = 2주 (목표가 = 50 * 1.1 = 55)
        assert action.loc_buy_high_qty == 2
        assert action.loc_buy_high_price == 55.0

        # 지정가 매도: 기존 보유분 전량
        assert action.limit_sell_qty == 100
        assert action.limit_sell_price == 55.0

    def test_floor_rounding(self):
        """수량 계산 시 내림 적용."""
        state = make_state(
            splits_used=1.0,
            total_shares=3,
            total_invested=210.0,
            avg_price=70.0,  # split_amount=250, 0.5회차=125, 125/70=1.78 → 1주
        )
        action = calculate_daily_action(state, current_price=68.0, existing_shares=3)

        assert action.loc_buy_avg_qty == 1  # floor(125/70) = 1
        # 고가: 70 * 1.1 = 77, floor(125/77) = 1
        assert action.loc_buy_high_qty == 1

    def test_zero_quantity_when_price_too_high(self):
        """0.5회차 금액으로 1주도 못 살 때."""
        state = make_state(
            splits_used=1.0,
            total_shares=1,
            total_invested=300.0,
            avg_price=300.0,  # 0.5회차=125, 125/300=0.41 → 0주
        )
        action = calculate_daily_action(state, current_price=290.0, existing_shares=1)

        assert action.loc_buy_avg_qty == 0


class TestSplitsExhausted:
    """40회차 소진 테스트."""

    def test_40_splits_exhausted(self):
        """40분할 소진 시 매수 중단, 지정가 매도만 유지."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=200)

        # pending_sell 전환
        assert state.pending_sell is True
        assert action.limit_sell_qty == 200
        assert action.limit_sell_price == 44.0  # 40 * 1.1

    def test_pending_sell_keeps_limit_sell(self):
        """매도 대기 상태에서 매일 지정가 매도 유지."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            pending_sell=True,
        )
        action = calculate_daily_action(state, current_price=39.0, existing_shares=200)

        assert action.limit_sell_qty == 200


class TestPaused:
    """일시 중지 테스트."""

    def test_paused_skips_all(self):
        state = make_state(is_paused=True, splits_used=5.0, total_shares=50, avg_price=50.0)
        action = calculate_daily_action(state, current_price=48.0, existing_shares=50)

        assert action.should_skip is True
        assert "중지" in action.skip_reason


class TestStateUpdate:
    """체결 후 상태 업데이트 테스트."""

    def test_buy_updates_avg_price(self):
        """매수 체결 후 평균단가 업데이트."""
        state = make_state(
            splits_used=5.0,
            total_shares=100,
            total_invested=5000.0,
            avg_price=50.0,
        )

        update_state_after_fill(state, filled_qty=10, filled_price=45.0, filled_amount=450.0, side="buy")

        assert state.total_shares == 110
        assert state.total_invested == 5450.0
        assert state.avg_price == pytest.approx(5450.0 / 110, abs=0.01)
        # splits_used: 450/250 = 1.8 추가
        assert state.splits_used == pytest.approx(5.0 + 1.8, abs=0.01)

    def test_sell_updates_pnl(self):
        """매도 체결 후 실현 손익 업데이트."""
        state = make_state(
            splits_used=10.0,
            total_shares=100,
            total_invested=5000.0,
            avg_price=50.0,
        )

        # 100주 전량 매도 @ $55
        update_state_after_fill(state, filled_qty=100, filled_price=55.0, filled_amount=5500.0, side="sell")

        assert state.total_shares == 0
        assert state.realized_pnl == 500.0  # 5500 - 5000
        assert state.total_invested == 0.0

    def test_partial_sell(self):
        """부분 매도 시 잔여 주식 유지."""
        state = make_state(
            splits_used=10.0,
            total_shares=100,
            total_invested=5000.0,
            avg_price=50.0,
        )

        # 60주만 매도
        update_state_after_fill(state, filled_qty=60, filled_price=55.0, filled_amount=3300.0, side="sell")

        assert state.total_shares == 40
        assert state.realized_pnl == 300.0  # 3300 - (50*60)
        assert state.total_invested == pytest.approx(50.0 * 40, abs=0.01)


class TestCycleReset:
    """사이클 리셋 테스트."""

    def test_reset_with_profit(self):
        """수익 포함 사이클 리셋."""
        state = make_state(
            cycle_number=1,
            total_capital=10000.0,
            realized_pnl=500.0,
        )

        reset_cycle(state, "2026-04-01")

        assert state.cycle_number == 2
        assert state.total_capital == 10500.0  # 원금 + 수익
        assert state.split_amount == pytest.approx(10500.0 / 40, abs=0.01)
        assert state.splits_used == 0.0
        assert state.total_shares == 0
        assert state.pending_sell is False
        assert state.realized_pnl == 0.0  # 새 사이클에서 초기화

    def test_reset_with_cash_under_limit(self):
        """잔고 < 상한 → 잔고 기준."""
        state = make_state(cycle_number=1, total_capital=10000.0, realized_pnl=0.0)
        reset_cycle(state, "2026-04-01", available_cash=8000.0, capital_limit=10000.0)

        assert state.total_capital == 8000.0
        assert state.split_amount == pytest.approx(8000.0 / 40, abs=0.01)

    def test_reset_with_cash_over_limit(self):
        """잔고 > 상한 → 상한 적용."""
        state = make_state(cycle_number=1, total_capital=10000.0, realized_pnl=0.0)
        reset_cycle(state, "2026-04-01", available_cash=15000.0, capital_limit=10000.0)

        assert state.total_capital == 10000.0

    def test_reset_no_cash_uses_prev_capital(self):
        """잔고 미조회(0) → 기존 방식 (원금+수익)."""
        state = make_state(cycle_number=1, total_capital=10000.0, realized_pnl=500.0)
        reset_cycle(state, "2026-04-01", available_cash=0.0, capital_limit=0.0)

        assert state.total_capital == 10500.0


class TestReturnCalculation:
    """수익률 계산 테스트."""

    def test_positive_return(self):
        state = make_state(splits_used=5.0, total_shares=100, avg_price=50.0)
        action = calculate_daily_action(state, current_price=55.0, existing_shares=100)
        assert action.return_pct == pytest.approx(10.0, abs=0.01)

    def test_negative_return(self):
        state = make_state(splits_used=5.0, total_shares=100, avg_price=50.0)
        action = calculate_daily_action(state, current_price=45.0, existing_shares=100)
        assert action.return_pct == pytest.approx(-10.0, abs=0.01)


class TestOver40Quarter:
    """40회차 소진 - quarter 전략 테스트."""

    def test_quarter_sell_triggered(self):
        """40회차 소진 시 quarter 전략: 1/4 매도."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            over40_strategy="quarter",
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=200)

        assert state.pending_sell is True
        assert action.over40_action == "quarter_sell"
        assert action.quarter_sell_qty == 50  # 200 // 4

    def test_quarter_sell_min_1_share(self):
        """1/4이 0주면 최소 1주."""
        state = make_state(
            splits_used=40.0,
            total_shares=3,
            total_invested=150.0,
            avg_price=50.0,
            over40_strategy="quarter",
        )
        action = calculate_daily_action(state, current_price=48.0, existing_shares=3)

        assert action.quarter_sell_qty == 1  # max(1, 3//4=0) = 1

    def test_apply_quarter_sell_result(self):
        """quarter 매도 후 splits_used 차감 확인."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            split_amount=250.0,
        )

        # 50주 매도 @ $38 = $1900
        apply_quarter_sell_result(state, sold_qty=50, sold_amount=1900.0)

        assert state.total_shares == 150
        assert state.over40_executed is True
        assert state.quarter_used is True
        assert state.pending_sell is False  # 매수 재개
        # splits_used: 40 - (1900/250) = 40 - 7.6 = 32.4
        assert state.splits_used == pytest.approx(32.4, abs=0.01)

    def test_quarter_second_time_becomes_full_exit(self):
        """quarter 이미 1회 사용 → 재소진 시 full_exit 전환."""
        state = make_state(
            splits_used=40.0,
            total_shares=150,
            total_invested=6000.0,
            avg_price=40.0,
            over40_strategy="quarter",
            quarter_used=True,
        )
        action = calculate_daily_action(state, current_price=35.0, existing_shares=150)

        assert action.over40_action == "full_exit"
        assert action.full_exit_qty == 150
        assert "전량 매도" in action.skip_reason

    def test_quarter_after_executed_resumes_normal(self):
        """quarter 매도 완료 후 pending_sell False → 일반 주문 재개."""
        state = make_state(
            splits_used=32.4,
            total_shares=150,
            total_invested=6000.0,
            avg_price=40.0,
            over40_strategy="quarter",
            over40_executed=True,
            pending_sell=False,  # quarter 매도 후 해제됨
        )
        action = calculate_daily_action(state, current_price=39.0, existing_shares=150)

        # 일반 LOC 주문으로 복귀
        assert action.over40_action == ""
        assert action.loc_buy_avg_qty > 0 or action.loc_buy_high_qty > 0


class TestOver40LowerTarget:
    """40회차 소진 - lower_target 전략 테스트."""

    def test_lower_target_reduces_to_5pct(self):
        """목표 수익률이 5%로 하향."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            over40_strategy="lower_target",
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=200)

        assert state.pending_sell is True
        assert action.over40_action == "lower_target"
        assert state.profit_target_pct == 0.05
        # 매도가: 40 * 1.05 = 42.0
        assert action.limit_sell_price == pytest.approx(42.0, abs=0.01)
        assert action.limit_sell_qty == 200


class TestOver40Hold:
    """40회차 소진 - hold 전략 테스트."""

    def test_hold_keeps_limit_sell_only(self):
        """hold: 매수 중단, 지정가 매도만."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            over40_strategy="hold",
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=200)

        assert action.over40_action == "hold"
        assert action.limit_sell_qty == 200
        assert action.limit_sell_price == pytest.approx(44.0, abs=0.01)  # 40 * 1.1
        assert action.loc_buy_avg_qty == 0
        assert action.loc_buy_high_qty == 0


class TestOver40FullExit:
    """40회차 소진 - full_exit 전략 테스트."""

    def test_full_exit_sells_all(self):
        """full_exit: 전량 매도."""
        state = make_state(
            splits_used=40.0,
            total_shares=200,
            total_invested=8000.0,
            avg_price=40.0,
            over40_strategy="full_exit",
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=200)

        assert action.over40_action == "full_exit"
        assert action.full_exit_qty == 200  # 전량

    def test_full_exit_already_executed(self):
        """full_exit 이미 실행됨 → 스킵."""
        state = make_state(
            splits_used=40.0,
            total_shares=0,
            total_invested=0.0,
            avg_price=40.0,
            over40_strategy="full_exit",
            over40_executed=True,
            pending_sell=True,
        )
        action = calculate_daily_action(state, current_price=38.0, existing_shares=0)

        assert action.should_skip is True
