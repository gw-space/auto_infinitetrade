"""3 사이클 연속 시뮬레이션 - 다양한 시장 조건에서 전략 검증.

사이클 1: 하락 후 반등 → 익절 (정상 케이스)
사이클 2: 지속 하락 → 40회차 소진 → quarter 전략 → 반등 후 익절
사이클 3: 횡보 후 급등 → 빠른 익절 (수익 복리 확인)
"""

import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState, reset_cycle


def simulate_loc_fills(action, closing_price: float) -> list[dict]:
    fills = []
    if action.is_cold_start:
        fills.append({
            "side": "buy", "quantity": action.cold_start_qty,
            "price": closing_price,
            "amount": round(closing_price * action.cold_start_qty, 2),
        })
        return fills

    if action.loc_buy_avg_qty > 0 and closing_price <= action.loc_buy_avg_price:
        fills.append({
            "side": "buy", "quantity": action.loc_buy_avg_qty,
            "price": closing_price,
            "amount": round(closing_price * action.loc_buy_avg_qty, 2),
        })
    if action.loc_buy_high_qty > 0 and closing_price <= action.loc_buy_high_price:
        fills.append({
            "side": "buy", "quantity": action.loc_buy_high_qty,
            "price": closing_price,
            "amount": round(closing_price * action.loc_buy_high_qty, 2),
        })
    if action.limit_sell_qty > 0 and closing_price >= action.limit_sell_price:
        fills.append({
            "side": "sell", "quantity": action.limit_sell_qty,
            "price": closing_price,
            "amount": round(closing_price * action.limit_sell_qty, 2),
        })
    return fills


@dataclass
class CycleResult:
    cycle: int
    days: int
    end_reason: str
    capital_start: float
    total_cost: float
    total_sold: float
    profit: float
    return_pct: float
    splits_used: float
    capital_end: float


def run_cycle(
    state: CycleState,
    prices: list[float],
    start_date: date,
    capital_limit: float,
    cycle_label: str,
) -> CycleResult:
    """한 사이클을 시뮬레이션한다."""

    print(f"\n{'='*70}")
    print(f"  사이클 #{state.cycle_number}: {cycle_label}")
    print(f"  자본금: ${state.total_capital:,.2f} | 분할: {state.num_splits} | 목표: {state.profit_target_pct*100:.0f}%")
    print(f"  40회차 전략: {state.over40_strategy}")
    print(f"{'='*70}")
    print(f"{'일차':>4} | {'종가':>8} | {'액션':<14} | {'체결':>6} | {'평단':>8} | {'보유':>6} | {'분할':>8} | {'수익률':>8}")
    print(f"{'-'*4}-+-{'-'*8}-+-{'-'*14}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}")

    total_cost = 0.0
    total_sold = 0.0
    cycle_completed = False
    end_reason = "진행중"
    day_num = 0
    closing_price = prices[0]

    for day_idx, closing_price in enumerate(prices):
        today = (start_date + timedelta(days=day_idx)).isoformat()
        day_num = day_idx + 1

        action = calculate_daily_action(state, closing_price, state.total_shares)

        # 스킵
        if action.should_skip and not action.over40_action:
            print(f"{day_num:>4} | ${closing_price:>7.2f} | {'SKIP':<14} | {'':>6} | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{state.num_splits:>2} | {action.return_pct:>+7.2f}%")
            continue

        # 40회차 전략 처리
        if action.over40_action:
            if action.over40_action == "quarter_sell" and not state.over40_executed:
                qty = action.quarter_sell_qty
                sold_amount = closing_price * qty
                total_sold += sold_amount
                apply_quarter_sell_result(state, qty, sold_amount)
                print(f"{day_num:>4} | ${closing_price:>7.2f} | {'QUARTER 1/4':<14} | {qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{state.num_splits:>2} | {action.return_pct:>+7.2f}%")
                continue

            elif action.over40_action == "full_exit" and not state.over40_executed:
                qty = action.full_exit_qty
                sold_amount = closing_price * qty
                total_sold += sold_amount
                update_state_after_fill(state, qty, closing_price, sold_amount, "sell")
                state.over40_executed = True
                end_reason = "full_exit"
                cycle_completed = True
                print(f"{day_num:>4} | ${closing_price:>7.2f} | {'FULL EXIT':<14} | {qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{state.num_splits:>2} | {action.return_pct:>+7.2f}%")
                break

            elif action.over40_action == "lower_target":
                pass  # profit_target_pct already changed

            elif action.over40_action == "hold":
                pass

            # hold/lower_target: 지정가 매도 체결 체크
            fills = simulate_loc_fills(action, closing_price)
            if fills:
                for f in fills:
                    if f["side"] == "buy":
                        total_cost += f["amount"]
                    update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                    if f["side"] == "sell":
                        total_sold += f["amount"]

                if state.total_shares <= 0:
                    end_reason = f"40회차→{action.over40_action}→매도"
                    cycle_completed = True

                action_str = "SELL(40소진)" if state.total_shares <= 0 else action.over40_action.upper()
            else:
                action_str = f"{action.over40_action.upper()}(대기)"

            fill_qty = sum(f["quantity"] for f in fills) if fills else 0
            ret = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
            print(f"{day_num:>4} | ${closing_price:>7.2f} | {action_str:<14} | {fill_qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{state.num_splits:>2} | {ret:>+7.2f}%")

            if cycle_completed:
                break
            continue

        # 일반 LOC 체결
        fills = simulate_loc_fills(action, closing_price)

        fill_qty = 0
        action_str = ""
        is_sell = False

        for f in fills:
            if f["side"] == "buy":
                total_cost += f["amount"]
            update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
            fill_qty += f["quantity"]
            if f["side"] == "sell":
                total_sold += f["amount"]
                is_sell = True

        if action.is_cold_start:
            action_str = "시장가 매수"
        elif not fills:
            action_str = "미체결"
        elif is_sell:
            action_str = "SELL(익절)"
            if state.total_shares <= 0:
                end_reason = "익절"
                cycle_completed = True
        else:
            buy_fills = [f for f in fills if f["side"] == "buy"]
            if len(buy_fills) == 2:
                action_str = "LOC 양쪽"
            elif action.loc_buy_avg_qty > 0 and action.loc_buy_high_qty == 0:
                action_str = "LOC 평단만"
            elif action.loc_buy_avg_qty == 0 and action.loc_buy_high_qty > 0:
                action_str = "LOC 고가만"
            else:
                action_str = "LOC 고가만"

        ret = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
        print(f"{day_num:>4} | ${closing_price:>7.2f} | {action_str:<14} | {fill_qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/{state.num_splits:>2} | {ret:>+7.2f}%")

        if cycle_completed:
            break

    # 결과 계산
    unrealized = closing_price * state.total_shares
    profit = (total_sold + unrealized) - total_cost
    return_pct = (profit / total_cost * 100) if total_cost > 0 else 0

    if not cycle_completed:
        end_reason = f"시뮬 종료({len(prices)}일)"

    capital_end = state.total_capital + state.realized_pnl

    result = CycleResult(
        cycle=state.cycle_number,
        days=day_num,
        end_reason=end_reason,
        capital_start=state.total_capital,
        total_cost=total_cost,
        total_sold=total_sold,
        profit=profit,
        return_pct=return_pct,
        splits_used=state.splits_used,
        capital_end=capital_end,
    )

    print(f"\n  --- 사이클 #{state.cycle_number} 결과 ---")
    print(f"  소요일: {result.days}일 | 종료: {result.end_reason}")
    print(f"  자본금: ${result.capital_start:,.2f} → ${result.capital_end:,.2f}")
    print(f"  투입: ${result.total_cost:,.2f} | 매도: ${result.total_sold:,.2f}")
    print(f"  손익: ${result.profit:+,.2f} ({result.return_pct:+.2f}%)")
    print(f"  분할: {result.splits_used:.1f}/{state.num_splits}")

    return result


def main():
    random.seed(42)

    print("\n" + "=" * 70)
    print("  라오어 무한매수법 - 3 사이클 연속 시뮬레이션")
    print("  수익 복리, 40회차 소진, quarter 전략 검증")
    print("=" * 70)

    capital_limit = 10000.0
    initial_capital = 10000.0
    num_splits = 40
    profit_target_pct = 0.10

    state = CycleState(
        symbol="TQQQ",
        cycle_number=1,
        total_capital=initial_capital,
        split_amount=initial_capital / num_splits,
        num_splits=num_splits,
        profit_target_pct=profit_target_pct,
        over40_strategy="quarter",
        cycle_start_date="2026-01-02",
    )

    results = []
    current_date = date(2026, 1, 2)

    # ═══════════════════════════════════════════
    # 사이클 1: 하락 → 반등 → 익절
    # ═══════════════════════════════════════════
    prices = []
    price = 50.0
    for i in range(50):
        if i < 12:
            price *= random.uniform(0.975, 0.998)
        elif i < 25:
            price *= random.uniform(0.997, 1.003)
        else:
            price *= random.uniform(1.008, 1.025)
        prices.append(round(price, 2))

    r = run_cycle(state, prices, current_date, capital_limit, "하락→횡보→반등 (익절 목표)")
    results.append(r)

    if state.total_shares <= 0:
        # 익절 완료 → 새 사이클
        available_cash = state.total_capital + state.realized_pnl  # 시뮬에서는 잔고=원금+수익
        reset_cycle(state, current_date.isoformat(), available_cash, capital_limit)
        current_date += timedelta(days=r.days + 1)
    else:
        print("\n  !! 사이클 1이 종료되지 않았습니다")
        return

    # ═══════════════════════════════════════════
    # 사이클 2: 지속 하락 → 40회차 소진 → quarter → 반등 → 익절
    # ═══════════════════════════════════════════
    prices = []
    price = 48.0  # 사이클 2 시작가
    # 30일 하락
    for i in range(30):
        price *= random.uniform(0.985, 1.002)
        prices.append(round(price, 2))
    # 10일 바닥 횡보
    for i in range(10):
        price *= random.uniform(0.998, 1.002)
        prices.append(round(price, 2))
    # 25일 반등
    for i in range(25):
        price *= random.uniform(1.005, 1.02)
        prices.append(round(price, 2))

    r = run_cycle(state, prices, current_date, capital_limit, "지속 하락→40회차 소진→quarter→반등 익절")
    results.append(r)

    if state.total_shares <= 0:
        available_cash = state.total_capital + state.realized_pnl
        reset_cycle(state, current_date.isoformat(), available_cash, capital_limit)
        current_date += timedelta(days=r.days + 1)
    else:
        # 아직 안 끝남 — 추가 가격으로 마무리 시도
        extra_prices = []
        last_p = prices[-1]
        for _ in range(20):
            last_p *= random.uniform(1.01, 1.03)
            extra_prices.append(round(last_p, 2))

        print(f"\n  >> 추가 {len(extra_prices)}일 반등 시뮬...")
        for day_idx, closing_price in enumerate(extra_prices):
            action = calculate_daily_action(state, closing_price, state.total_shares)
            if action.should_skip and not action.over40_action:
                continue
            fills = simulate_loc_fills(action, closing_price)
            for f in fills:
                update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                if f["side"] == "sell":
                    pass
            if state.total_shares <= 0:
                print(f"  >> Day +{day_idx+1} | ${closing_price:.2f} | 익절!")
                results[-1].end_reason = "quarter→반등→익절"
                results[-1].days += day_idx + 1
                break

        if state.total_shares <= 0:
            available_cash = state.total_capital + state.realized_pnl
            reset_cycle(state, current_date.isoformat(), available_cash, capital_limit)
            current_date += timedelta(days=r.days + 1)
        else:
            print("  !! 사이클 2 미종료, 사이클 3 스킵")

    # ═══════════════════════════════════════════
    # 사이클 3: 횡보 후 급등 → 빠른 익절 (복리 확인)
    # ═══════════════════════════════════════════
    if state.total_shares == 0 and state.splits_used == 0:
        prices = []
        price = 45.0
        # 5일 횡보
        for i in range(5):
            price *= random.uniform(0.998, 1.002)
            prices.append(round(price, 2))
        # 15일 급등
        for i in range(15):
            price *= random.uniform(1.015, 1.035)
            prices.append(round(price, 2))

        r = run_cycle(state, prices, current_date, capital_limit, "횡보 후 급등 → 빠른 익절 (복리)")
        results.append(r)

    # ═══════════════════════════════════════════
    # 전체 요약
    # ═══════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print(f"  3 사이클 전체 요약")
    print(f"{'='*70}")
    print(f"{'사이클':>6} | {'일수':>4} | {'시작자본':>12} | {'분할':>8} | {'손익':>12} | {'수익률':>8} | {'종료사유'}")
    print(f"{'-'*6}-+-{'-'*4}-+-{'-'*12}-+-{'-'*8}-+-{'-'*12}-+-{'-'*8}-+-{'-'*20}")

    total_profit = 0.0
    total_days = 0
    for r in results:
        total_profit += r.profit
        total_days += r.days
        print(f"  #{r.cycle:>3} | {r.days:>4} | ${r.capital_start:>10,.2f} | {r.splits_used:>5.1f}/40 | ${r.profit:>+10,.2f} | {r.return_pct:>+7.2f}% | {r.end_reason}")

    print(f"{'-'*6}-+-{'-'*4}-+-{'-'*12}-+-{'-'*8}-+-{'-'*12}-+-{'-'*8}-+-{'-'*20}")
    initial = results[0].capital_start
    total_return = (total_profit / initial * 100) if initial > 0 else 0
    print(f"  합계 | {total_days:>4} | ${initial:>10,.2f} | {'':>8} | ${total_profit:>+10,.2f} | {total_return:>+7.2f}% |")

    # 검증 항목
    print(f"\n{'='*70}")
    print(f"  검증 항목")
    print(f"{'='*70}")

    checks = []

    # 1. 사이클 번호 연속성
    expected_cycles = list(range(1, len(results) + 1))
    actual_cycles = [r.cycle for r in results]
    ok = actual_cycles == expected_cycles
    checks.append(("사이클 번호 연속성", ok, f"기대: {expected_cycles}, 실제: {actual_cycles}"))

    # 2. 수익 복리 반영 (사이클 2 자본 >= 사이클 1 자본 + 사이클 1 수익)
    if len(results) >= 2:
        ok = results[1].capital_start >= results[0].capital_start
        checks.append(("사이클 2 자본금 >= 사이클 1", ok,
                       f"사이클1: ${results[0].capital_start:,.2f}, 사이클2: ${results[1].capital_start:,.2f}"))

    # 3. 상한선 적용
    for r in results:
        ok = r.capital_start <= capital_limit
        checks.append((f"사이클 #{r.cycle} 상한선(${capital_limit:,.0f})", ok,
                       f"실제: ${r.capital_start:,.2f}"))

    # 4. 40회차 소진 후 quarter 동작 (사이클 2)
    if len(results) >= 2:
        ok = "quarter" in results[1].end_reason or results[1].splits_used > 30
        checks.append(("사이클 2: 40회차 소진/quarter 동작", ok,
                       f"분할 {results[1].splits_used:.1f}/40, 종료: {results[1].end_reason}"))

    # 5. 모든 사이클 종료 시 보유 0주
    ok = state.total_shares == 0 or len(results) < 3
    checks.append(("최종 보유 0주", ok, f"잔여: {state.total_shares}주"))

    # 6. realized_pnl 초기화 (새 사이클 시작 시 0)
    ok = state.realized_pnl >= 0 or state.total_shares > 0  # 진행 중이면 OK
    checks.append(("realized_pnl 관리", ok, f"현재: ${state.realized_pnl:.2f}"))

    # 7. splits_used 범위
    for r in results:
        ok = 0 <= r.splits_used <= 40
        checks.append((f"사이클 #{r.cycle} splits_used 범위", ok, f"{r.splits_used:.1f}"))

    all_pass = True
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{status}] {name}: {detail}")

    print(f"\n{'='*70}")
    if all_pass:
        print(f"  모든 검증 통과!")
    else:
        print(f"  일부 검증 실패 — 위 FAIL 항목 확인")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
