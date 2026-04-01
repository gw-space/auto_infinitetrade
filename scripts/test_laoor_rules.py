"""라오어 무한매수법 규칙 1:1 대응 검증.

각 규칙이 코드에서 정확히 구현되었는지 시뮬레이션으로 확인한다.
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

P = 0
F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        F += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def make(**kw):
    d = dict(symbol="T", cycle_number=1, total_capital=10000, split_amount=250,
             num_splits=40, splits_used=0, total_shares=0, total_invested=0,
             avg_price=0, realized_pnl=0, profit_target_pct=0.10, over40_strategy="quarter")
    d.update(kw)
    return CycleState(**d)


def loc_fills(action, cp):
    fills = []
    if action.is_cold_start:
        fills.append(("buy", action.cold_start_qty, cp))
        return fills
    if action.loc_buy_avg_qty > 0 and cp <= action.loc_buy_avg_price:
        fills.append(("buy", action.loc_buy_avg_qty, cp))
    if action.loc_buy_high_qty > 0 and cp <= action.loc_buy_high_price:
        fills.append(("buy", action.loc_buy_high_qty, cp))
    if action.limit_sell_qty > 0 and cp >= action.limit_sell_price:
        fills.append(("sell", action.limit_sell_qty, cp))
    return fills


print("=" * 70)
print("  라오어 무한매수법 규칙 1:1 대응 검증")
print("=" * 70)

# ────────────────────────────────────────
print("\n■ 규칙 1: 총 투자금 40분할")
# ────────────────────────────────────────
s = make()
check("split_amount = 총자금/40", s.split_amount == 250.0, f"10000/40={s.split_amount}")

# ────────────────────────────────────────
print("\n■ 규칙 2: 1회차 즉시 매수 (전체 1회차 금액)")
# ────────────────────────────────────────
s = make()
a = calculate_daily_action(s, 50.0, 0)
check("1회차 cold_start", a.is_cold_start)
check("1회차 수량 = floor(250/50) = 5", a.cold_start_qty == 5)
check("1회차 LOC 없음", a.loc_buy_avg_qty == 0 and a.loc_buy_high_qty == 0)
check("1회차 매도 없음", a.limit_sell_qty == 0)

# ────────────────────────────────────────
print("\n■ 규칙 3: 2회차~ LOC 평단 0.5회차 + LOC 고가 0.5회차")
# ────────────────────────────────────────
s = make(splits_used=5, total_shares=50, total_invested=2500, avg_price=50.0)
a = calculate_daily_action(s, 48.0, 50)
target = round(50.0 * 1.1, 2)  # 55.0
check("LOC 평단가 = 평균단가", a.loc_buy_avg_price == 50.0)
check("LOC 고가 = 평단*1.1", a.loc_buy_high_price == target, f"{a.loc_buy_high_price}")
check("LOC 평단 수량 = floor(125/50) = 2", a.loc_buy_avg_qty == 2)
check("LOC 고가 수량 = floor(125/55) = 2", a.loc_buy_high_qty == 2)

# ────────────────────────────────────────
print("\n■ 규칙 4: 매일 지정가 매도 (목표익절가, 기존보유 전량)")
# ────────────────────────────────────────
check("매도 수량 = 보유 전량", a.limit_sell_qty == 50)
check("매도가 = 목표익절가", a.limit_sell_price == target)

# ────────────────────────────────────────
print("\n■ 규칙 5: LOC 체결 조건")
# ────────────────────────────────────────
# 5a: 하락일 (종가 <= 평단) → 양쪽 체결
fills = loc_fills(a, 48.0)
buy_fills = [f for f in fills if f[0] == "buy"]
check("하락일: LOC 양쪽 체결", len(buy_fills) == 2, f"종가 48 <= 평단 50, 종가 48 <= 고가 55")

# 5b: 횡보일 (평단 < 종가 <= 고가) → 고가만
fills = loc_fills(a, 52.0)
buy_fills = [f for f in fills if f[0] == "buy"]
check("횡보일: LOC 고가만", len(buy_fills) == 1, f"종가 52 > 평단 50, 종가 52 <= 고가 55")

# 5c: 급등일 (종가 > 고가) → 미체결 + 매도 체결
fills = loc_fills(a, 56.0)
buy_fills = [f for f in fills if f[0] == "buy"]
sell_fills = [f for f in fills if f[0] == "sell"]
check("급등일: 매수 미체결", len(buy_fills) == 0, f"종가 56 > 고가 55")
check("급등일: 매도 체결", len(sell_fills) == 1, f"종가 56 >= 매도가 55")

# 5d: 종가 == 평단 (경계) → 양쪽 체결
fills = loc_fills(a, 50.0)
buy_fills = [f for f in fills if f[0] == "buy"]
check("종가=평단: 양쪽 체결", len(buy_fills) == 2, f"종가 50 <= 평단 50")

# 5e: 종가 == 고가 (경계) → 고가 체결 + 매도 체결
fills = loc_fills(a, 55.0)
buy_fills = [f for f in fills if f[0] == "buy"]
sell_fills = [f for f in fills if f[0] == "sell"]
check("종가=고가: 고가 체결", len(buy_fills) == 1)
check("종가=고가: 매도 체결", len(sell_fills) == 1)

# ────────────────────────────────────────
print("\n■ 규칙 6: 수량 계산 내림(floor)")
# ────────────────────────────────────────
s = make(splits_used=5, total_shares=10, total_invested=700, avg_price=70.0)
a = calculate_daily_action(s, 65.0, 10)
check("floor(125/70) = 1", a.loc_buy_avg_qty == 1, f"실제: {a.loc_buy_avg_qty}")
target70 = round(70 * 1.1, 2)  # 77
check("floor(125/77) = 1", a.loc_buy_high_qty == 1, f"실제: {a.loc_buy_high_qty}")

# ────────────────────────────────────────
print("\n■ 규칙 7: splits_used는 실제 체결 금액 기준")
# ────────────────────────────────────────
s = make(splits_used=5, total_shares=50, total_invested=2500, avg_price=50.0)
update_state_after_fill(s, 3, 48.0, 144.0, "buy")
expected_splits = 5 + 144.0 / 250.0  # 5.576
check("splits_used = 기존 + 체결금/split_amount", abs(s.splits_used - expected_splits) < 0.01,
      f"expected={expected_splits:.3f}, actual={s.splits_used:.3f}")

# ────────────────────────────────────────
print("\n■ 규칙 8: 40회차 소진 (남은 분할 < 1.0)")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0)
a = calculate_daily_action(s, 38.0, 200)
check("remaining 0.5 < 1.0 → 소진", s.pending_sell, f"splits={s.splits_used}")
check("over40_action 발생", a.over40_action != "")

# ────────────────────────────────────────
print("\n■ 규칙 9: quarter 전략 (1/4 매도, 시드 재확보)")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0)
a = calculate_daily_action(s, 38.0, 200)
check("quarter_sell 발동", a.over40_action == "quarter_sell")
check("1/4 수량 = 50", a.quarter_sell_qty == 50, f"200//4={a.quarter_sell_qty}")

# quarter 실행
apply_quarter_sell_result(s, 50, 50 * 38.0)
check("보유 = 150", s.total_shares == 150)
check("quarter_used = True", s.quarter_used)
check("pending_sell = False (매수 재개)", s.pending_sell == False)
check("splits_used 차감됨", s.splits_used < 39.5, f"splits={s.splits_used:.1f}")

# quarter 후 일반 주문 재개
a2 = calculate_daily_action(s, 37.0, 150)
check("quarter 후 LOC 주문 재개", a2.loc_buy_avg_qty > 0 or a2.loc_buy_high_qty > 0,
      f"avg={a2.loc_buy_avg_qty}, high={a2.loc_buy_high_qty}")

# ────────────────────────────────────────
print("\n■ 규칙 10: quarter 1회만, 재소진 시 full_exit")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=150, total_invested=6000, avg_price=40.0,
         quarter_used=True)
a = calculate_daily_action(s, 35.0, 150)
check("quarter_used → full_exit 전환", a.over40_action == "full_exit")
check("full_exit 수량 = 전량", a.full_exit_qty == 150)

# ────────────────────────────────────────
print("\n■ 규칙 11: 익절 후 사이클 리셋")
# ────────────────────────────────────────
s = make(cycle_number=1, total_capital=10000, realized_pnl=500)
reset_cycle(s, "2026-04-01", available_cash=10500, capital_limit=10000)
check("사이클 번호 +1", s.cycle_number == 2)
check("자본금 = min(잔고, 상한)", s.total_capital == 10000)
check("realized_pnl 초기화", s.realized_pnl == 0)
check("splits_used 초기화", s.splits_used == 0)
check("total_shares 초기화", s.total_shares == 0)
check("quarter_used 초기화", s.quarter_used == False)
check("split_amount 재계산", s.split_amount == 10000 / 40)

# ────────────────────────────────────────
print("\n■ 규칙 12: lower_target (목표 5% 하향)")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0,
         over40_strategy="lower_target")
a = calculate_daily_action(s, 38.0, 200)
check("lower_target 실행", a.over40_action == "lower_target")
check("profit_target 5%로 하향", s.profit_target_pct == 0.05, f"{s.profit_target_pct}")
check("매도가 = 40*1.05 = 42", a.limit_sell_price == 42.0, f"{a.limit_sell_price}")
check("매수 없음 (매도만)", a.loc_buy_avg_qty == 0 and a.loc_buy_high_qty == 0)

# lower_target 2일차: 여전히 매수 없음
a2 = calculate_daily_action(s, 39.0, 200)
check("lower_target 2일차: over40_action 유지", a2.over40_action == "lower_target")
check("lower_target 2일차: 매수 없음", a2.loc_buy_avg_qty == 0 and a2.loc_buy_high_qty == 0)

# ────────────────────────────────────────
print("\n■ 규칙 13: hold (매수 중단, 매도만)")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0,
         over40_strategy="hold")
a = calculate_daily_action(s, 38.0, 200)
check("hold: over40_action", a.over40_action == "hold")
check("hold: 매수 없음", a.loc_buy_avg_qty == 0 and a.loc_buy_high_qty == 0)
check("hold: 매도 유지", a.limit_sell_qty == 200)
check("hold: 매도가 = 44", a.limit_sell_price == 44.0)

# ────────────────────────────────────────
print("\n■ 규칙 14: full_exit (전량 매도)")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0,
         over40_strategy="full_exit")
a = calculate_daily_action(s, 38.0, 200)
check("full_exit: 전량 매도", a.over40_action == "full_exit")
check("full_exit: 수량 = 200", a.full_exit_qty == 200)

# ────────────────────────────────────────
print("\n■ 규칙 15: 평균단가 계산")
# ────────────────────────────────────────
s = make(splits_used=2, total_shares=10, total_invested=500, avg_price=50.0)
update_state_after_fill(s, 5, 45.0, 225.0, "buy")
expected_avg = (500 + 225) / 15
check("평단 = 총투입/총수량", abs(s.avg_price - expected_avg) < 0.01,
      f"expected={expected_avg:.2f}, actual={s.avg_price:.2f}")

# ────────────────────────────────────────
print("\n■ 규칙 16: 매도 시 손익 계산")
# ────────────────────────────────────────
s = make(splits_used=10, total_shares=100, total_invested=5000, avg_price=50.0)
update_state_after_fill(s, 100, 55.0, 5500.0, "sell")
check("전량 매도: 보유 0주", s.total_shares == 0)
check("수익 = 5500-5000 = 500", abs(s.realized_pnl - 500) < 0.01)
check("total_invested 초기화", s.total_invested == 0)

# 부분 매도
s2 = make(splits_used=10, total_shares=100, total_invested=5000, avg_price=50.0)
update_state_after_fill(s2, 60, 55.0, 3300.0, "sell")
check("부분 매도: 잔여 40주", s2.total_shares == 40)
check("부분 수익 = 3300-3000 = 300", abs(s2.realized_pnl - 300) < 0.01)
check("투자금 비례 차감", abs(s2.total_invested - 50 * 40) < 0.01)

# ────────────────────────────────────────
print("\n■ 규칙 17: 수익률 계산")
# ────────────────────────────────────────
s = make(splits_used=10, total_shares=100, avg_price=50.0)
a = calculate_daily_action(s, 55.0, 100)
check("+10% 수익률", abs(a.return_pct - 10.0) < 0.01)
a = calculate_daily_action(s, 45.0, 100)
check("-10% 수익률", abs(a.return_pct - (-10.0)) < 0.01)

# ────────────────────────────────────────
print("\n■ 규칙 18: 일시 중지 (/pause)")
# ────────────────────────────────────────
s = make(splits_used=10, total_shares=50, avg_price=48.0, is_paused=True)
a = calculate_daily_action(s, 45.0, 50)
check("pause: 스킵", a.should_skip)
check("pause: 사유", "중지" in a.skip_reason)

# ────────────────────────────────────────
print("\n■ 규칙 19: 고가 종목 (0주 매수)")
# ────────────────────────────────────────
s = make(total_capital=5000, split_amount=125)
a = calculate_daily_action(s, 500.0, 0)
check("1회차 불가 (125 < 500)", a.should_skip)

# ────────────────────────────────────────
print("\n■ 규칙 20: 연속 사이클 (복리)")
# ────────────────────────────────────────
s = make()
for cycle in range(3):
    prices = [50 * (0.995 ** i) for i in range(10)]
    prices += [prices[-1] * (1.01 ** i) for i in range(1, 25)]
    for cp in prices:
        a = calculate_daily_action(s, cp, s.total_shares)
        if a.should_skip:
            continue
        fills = loc_fills(a, cp)
        for side, qty, price in fills:
            update_state_after_fill(s, qty, price, price * qty, side)
        if s.total_shares <= 0 and any(f[0] == "sell" for f in fills):
            break
    if s.total_shares <= 0:
        reset_cycle(s, "2026-04-01", available_cash=s.total_capital + s.realized_pnl, capital_limit=10000)

check("3사이클 후 cycle_number = 4", s.cycle_number == 4)
check("상한 적용 유지", s.total_capital <= 10000)

# ────────────────────────────────────────
print("\n■ 규칙 21: pending_sell 상태에서 매수 없음")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=200, total_invested=8000, avg_price=40.0,
         pending_sell=True, over40_strategy="hold")
a = calculate_daily_action(s, 38.0, 200)
check("pending_sell: LOC 매수 없음", a.loc_buy_avg_qty == 0 and a.loc_buy_high_qty == 0)
check("pending_sell: 매도 유지", a.limit_sell_qty == 200)

# ────────────────────────────────────────
print("\n■ 규칙 22: 새 사이클 자본금 = min(잔고, 상한)")
# ────────────────────────────────────────
# 잔고 < 상한
s = make(realized_pnl=0)
reset_cycle(s, "2026-04-01", available_cash=8000, capital_limit=10000)
check("잔고 8000 < 상한 10000 → 8000", s.total_capital == 8000)

# 잔고 > 상한
s2 = make(realized_pnl=0)
reset_cycle(s2, "2026-04-01", available_cash=15000, capital_limit=10000)
check("잔고 15000 > 상한 10000 → 10000", s2.total_capital == 10000)

# 잔고 미조회
s3 = make(realized_pnl=500)
reset_cycle(s3, "2026-04-01", available_cash=0, capital_limit=0)
check("잔고 미조회 → 원금+수익 = 10500", s3.total_capital == 10500)

# ────────────────────────────────────────
print("\n■ 규칙 23: 보유 0주에서 40회차 전략 → 스킵")
# ────────────────────────────────────────
s = make(splits_used=39.5, total_shares=0, total_invested=0, avg_price=40.0, pending_sell=True)
a = calculate_daily_action(s, 38.0, 0)
check("보유 0주 → should_skip", a.should_skip)

# ────────────────────────────────────────
# 결과
# ────────────────────────────────────────
print(f"\n{'=' * 70}")
print(f"  결과: {P} PASS / {F} FAIL / 총 {P + F}건")
if F == 0:
    print("  라오어 무한매수법 전체 규칙 검증 통과!")
else:
    print(f"  {F}건 실패 — 위 FAIL 확인")
print(f"{'=' * 70}")
