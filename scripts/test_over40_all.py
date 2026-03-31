"""40회차 소진 후 4가지 전략 모두 시뮬레이션.

각 전략별로 3 사이클씩 운영:
- 사이클 1: 40회차 소진 → 전략 실행 → (반등 시) 익절
- 사이클 2: 다시 40회차 소진 → 전략 실행
- 사이클 3: 정상 익절 (전략 후유증 없이 복귀 확인)
"""

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState, reset_cycle


def simulate_loc_fills(action, closing_price):
    fills = []
    if action.is_cold_start:
        fills.append({"side": "buy", "quantity": action.cold_start_qty,
                       "price": closing_price, "amount": round(closing_price * action.cold_start_qty, 2)})
        return fills
    if action.loc_buy_avg_qty > 0 and closing_price <= action.loc_buy_avg_price:
        fills.append({"side": "buy", "quantity": action.loc_buy_avg_qty,
                       "price": closing_price, "amount": round(closing_price * action.loc_buy_avg_qty, 2)})
    if action.loc_buy_high_qty > 0 and closing_price <= action.loc_buy_high_price:
        fills.append({"side": "buy", "quantity": action.loc_buy_high_qty,
                       "price": closing_price, "amount": round(closing_price * action.loc_buy_high_qty, 2)})
    if action.limit_sell_qty > 0 and closing_price >= action.limit_sell_price:
        fills.append({"side": "sell", "quantity": action.limit_sell_qty,
                       "price": closing_price, "amount": round(closing_price * action.limit_sell_qty, 2)})
    return fills


def generate_forced_40_exhaust(start=50.0):
    """40회차 확실히 소진 + 이후 반등 없는 가격.

    매일 0.5% 하락 → LOC 양쪽 체결 → 분할 빠르게 소진.
    40회차 소진 후에도 계속 하락하여 전략별 차이가 드러남.
    """
    prices = []
    p = start
    # 70일 연속 하락 (40분할 소진 + 전략 발동 후에도 하락 지속)
    for _ in range(70):
        p *= 0.995
        prices.append(round(p, 2))
    # 이후 느린 반등 (quarter/hold/lower_target 익절 기회)
    for _ in range(60):
        p *= 1.005
        prices.append(round(p, 2))
    return prices


def generate_recovery(start, days=20):
    """특정 가격에서 반등."""
    prices = []
    p = start
    for _ in range(days):
        p *= random.uniform(1.008, 1.025)
        prices.append(round(p, 2))
    return prices


def run_one_cycle(state, prices, start_date, verbose=True):
    """한 사이클 실행. (total_cost, total_sold, day_count, end_reason) 반환."""
    total_cost = 0.0
    total_sold = 0.0
    day_num = 0
    end_reason = "미종료"
    last_price = prices[0]

    for day_idx, cp in enumerate(prices):
        last_price = cp
        day_num = day_idx + 1
        action = calculate_daily_action(state, cp, state.total_shares)

        # 스킵
        if action.should_skip and not action.over40_action:
            if verbose:
                print(f"  {day_num:>3} | ${cp:>7.2f} | {'SKIP':<16} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {action.return_pct:>+7.2f}%")
            continue

        # 40회차 전략
        if action.over40_action:
            if action.over40_action == "quarter_sell" and not state.over40_executed:
                qty = action.quarter_sell_qty
                amt = round(cp * qty, 2)
                total_sold += amt
                apply_quarter_sell_result(state, qty, amt)
                if verbose:
                    print(f"  {day_num:>3} | ${cp:>7.2f} | {'QUARTER 1/4':<16} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {action.return_pct:>+7.2f}%")
                continue

            elif action.over40_action == "full_exit" and not state.over40_executed:
                qty = action.full_exit_qty
                amt = round(cp * qty, 2)
                total_sold += amt
                update_state_after_fill(state, qty, cp, amt, "sell")
                state.over40_executed = True
                end_reason = "full_exit 전량매도"
                if verbose:
                    print(f"  {day_num:>3} | ${cp:>7.2f} | {'FULL EXIT':<16} | {0:>5}주 | {state.splits_used:>5.1f}/40 | {action.return_pct:>+7.2f}%")
                break

            elif action.over40_action == "lower_target":
                if verbose and not state.over40_executed:
                    print(f"  {day_num:>3} | ${cp:>7.2f} | {'LOWER 5%':<16} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {action.return_pct:>+7.2f}%")
                    state.over40_executed = True  # 표시용

            elif action.over40_action == "hold":
                if verbose and day_num <= 3 or day_num % 10 == 0:
                    ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
                    print(f"  {day_num:>3} | ${cp:>7.2f} | {'HOLD(대기)':<16} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")

            # 지정가 매도 체결 체크
            fills = simulate_loc_fills(action, cp)
            for f in fills:
                if f["side"] == "buy":
                    total_cost += f["amount"]
                update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                if f["side"] == "sell":
                    total_sold += f["amount"]

            if state.total_shares <= 0:
                end_reason = f"40소진→{state.over40_strategy}→익절"
                if verbose:
                    ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
                    print(f"  {day_num:>3} | ${cp:>7.2f} | {'SELL(40→익절)':<16} | {0:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")
                break
            continue

        # 일반 LOC
        fills = simulate_loc_fills(action, cp)
        fill_qty = 0
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
            a = "시장가매수"
        elif not fills:
            a = "미체결"
        elif is_sell:
            a = "SELL(익절)"
        elif len([f for f in fills if f["side"] == "buy"]) == 2:
            a = "LOC 양쪽"
        else:
            a = "LOC 고가만"

        if is_sell and state.total_shares <= 0:
            end_reason = "익절"

        ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
        if verbose and (day_num <= 5 or day_num % 5 == 0 or is_sell or state.splits_used >= 38):
            print(f"  {day_num:>3} | ${cp:>7.2f} | {a:<16} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")

        if end_reason == "익절":
            break

    unrealized = last_price * state.total_shares
    profit = (total_sold + unrealized) - total_cost
    return total_cost, total_sold, day_num, end_reason, profit


def run_strategy_test(strategy_name, seed=42):
    """한 전략으로 3사이클 연속 시뮬레이션."""
    random.seed(seed)

    print(f"\n{'#'*70}")
    print(f"  전략: {strategy_name.upper()}")
    print(f"{'#'*70}")

    capital_limit = 10000.0
    state = CycleState(
        symbol="TQQQ", cycle_number=1,
        total_capital=capital_limit,
        split_amount=capital_limit / 40,
        num_splits=40, profit_target_pct=0.10,
        over40_strategy=strategy_name,
        cycle_start_date="2026-01-02",
    )

    cycle_results = []
    current_date = date(2026, 1, 2)

    for cycle_idx in range(3):
        if cycle_idx < 2:
            # 사이클 1,2: 70일 하락(40분할 확실 소진) + 60일 느린 반등
            prices = generate_forced_40_exhaust(start=50.0)
        else:
            # 사이클 3: 정상 익절 (전략 복귀 확인)
            prices = []
            p = 45.0
            for i in range(30):
                if i < 8:
                    p *= random.uniform(0.98, 1.0)
                else:
                    p *= random.uniform(1.01, 1.03)
                prices.append(round(p, 2))

        print(f"\n  --- 사이클 #{state.cycle_number} (시작자본: ${state.total_capital:,.2f}) ---")

        cost, sold, days, reason, profit = run_one_cycle(state, prices, current_date)
        ret = (profit / cost * 100) if cost > 0 else 0

        cycle_results.append({
            "cycle": state.cycle_number,
            "days": days,
            "reason": reason,
            "capital": state.total_capital,
            "cost": cost,
            "sold": sold,
            "profit": profit,
            "return": ret,
            "splits": state.splits_used,
            "shares": state.total_shares,
        })

        print(f"  결과: {days}일 | {reason} | 분할 {state.splits_used:.1f}/40 | 손익 ${profit:+,.2f} ({ret:+.2f}%)")

        # 사이클 종료 처리
        if state.total_shares <= 0:
            available = state.total_capital + state.realized_pnl
            reset_cycle(state, current_date.isoformat(), available, capital_limit)
            current_date += timedelta(days=days + 1)
        else:
            # 아직 보유 중 → 강제 반등 추가
            last_p = prices[-1]
            extra = generate_recovery(last_p, days=25)
            print(f"  >> 보유 {state.total_shares}주 남음, 추가 반등 {len(extra)}일...")
            cost2, sold2, days2, reason2, profit2 = run_one_cycle(state, extra, current_date + timedelta(days=days))

            cycle_results[-1]["days"] += days2
            cycle_results[-1]["reason"] = reason2
            cycle_results[-1]["cost"] += cost2
            cycle_results[-1]["sold"] += sold2
            total_profit = (sold + sold2 + state.total_shares * extra[-1]) - (cost + cost2)
            cycle_results[-1]["profit"] = total_profit
            cycle_results[-1]["return"] = (total_profit / (cost + cost2) * 100) if (cost + cost2) > 0 else 0
            cycle_results[-1]["splits"] = state.splits_used
            cycle_results[-1]["shares"] = state.total_shares

            print(f"  추가 결과: +{days2}일 | {reason2} | 손익 ${total_profit:+,.2f}")

            if state.total_shares <= 0:
                available = state.total_capital + state.realized_pnl
                reset_cycle(state, current_date.isoformat(), available, capital_limit)
                current_date += timedelta(days=days + days2 + 1)
            else:
                print(f"  !! 여전히 {state.total_shares}주 보유, 다음 사이클 스킵")
                break

    return cycle_results


def main():
    all_results = {}

    for strategy in ["quarter", "lower_target", "hold", "full_exit"]:
        results = run_strategy_test(strategy, seed=42)
        all_results[strategy] = results

    # 전체 비교 요약
    print(f"\n\n{'='*80}")
    print(f"  40회차 소진 전략 비교 (3 사이클)")
    print(f"{'='*80}")
    print(f"{'전략':<14} | {'사이클':>6} | {'일수':>4} | {'분할':>8} | {'손익':>12} | {'수익률':>8} | {'종료사유'}")
    print(f"{'-'*14}-+-{'-'*6}-+-{'-'*4}-+-{'-'*8}-+-{'-'*12}-+-{'-'*8}-+-{'-'*20}")

    strategy_totals = {}
    for strategy, results in all_results.items():
        total_profit = 0
        total_days = 0
        for r in results:
            total_profit += r["profit"]
            total_days += r["days"]
            print(f"{strategy:<14} | #{r['cycle']:>4} | {r['days']:>4} | {r['splits']:>5.1f}/40 | ${r['profit']:>+10,.2f} | {r['return']:>+7.2f}% | {r['reason']}")
        strategy_totals[strategy] = {"profit": total_profit, "days": total_days}
        print(f"{'-'*14}-+-{'-'*6}-+-{'-'*4}-+-{'-'*8}-+-{'-'*12}-+-{'-'*8}-+-{'-'*20}")

    print(f"\n{'='*80}")
    print(f"  전략별 총 수익 비교")
    print(f"{'='*80}")
    print(f"{'전략':<14} | {'총 일수':>6} | {'총 수익':>12} | {'총 수익률':>10}")
    print(f"{'-'*14}-+-{'-'*6}-+-{'-'*12}-+-{'-'*10}")
    for strategy, totals in strategy_totals.items():
        ret = (totals["profit"] / 10000 * 100)
        print(f"{strategy:<14} | {totals['days']:>5}일 | ${totals['profit']:>+10,.2f} | {ret:>+9.2f}%")

    # 검증
    print(f"\n{'='*80}")
    print(f"  검증")
    print(f"{'='*80}")
    all_pass = True

    for strategy, results in all_results.items():
        # 사이클 번호 연속
        cycles = [r["cycle"] for r in results]
        ok = cycles == list(range(1, len(results) + 1))
        status = "PASS" if ok else "FAIL"
        if not ok: all_pass = False
        print(f"  [{status}] {strategy}: 사이클 번호 연속 {cycles}")

        # 마지막 사이클 보유 0주
        last = results[-1]
        ok = last["shares"] == 0
        status = "PASS" if ok else "FAIL"
        if not ok: all_pass = False
        print(f"  [{status}] {strategy}: 최종 보유 {last['shares']}주")

        # 상한선 체크
        for r in results:
            ok = r["capital"] <= 10000.0
            status = "PASS" if ok else "FAIL"
            if not ok: all_pass = False
            print(f"  [{status}] {strategy} #{r['cycle']}: 자본금 ${r['capital']:,.2f} <= $10,000")

    print(f"\n  {'모든 검증 통과!' if all_pass else '일부 검증 실패!'}")


if __name__ == "__main__":
    main()
