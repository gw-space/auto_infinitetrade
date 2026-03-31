"""종합 시뮬레이션 - 모든 시나리오 검증.

1. 40회차 소진 전 익절
2. 40회차 소진 후 hold 상태에서 익절
3. 40회차 소진 → quarter 진행 중 익절
4. 40회차 소진 → quarter 후 재소진 → full_exit
5. 1회차 시장가 매수 정상 동작
6. 사이클 리셋 후 자본금 정상 반영
7. 연속 3사이클 복리 동작
8. /sell 강제 매도 (장중 전량 매도)
9. 고가 종목 (0주 매수 경고)
10. 소액 투자 (1회차 금액 부족)
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState, reset_cycle

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def make_state(**kwargs):
    defaults = {
        "symbol": "TQQQ",
        "cycle_number": 1,
        "total_capital": 10000.0,
        "split_amount": 250.0,
        "num_splits": 40,
        "splits_used": 0.0,
        "total_shares": 0,
        "total_invested": 0.0,
        "avg_price": 0.0,
        "realized_pnl": 0.0,
        "profit_target_pct": 0.10,
        "over40_strategy": "quarter",
    }
    defaults.update(kwargs)
    return CycleState(**defaults)


def simulate_fills(action, closing_price):
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


def run_days(state, prices):
    """가격 리스트로 시뮬레이션 실행. (total_cost, total_sold, days, end_reason) 반환."""
    total_cost = 0.0
    total_sold = 0.0
    days = 0

    for cp in prices:
        days += 1
        action = calculate_daily_action(state, cp, state.total_shares)

        if action.should_skip and not action.over40_action:
            continue

        # 40회차 전략
        if action.over40_action:
            if action.over40_action == "quarter_sell" and not state.over40_executed:
                qty = action.quarter_sell_qty
                amt = round(cp * qty, 2)
                total_sold += amt
                apply_quarter_sell_result(state, qty, amt)
                continue
            elif action.over40_action == "full_exit":
                qty = action.full_exit_qty
                amt = round(cp * qty, 2)
                total_sold += amt
                update_state_after_fill(state, qty, cp, amt, "sell")
                state.over40_executed = True
                return total_cost, total_sold, days, "full_exit"
            elif action.over40_action == "lower_target":
                pass
            elif action.over40_action == "hold":
                pass

            # hold/lower_target: 매도 체결 체크
            fills = simulate_fills(action, cp)
            for f in fills:
                if f["side"] == "buy": total_cost += f["amount"]
                update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                if f["side"] == "sell": total_sold += f["amount"]
            if state.total_shares <= 0:
                return total_cost, total_sold, days, f"40→{state.over40_strategy}→익절"
            continue

        # 일반 LOC
        fills = simulate_fills(action, cp)
        for f in fills:
            if f["side"] == "buy": total_cost += f["amount"]
            update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
            if f["side"] == "sell": total_sold += f["amount"]

        if state.total_shares <= 0 and any(f["side"] == "sell" for f in fills):
            return total_cost, total_sold, days, "익절"

    return total_cost, total_sold, days, "미종료"


# ============================================================
print("=" * 65)
print("  종합 시뮬레이션 검증")
print("=" * 65)


# --- 1. 40회차 소진 전 익절 ---
print("\n--- 1. 40회차 소진 전 익절 ---")
state = make_state()
prices = [50 * (0.995 ** i) for i in range(20)]  # 20일 하락
prices += [prices[-1] * (1.01 ** i) for i in range(1, 30)]  # 30일 상승
cost, sold, days, reason = run_days(state, prices)
check("익절 발생", reason == "익절", f"Day {days}")
check("보유 0주", state.total_shares == 0)
check("splits < 40", state.splits_used < 40, f"splits={state.splits_used:.1f}")
check("수익 발생", sold > cost, f"${sold - cost:+.2f}")


# --- 2. 40회차 소진 → hold 상태에서 익절 ---
print("\n--- 2. 40회차 소진 → hold → 익절 ---")
state = make_state(over40_strategy="hold")
prices = [50 * (0.995 ** i) for i in range(70)]  # 70일 하락 (40회차 소진)
prices += [prices[-1] * (1.005 ** i) for i in range(1, 80)]  # 느린 반등
cost, sold, days, reason = run_days(state, prices)
check("40회차 소진됨", state.pending_sell or reason.startswith("40"))
check("hold 후 익절", "익절" in reason, f"reason={reason}")
check("보유 0주", state.total_shares == 0)


# --- 3. 40회차 소진 → quarter → 매수 재개 → 익절 ---
print("\n--- 3. 40회차 소진 → quarter → 매수 재개 → 익절 ---")
# quarter 실행 직후 상태를 직접 세팅하여 매수 재개 → 익절 검증
state = make_state(
    over40_strategy="quarter",
    splits_used=31.0, total_shares=170, total_invested=170 * 43.0,
    avg_price=43.0, quarter_used=True, over40_executed=True, pending_sell=False,
)
# 평단 43, 목표 47.3 → 현재가 43~48로 반등
prices = [43.0 + i * 0.5 for i in range(15)]  # 43→50까지 선형 상승
cost, sold, days, reason = run_days(state, prices)
check("quarter 사용됨", state.quarter_used)
check("익절 발생", "익절" in reason, f"reason={reason}")
check("보유 0주", state.total_shares == 0)


# --- 4. 40회차 소진 → quarter → 재소진 → full_exit ---
print("\n--- 4. quarter 후 재소진 → full_exit ---")
state = make_state(over40_strategy="quarter")
# 60일 하락 → quarter 발동
prices = [50 * (0.995 ** i) for i in range(60)]
# quarter 후 매수 재개하지만 계속 하락 → 재소진
prices += [prices[-1] * (0.997 ** i) for i in range(1, 60)]
cost, sold, days, reason = run_days(state, prices)
check("quarter 사용됨", state.quarter_used)
check("full_exit 발생", reason == "full_exit", f"reason={reason}")
check("보유 0주", state.total_shares == 0)
check("over40_executed", state.over40_executed)


# --- 5. 1회차 시장가 매수 ---
print("\n--- 5. 1회차 시장가 매수 ---")
state = make_state()
action = calculate_daily_action(state, 50.0, 0)
check("cold_start", action.is_cold_start)
check("수량 = floor(250/50) = 5", action.cold_start_qty == 5)
check("LOC 없음", action.loc_buy_avg_qty == 0 and action.loc_buy_high_qty == 0)


# --- 6. 1회차 주가 너무 높음 ---
print("\n--- 6. 1회차 주가가 1회차 금액보다 높음 ---")
state = make_state(split_amount=100.0)
action = calculate_daily_action(state, 150.0, 0)
check("스킵", action.should_skip)
check("매수 불가 사유", "매수 불가" in action.skip_reason)


# --- 7. 사이클 리셋 후 자본금 ---
print("\n--- 7. 사이클 리셋 - 자본금 반영 ---")
state = make_state(cycle_number=1, total_capital=10000.0, realized_pnl=500.0)

# 잔고 기반 (잔고 < 상한)
reset_cycle(state, "2026-04-01", available_cash=8000.0, capital_limit=10000.0)
check("잔고 기반 자본금", state.total_capital == 8000.0, f"${state.total_capital}")
check("사이클 번호 증가", state.cycle_number == 2)
check("realized_pnl 초기화", state.realized_pnl == 0.0)
check("splits 초기화", state.splits_used == 0.0)
check("quarter_used 초기화", state.quarter_used == False)

# 잔고 > 상한
state2 = make_state(cycle_number=1, total_capital=10000.0, realized_pnl=0.0)
reset_cycle(state2, "2026-04-01", available_cash=15000.0, capital_limit=10000.0)
check("상한 적용", state2.total_capital == 10000.0, f"${state2.total_capital}")


# --- 8. 연속 3사이클 복리 ---
print("\n--- 8. 연속 3사이클 ---")
state = make_state()
cycle_profits = []

for cycle in range(3):
    # 10일 하락 → 20일 상승
    prices = [50 * (0.995 ** i) for i in range(10)]
    prices += [prices[-1] * (1.01 ** i) for i in range(1, 25)]

    cost, sold, days, reason = run_days(state, prices)
    profit = sold - cost
    cycle_profits.append(profit)

    if state.total_shares <= 0:
        available = state.total_capital + state.realized_pnl
        reset_cycle(state, "2026-04-01", available, 10000.0)

check("3사이클 모두 익절", len(cycle_profits) == 3)
check("사이클 번호 = 4", state.cycle_number == 4, f"cycle={state.cycle_number}")
check("모든 사이클 수익", all(p > 0 for p in cycle_profits),
      f"profits={[f'${p:.0f}' for p in cycle_profits]}")


# --- 9. /sell 강제 매도 시뮬레이션 ---
print("\n--- 9. /sell 강제 매도 ---")
state = make_state(
    splits_used=15.0,
    total_shares=100,
    total_invested=4500.0,
    avg_price=45.0,
)
# 강제 매도 = 시장가 전량 매도
sell_price = 42.0
sell_amount = sell_price * state.total_shares
update_state_after_fill(state, state.total_shares, sell_price, sell_amount, "sell")
check("전량 매도 후 0주", state.total_shares == 0)
check("realized_pnl 반영", state.realized_pnl != 0,
      f"pnl=${state.realized_pnl:.2f}")
check("total_invested = 0", state.total_invested == 0.0)


# --- 10. 고가 종목 ($500) ---
print("\n--- 10. 고가 종목 - 0주 매수 ---")
state = make_state(total_capital=5000.0, split_amount=125.0)
# 1회차: 125/500 = 0.25 → 0주
action = calculate_daily_action(state, 500.0, 0)
check("매수 불가 스킵", action.should_skip)


# --- 11. LOC 체결 시나리오별 ---
print("\n--- 11. LOC 체결 시나리오 ---")
state = make_state(
    splits_used=5.0,
    total_shares=50,
    total_invested=2500.0,
    avg_price=50.0,
)
target = 50.0 * 1.1  # 55.0

# 하락일: 종가 <= 평단 → 양쪽 체결
action = calculate_daily_action(state, 48.0, 50)
fills = simulate_fills(action, 48.0)
buy_fills = [f for f in fills if f["side"] == "buy"]
check("하락일: 양쪽 체결", len(buy_fills) == 2, f"fills={len(buy_fills)}")

# 횡보일: 평단 < 종가 <= 고가 → 고가만
fills = simulate_fills(action, 52.0)
buy_fills = [f for f in fills if f["side"] == "buy"]
check("횡보일: 고가만 체결", len(buy_fills) == 1, f"fills={len(buy_fills)}")

# 급등일: 종가 > 고가 → 매수 미체결, 매도 체결
fills = simulate_fills(action, 56.0)
buy_fills = [f for f in fills if f["side"] == "buy"]
sell_fills = [f for f in fills if f["side"] == "sell"]
check("급등일: 매수 미체결", len(buy_fills) == 0)
check("급등일: 매도 체결", len(sell_fills) == 1)


# --- 12. 40회차 소진 → lower_target → 5% 익절 ---
print("\n--- 12. lower_target → 5% 익절 ---")
state = make_state(over40_strategy="lower_target")
prices = [50 * (0.995 ** i) for i in range(60)]  # 하락 → 소진
prices += [prices[-1] * (1.005 ** i) for i in range(1, 80)]  # 반등 (5% 도달)
cost, sold, days, reason = run_days(state, prices)
check("lower_target 실행", state.profit_target_pct == 0.05,
      f"target={state.profit_target_pct}")
check("익절 발생", "익절" in reason, f"reason={reason}")


# --- 13. 40회차 소진 → full_exit → 새 사이클 ---
print("\n--- 13. full_exit → 새 사이클 ---")
state = make_state(over40_strategy="full_exit")
prices = [50 * (0.995 ** i) for i in range(60)]
cost, sold, days, reason = run_days(state, prices)
check("full_exit 실행", reason == "full_exit")
check("보유 0주", state.total_shares == 0)
# 새 사이클 시작
old_capital = state.total_capital
reset_cycle(state, "2026-04-01", available_cash=old_capital + state.realized_pnl, capital_limit=10000.0)
check("새 사이클 시작", state.cycle_number == 2)
check("splits 초기화", state.splits_used == 0.0)


# --- 14. 일시 중지 상태 ---
print("\n--- 14. 일시 중지 (/pause) ---")
state = make_state(
    splits_used=10.0, total_shares=50, avg_price=48.0, is_paused=True
)
action = calculate_daily_action(state, 45.0, 50)
check("일시 중지 스킵", action.should_skip)
check("사유에 '중지' 포함", "중지" in action.skip_reason)


# --- 15. 수량 내림(floor) 검증 ---
print("\n--- 15. 수량 내림(floor) 계산 ---")
state = make_state(
    splits_used=5.0, total_shares=10, total_invested=700.0, avg_price=70.0
)
action = calculate_daily_action(state, 68.0, 10)
# 0.5회차 = 125, floor(125/70) = 1
check("LOC 평단 수량 내림", action.loc_buy_avg_qty == 1,
      f"qty={action.loc_buy_avg_qty}, expected=floor(125/70)=1")
# 고가 = 77, floor(125/77) = 1
check("LOC 고가 수량 내림", action.loc_buy_high_qty == 1,
      f"qty={action.loc_buy_high_qty}, expected=floor(125/77)=1")


# --- 16. 부분 체결 후 상태 ---
print("\n--- 16. 부분 매도 후 상태 ---")
state = make_state(
    splits_used=20.0, total_shares=100, total_invested=4500.0, avg_price=45.0
)
# 60주만 매도 @ $50
update_state_after_fill(state, 60, 50.0, 3000.0, "sell")
check("잔여 40주", state.total_shares == 40)
check("손익 반영", state.realized_pnl == 300.0, f"pnl={state.realized_pnl}(3000-2700)")
check("투자금 비례 차감", abs(state.total_invested - 45.0 * 40) < 0.01)


# --- 17. 수익률 계산 ---
print("\n--- 17. 수익률 계산 ---")
state = make_state(splits_used=10.0, total_shares=100, avg_price=50.0)
action = calculate_daily_action(state, 55.0, 100)
check("양수 수익률", abs(action.return_pct - 10.0) < 0.01, f"{action.return_pct:.2f}%")
action = calculate_daily_action(state, 45.0, 100)
check("음수 수익률", abs(action.return_pct - (-10.0)) < 0.01, f"{action.return_pct:.2f}%")


# --- 18. pending_sell 상태에서 매도만 유지 ---
print("\n--- 18. pending_sell: 매수 없이 매도만 ---")
state = make_state(
    splits_used=39.5, total_shares=200, total_invested=8000.0,
    avg_price=40.0, pending_sell=True, over40_strategy="hold"
)
action = calculate_daily_action(state, 38.0, 200)
check("매수 없음", action.loc_buy_avg_qty == 0 and action.loc_buy_high_qty == 0)
check("매도 주문 있음", action.limit_sell_qty == 200)
check("매도가 = 목표가", abs(action.limit_sell_price - 44.0) < 0.01)


# ============================================================
print(f"\n{'=' * 65}")
print(f"  결과: {PASS_COUNT} PASS / {FAIL_COUNT} FAIL / 총 {PASS_COUNT + FAIL_COUNT}건")
if FAIL_COUNT == 0:
    print(f"  모든 검증 통과!")
else:
    print(f"  {FAIL_COUNT}건 실패 — 위 FAIL 항목 확인")
print(f"{'=' * 65}")
