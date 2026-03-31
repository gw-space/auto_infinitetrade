"""무한매수법 시뮬레이터 - KIS API 없이 전체 사이클 테스트.

가상 가격 데이터를 생성하여 전략 로직을 검증한다.
5분 안에 40회차+ 사이클을 돌릴 수 있다.
"""

import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy.infinite_buy import (
    DailyAction,
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState, reset_cycle


# === 가격 시나리오 생성 ===

def generate_prices_profit_taking(days: int = 30, start: float = 50.0) -> list[float]:
    """시나리오 1: 하락 후 반등 → 익절.
    10일 하락(-15%) → 10일 횡보 → 10일 상승(+25%) → 익절 발생.
    """
    prices = []
    price = start
    for i in range(days):
        if i < 10:
            price *= random.uniform(0.97, 0.995)  # 하락
        elif i < 20:
            price *= random.uniform(0.99, 1.01)   # 횡보
        else:
            price *= random.uniform(1.01, 1.03)    # 상승
        prices.append(round(price, 2))
    return prices


def generate_prices_40_exhausted(days: int = 45, start: float = 50.0) -> list[float]:
    """시나리오 2: 지속 하락 → 40회차 소진.
    꾸준히 하락하여 목표가에 도달하지 못함.
    """
    prices = []
    price = start
    for i in range(days):
        price *= random.uniform(0.985, 1.005)  # 완만한 하락
        prices.append(round(price, 2))
    return prices


def generate_prices_sideways(days: int = 50, start: float = 50.0) -> list[float]:
    """시나리오 3: 횡보 → 고가 LOC만 체결되는 날 많음."""
    prices = []
    price = start
    for i in range(days):
        price *= random.uniform(0.995, 1.005)
        prices.append(round(price, 2))
    return prices


def generate_prices_vshape(days: int = 40, start: float = 50.0) -> list[float]:
    """V자 반등: 급락 후 급반등."""
    prices = []
    price = start
    half = days // 2
    for i in range(days):
        if i < half:
            price *= random.uniform(0.95, 0.98)  # 급락
        else:
            price *= random.uniform(1.03, 1.06)   # 급반등
        prices.append(round(price, 2))
    return prices


def generate_prices_early_profit(days: int = 15, start: float = 50.0) -> list[float]:
    """초반 급등: 몇 회차 안에 익절."""
    prices = []
    price = start
    for i in range(days):
        price *= random.uniform(1.01, 1.03)
        prices.append(round(price, 2))
    return prices


def generate_prices_high_stock(days: int = 50, start: float = 500.0) -> list[float]:
    """고가 종목: 0.5회차 금액으로 1주도 못 사는 경우 포함."""
    prices = []
    price = start
    for i in range(days):
        price *= random.uniform(0.99, 1.01)
        prices.append(round(price, 2))
    return prices


def generate_prices_crash_recovery(days: int = 60, start: float = 50.0) -> list[float]:
    """폭락 후 장기 회복: -40% 폭락 후 천천히 회복."""
    prices = []
    price = start
    for i in range(days):
        if i < 5:
            price *= random.uniform(0.85, 0.92)  # 5일간 폭락
        elif i < 15:
            price *= random.uniform(0.98, 1.00)   # 바닥 횡보
        else:
            price *= random.uniform(1.005, 1.02)   # 느린 회복
        prices.append(round(price, 2))
    return prices


def generate_prices_whipsaw(days: int = 50, start: float = 50.0) -> list[float]:
    """휩소: 급등락 반복."""
    prices = []
    price = start
    for i in range(days):
        if i % 5 < 2:
            price *= random.uniform(0.95, 0.98)
        else:
            price *= random.uniform(1.02, 1.05)
        prices.append(round(price, 2))
    return prices


def generate_prices_small_capital(days: int = 40, start: float = 50.0) -> list[float]:
    """소액 테스트용 가격 (일반 하락 후 반등)."""
    prices = []
    price = start
    for i in range(days):
        if i < 20:
            price *= random.uniform(0.98, 1.005)
        else:
            price *= random.uniform(1.005, 1.025)
        prices.append(round(price, 2))
    return prices


# === LOC 체결 시뮬레이션 ===

def simulate_loc_fills(action: DailyAction, closing_price: float) -> list[dict]:
    """종가 기준으로 LOC 체결 여부를 판정한다.

    - LOC 매수(평단): 종가 <= 평균단가 → 체결
    - LOC 매수(고가): 종가 <= 목표 익절가 → 체결
    - 지정가 매도: 종가 >= 목표 익절가 → 체결
    """
    fills = []

    if action.is_cold_start:
        # 1회차: 시장가 → 무조건 체결
        fills.append({
            "side": "buy",
            "quantity": action.cold_start_qty,
            "price": closing_price,
            "amount": round(closing_price * action.cold_start_qty, 2),
        })
        return fills

    # LOC 매수(평단): 종가 <= 평균단가
    if action.loc_buy_avg_qty > 0 and closing_price <= action.loc_buy_avg_price:
        fills.append({
            "side": "buy",
            "quantity": action.loc_buy_avg_qty,
            "price": closing_price,
            "amount": round(closing_price * action.loc_buy_avg_qty, 2),
        })

    # LOC 매수(고가): 종가 <= 목표 익절가
    if action.loc_buy_high_qty > 0 and closing_price <= action.loc_buy_high_price:
        fills.append({
            "side": "buy",
            "quantity": action.loc_buy_high_qty,
            "price": closing_price,
            "amount": round(closing_price * action.loc_buy_high_qty, 2),
        })

    # 지정가 매도: 종가 >= 목표 익절가
    if action.limit_sell_qty > 0 and closing_price >= action.limit_sell_price:
        fills.append({
            "side": "sell",
            "quantity": action.limit_sell_qty,
            "price": closing_price,
            "amount": round(closing_price * action.limit_sell_qty, 2),
        })

    return fills


# === 시뮬레이터 ===

@dataclass
class SimResult:
    scenario: str
    total_days: int
    cycle_completed: bool
    end_reason: str
    total_invested: float
    total_sold: float
    profit: float
    return_pct: float
    splits_used: float
    final_shares: int
    final_avg_price: float


def run_simulation(
    scenario_name: str,
    prices: list[float],
    total_capital: float = 10000.0,
    num_splits: int = 40,
    profit_target_pct: float = 0.10,
    over40_strategy: str = "quarter",
) -> SimResult:
    """한 시나리오를 시뮬레이션한다."""

    state = CycleState(
        symbol="SIM",
        cycle_number=1,
        total_capital=total_capital,
        split_amount=total_capital / num_splits,
        num_splits=num_splits,
        profit_target_pct=profit_target_pct,
        over40_strategy=over40_strategy,
        cycle_start_date="2026-01-01",
    )

    start_date = date(2026, 1, 1)
    total_sold = 0.0
    total_cost_basis = 0.0  # 실제 투입 금액 추적
    cycle_completed = False
    end_reason = "진행중"

    print(f"\n{'='*70}")
    print(f"  시나리오: {scenario_name}")
    print(f"  자본금: ${total_capital:,.2f} | {num_splits}분할 | 목표수익률: {profit_target_pct*100:.0f}%")
    print(f"  40회차 전략: {over40_strategy}")
    print(f"  시작가: ${prices[0]:.2f}")
    print(f"{'='*70}")
    print(f"{'일차':>4} | {'종가':>8} | {'액션':<14} | {'체결':>6} | {'평단':>8} | {'보유':>6} | {'분할':>8} | {'수익률':>8}")
    print(f"{'-'*4}-+-{'-'*8}-+-{'-'*14}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}")

    for day_idx, closing_price in enumerate(prices):
        today = (start_date + timedelta(days=day_idx)).isoformat()
        day_num = day_idx + 1

        # 전략 판단
        action = calculate_daily_action(state, closing_price, state.total_shares)

        if action.should_skip and not action.over40_action:
            print(f"{day_num:>4} | ${closing_price:>7.2f} | {'SKIP':<14} | {'':>6} | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{num_splits:>2} | {action.return_pct:>+7.2f}%")
            continue

        # 40회차 전략 처리
        if action.over40_action:
            if action.over40_action == "quarter_sell" and not state.over40_executed:
                qty = action.quarter_sell_qty
                sold_amount = closing_price * qty
                apply_quarter_sell_result(state, qty, sold_amount)
                total_sold += sold_amount
                print(f"{day_num:>4} | ${closing_price:>7.2f} | {'QUARTER 1/4':<14} | {qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{num_splits:>2} | {action.return_pct:>+7.2f}%")
                continue
            elif action.over40_action == "full_exit" and not state.over40_executed:
                qty = action.full_exit_qty
                sold_amount = closing_price * qty
                total_sold += sold_amount
                update_state_after_fill(state, qty, closing_price, sold_amount, "sell")
                state.over40_executed = True
                end_reason = "full_exit 전량 매도"
                cycle_completed = True
                print(f"{day_num:>4} | ${closing_price:>7.2f} | {'FULL EXIT':<14} | {qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{num_splits:>2} | {action.return_pct:>+7.2f}%")
                break
            elif action.over40_action == "lower_target":
                # profit_target_pct already lowered in _handle_over40
                pass
            elif action.over40_action == "hold":
                pass

            # hold / lower_target: 지정가 매도만 유지
            fills = simulate_loc_fills(action, closing_price)
            if fills:
                for f in fills:
                    update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                    if f["side"] == "sell":
                        total_sold += f["amount"]
                sell_fills = [f for f in fills if f["side"] == "sell"]
                if sell_fills and state.total_shares <= 0:
                    end_reason = f"40회차 {action.over40_action} → 매도 체결"
                    cycle_completed = True
                    action_str = "SELL(40소진)"
                else:
                    action_str = action.over40_action.upper()
            else:
                action_str = f"{action.over40_action.upper()}(대기)"

            fill_qty = sum(f["quantity"] for f in fills) if fills else 0
            ret = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
            print(f"{day_num:>4} | ${closing_price:>7.2f} | {action_str:<14} | {fill_qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{num_splits:>2} | {ret:>+7.2f}%")

            if cycle_completed:
                break
            continue

        # LOC 체결 시뮬레이션
        fills = simulate_loc_fills(action, closing_price)

        # 상태 업데이트
        action_str = ""
        fill_qty = 0
        for f in fills:
            if f["side"] == "buy":
                total_cost_basis += f["amount"]
            update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
            fill_qty += f["quantity"]
            if f["side"] == "sell":
                total_sold += f["amount"]

        # 액션 문자열
        if action.is_cold_start:
            action_str = "시장가 매수"
        elif not fills:
            action_str = "미체결"
        else:
            sides = set(f["side"] for f in fills)
            if "sell" in sides:
                action_str = "SELL(익절)"
                if state.total_shares <= 0:
                    end_reason = "익절 매도"
                    cycle_completed = True
            else:
                buy_fills = [f for f in fills if f["side"] == "buy"]
                if len(buy_fills) == 2:
                    action_str = "LOC 양쪽 체결"
                elif action.loc_buy_avg_qty > 0 and action.loc_buy_high_qty == 0:
                    action_str = "LOC 평단만"
                elif action.loc_buy_avg_qty == 0 and action.loc_buy_high_qty > 0:
                    action_str = "LOC 고가만"
                else:
                    action_str = "LOC 고가만"

        ret = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0.0
        print(f"{day_num:>4} | ${closing_price:>7.2f} | {action_str:<14} | {fill_qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{num_splits:>2} | {ret:>+7.2f}%")

        state.last_order_date = today

        if cycle_completed:
            break

    # 결과 요약
    invested = total_cost_basis if total_cost_basis > 0 else state.total_invested
    unrealized = closing_price * state.total_shares
    profit = (total_sold + unrealized) - invested
    return_pct = (profit / invested * 100) if invested > 0 else 0.0

    if not cycle_completed:
        if state.pending_sell:
            end_reason = f"40회차 소진 ({over40_strategy})"
        else:
            end_reason = f"시뮬레이션 종료 ({len(prices)}일)"

    result = SimResult(
        scenario=scenario_name,
        total_days=day_num,
        cycle_completed=cycle_completed,
        end_reason=end_reason,
        total_invested=state.total_invested if not cycle_completed else invested,
        total_sold=total_sold,
        profit=profit,
        return_pct=return_pct,
        splits_used=state.splits_used,
        final_shares=state.total_shares,
        final_avg_price=state.avg_price,
    )

    print(f"\n  --- 결과 ---")
    print(f"  소요일: {result.total_days}일")
    print(f"  종료사유: {result.end_reason}")
    print(f"  사용분할: {result.splits_used:.1f}/{num_splits}")
    print(f"  총투입: ${result.total_invested:,.2f}")
    print(f"  총매도: ${result.total_sold:,.2f}")
    print(f"  손익: ${result.profit:+,.2f} ({result.return_pct:+.2f}%)")
    print(f"  잔여: {result.final_shares}주 @ ${result.final_avg_price:.2f}")

    return result


def main():
    random.seed(42)  # 재현 가능한 결과

    print("\n" + "=" * 70)
    print("  라오어 무한매수법 시뮬레이터")
    print("  KIS API 없이 전략 로직을 검증합니다.")
    print("=" * 70)

    results = []

    # 시나리오 1: 하락 후 반등 → 익절
    prices = generate_prices_profit_taking(days=35, start=50.0)
    r = run_simulation("하락 후 반등 → 익절", prices)
    results.append(r)

    # 시나리오 2: 40회차 소진 (quarter)
    prices = generate_prices_40_exhausted(days=50, start=50.0)
    r = run_simulation("지속 하락 → 40회차 소진 (quarter)", prices, over40_strategy="quarter")
    results.append(r)

    # 시나리오 3: 40회차 소진 (full_exit)
    random.seed(42)
    prices = generate_prices_40_exhausted(days=50, start=50.0)
    r = run_simulation("지속 하락 → 40회차 소진 (full_exit)", prices, over40_strategy="full_exit")
    results.append(r)

    # 시나리오 4: 40회차 소진 (lower_target 5%)
    random.seed(42)
    prices = generate_prices_40_exhausted(days=55, start=50.0)
    # 마지막에 약간 반등 추가 (5% 목표에 도달하도록)
    last = prices[-1]
    for _ in range(10):
        last *= random.uniform(1.01, 1.025)
        prices.append(round(last, 2))
    r = run_simulation("지속 하락 → lower_target 5%", prices, over40_strategy="lower_target")
    results.append(r)

    # 시나리오 5: 횡보 → 고가 LOC만 체결
    random.seed(99)
    prices = generate_prices_sideways(days=50, start=50.0)
    r = run_simulation("횡보장 (고가 LOC 위주 체결)", prices)
    results.append(r)

    # 시나리오 6: V자 반등 (급락 후 급반등)
    random.seed(77)
    prices = generate_prices_vshape(days=30, start=50.0)
    r = run_simulation("V자 반등 (급락→급반등)", prices)
    results.append(r)

    # 시나리오 7: 초반 급등 → 빠른 익절
    random.seed(55)
    prices = generate_prices_early_profit(days=15, start=50.0)
    r = run_simulation("초반 급등 → 빠른 익절", prices)
    results.append(r)

    # 시나리오 8: 고가 종목 ($500) - 0주 매수 엣지케이스
    random.seed(33)
    prices = generate_prices_high_stock(days=30, start=500.0)
    r = run_simulation("고가종목 $500 (0주 엣지)", prices, total_capital=5000.0)
    results.append(r)

    # 시나리오 9: 폭락 후 장기 회복
    random.seed(11)
    prices = generate_prices_crash_recovery(days=60, start=50.0)
    r = run_simulation("폭락(-40%) 후 장기 회복", prices)
    results.append(r)

    # 시나리오 10: 휩소 (급등락 반복)
    random.seed(88)
    prices = generate_prices_whipsaw(days=40, start=50.0)
    r = run_simulation("휩소 (급등락 반복)", prices)
    results.append(r)

    # 시나리오 11: 소액 투자 ($1,000)
    random.seed(22)
    prices = generate_prices_small_capital(days=40, start=50.0)
    r = run_simulation("소액 $1,000 투자", prices, total_capital=1000.0)
    results.append(r)

    # 시나리오 12: 20분할 (빠른 사이클)
    random.seed(42)
    prices = generate_prices_profit_taking(days=25, start=50.0)
    r = run_simulation("20분할 빠른 사이클", prices, num_splits=20)
    results.append(r)

    # 시나리오 13: 목표 수익률 5%
    random.seed(42)
    prices = generate_prices_profit_taking(days=30, start=50.0)
    r = run_simulation("목표 수익률 5%", prices, profit_target_pct=0.05)
    results.append(r)

    # 시나리오 14: 40회차 소진 → hold 전략
    random.seed(42)
    prices = generate_prices_40_exhausted(days=50, start=50.0)
    r = run_simulation("40회차 소진 (hold)", prices, over40_strategy="hold")
    results.append(r)

    # 전체 요약
    print(f"\n\n{'='*70}")
    print(f"  전체 시뮬레이션 요약")
    print(f"{'='*70}")
    print(f"{'시나리오':<30} | {'일수':>4} | {'분할':>8} | {'손익':>12} | {'수익률':>8} | {'종료사유'}")
    print(f"{'-'*30}-+-{'-'*4}-+-{'-'*8}-+-{'-'*12}-+-{'-'*8}-+-{'-'*20}")
    for r in results:
        print(f"{r.scenario:<30} | {r.total_days:>4} | {r.splits_used:>5.1f}/40 | ${r.profit:>+10,.2f} | {r.return_pct:>+7.2f}% | {r.end_reason}")


if __name__ == "__main__":
    main()
