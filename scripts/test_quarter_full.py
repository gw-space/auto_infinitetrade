"""Quarter 전략 발동 1사이클 풀 시뮬레이션 + 구글 시트 기록 + 텔레그램 알림."""

import asyncio
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_loader import load_config
from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import CycleState
from src.logging_sheet.sheets import SheetsLogger
from src.charts.renderer import render_return_chart


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


def generate_prices():
    """40회차 소진 → quarter 발동 → 매수 재개 → 익절."""
    prices = []
    p = 50.0
    # 60일 하락 (매일 0.5% 하락 → 40분할 소진)
    for _ in range(60):
        p *= 0.995
        prices.append(round(p, 2))
    # 10일 바닥 횡보
    for _ in range(10):
        p *= 1.0005
        prices.append(round(p, 2))
    # 30일 반등 (익절까지)
    for _ in range(30):
        p *= 1.008
        prices.append(round(p, 2))
    return prices


async def main():
    config = load_config()

    from telegram import Bot
    bot = Bot(token=config.telegram.bot_token)
    chat_id = config.telegram.chat_id

    sheets = SheetsLogger(
        spreadsheet_id=config.google_sheets.spreadsheet_id,
        credentials_path=config.google_sheets.credentials_path,
    )

    print("=" * 65)
    print("  Quarter 전략 풀 사이클 시뮬레이션")
    print("  텔레그램 + 구글 시트 기록")
    print("=" * 65)

    await bot.send_message(chat_id=chat_id,
        text="🚀 <b>Quarter 전략 풀 사이클 시뮬레이션 시작</b>\n$10,000 / 40분할 / 목표 10%",
        parse_mode="HTML")

    state = CycleState(
        symbol="TQQQ", cycle_number=1,
        total_capital=10000.0, split_amount=250.0,
        num_splits=40, profit_target_pct=0.10,
        over40_strategy="quarter",
        cycle_start_date="2026-01-02",
    )

    prices = generate_prices()
    start_date = date(2026, 1, 2)
    total_cost = 0.0
    total_sold = 0.0
    chart_dates = []
    chart_returns = []
    quarter_happened = False

    print(f"{'일차':>4} | {'종가':>8} | {'액션':<16} | {'체결':>6} | {'평단':>8} | {'보유':>6} | {'분할':>8} | {'수익률':>8}")
    print(f"{'-'*4}-+-{'-'*8}-+-{'-'*16}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}")

    for day_idx, cp in enumerate(prices):
        today_str = (start_date + timedelta(days=day_idx)).isoformat()
        day_num = day_idx + 1

        action = calculate_daily_action(state, cp, state.total_shares)
        target_price = round(state.avg_price * (1 + state.profit_target_pct), 2) if state.avg_price > 0 else 0

        # === 40회차 전략 처리 ===
        if action.over40_action:
            if action.over40_action == "quarter_sell" and not state.over40_executed:
                quarter_happened = True
                qty = action.quarter_sell_qty
                amt = round(cp * qty, 2)
                total_sold += amt
                apply_quarter_sell_result(state, qty, amt)
                target_price = round(state.avg_price * (1 + state.profit_target_pct), 2)
                ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0

                a_str = f"QUARTER 1/4({qty}주)"
                print(f"{day_num:>4} | ${cp:>7.2f} | {a_str:<16} | {qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")

                sheets.log_daily(
                    cycle_number=1, today=today_str, symbol="TQQQ",
                    current_price=cp, avg_price=state.avg_price, quantity=state.total_shares,
                    loc_avg_price=0, loc_high_price=0,
                    action=f"QUARTER 1/4 매도 {qty}주",
                    fill_qty=qty, fill_amount=amt,
                    splits_used=state.splits_used, num_splits=40,
                    return_pct=ret, usd_krw_rate=1380.0,
                    eval_amount=cp * state.total_shares,
                    realized_pnl=state.realized_pnl,
                    notes=f"40회차 소진 → quarter 전략: {qty}주(1/4) 매도, splits {state.splits_used:.1f}/40으로 복원",
                )
                time.sleep(2)

                await bot.send_message(chat_id=chat_id, text=(
                    f"⚠️ <b>[TQQQ] Day {day_num} - 40회차 소진! Quarter 전략 실행</b>\n\n"
                    f"  1/4 매도: {qty}주 @ ${cp:.2f}\n"
                    f"  분할 복원: {state.splits_used:.1f}/40\n"
                    f"  평균단가: ${state.avg_price:.2f}\n"
                    f"  잔여: {state.total_shares}주\n"
                    f"  매수 재개됩니다."
                ), parse_mode="HTML")

                chart_dates.append(today_str)
                chart_returns.append(round(ret, 2))
                continue

            # quarter 실행 후 hold/lower_target 등은 여기서 처리
            fills = simulate_loc_fills(action, cp)
            for f in fills:
                if f["side"] == "buy": total_cost += f["amount"]
                update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
                if f["side"] == "sell": total_sold += f["amount"]

            if state.total_shares <= 0:
                ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
                print(f"{day_num:>4} | ${cp:>7.2f} | {'SELL(40→익절)':<16} | {state.total_shares:>5}주 | ${state.avg_price:>7.2f} | {0:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")
                break
            continue

        # === 스킵 ===
        if action.should_skip:
            continue

        # === 일반 LOC ===
        fills = simulate_loc_fills(action, cp)
        fill_qty = 0
        fill_amt = 0.0
        is_sell = False

        for f in fills:
            if f["side"] == "buy": total_cost += f["amount"]
            update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
            fill_qty += f["quantity"]
            fill_amt += f["amount"]
            if f["side"] == "sell":
                total_sold += f["amount"]
                is_sell = True

        # 액션 문자열
        if action.is_cold_start:
            a_str = "시장가매수"
        elif not fills:
            a_str = "미체결"
        elif is_sell:
            a_str = "SELL(익절)"
        else:
            buy_fills = [f for f in fills if f["side"] == "buy"]
            if len(buy_fills) == 2:
                a_str = "LOC 양쪽"
            elif len(buy_fills) == 1:
                # 어떤 LOC가 체결됐는지 구분
                if action.loc_buy_avg_qty > 0 and action.loc_buy_high_qty == 0:
                    a_str = "LOC 평단만"
                elif action.loc_buy_avg_qty == 0 and action.loc_buy_high_qty > 0:
                    a_str = "LOC 고가만"
                else:
                    # 양쪽 주문했는데 한쪽만 체결
                    fill_price = buy_fills[0]["price"]
                    if fill_price <= action.loc_buy_avg_price:
                        a_str = "LOC 평단만"
                    else:
                        a_str = "LOC 고가만"
            else:
                a_str = "미체결"

        ret = (cp - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
        target_price = round(state.avg_price * (1 + state.profit_target_pct), 2) if state.avg_price > 0 else 0

        print(f"{day_num:>4} | ${cp:>7.2f} | {a_str:<16} | {fill_qty:>5}주 | ${state.avg_price:>7.2f} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {ret:>+7.2f}%")

        # 구글 시트 기록
        notes = ""
        if quarter_happened and a_str.startswith("LOC"):
            notes = "quarter 후 매수 재개"
            quarter_happened = False

        sheets.log_daily(
            cycle_number=1, today=today_str, symbol="TQQQ",
            current_price=cp, avg_price=state.avg_price, quantity=state.total_shares,
            loc_avg_price=state.avg_price if action.loc_buy_avg_qty > 0 else 0,
            loc_high_price=target_price if action.loc_buy_high_qty > 0 or action.limit_sell_qty > 0 else 0,
            action=a_str,
            fill_qty=fill_qty, fill_amount=fill_amt,
            splits_used=state.splits_used, num_splits=40,
            return_pct=ret, usd_krw_rate=1380.0,
            eval_amount=cp * state.total_shares,
            realized_pnl=state.realized_pnl,
            notes=notes,
        )
        time.sleep(2)

        chart_dates.append(today_str)
        chart_returns.append(round(ret, 2))

        # 텔레그램 (주요 이벤트만)
        if day_num == 1 or day_num % 10 == 0 or is_sell:
            await bot.send_message(chat_id=chat_id, text=(
                f"📊 <b>[TQQQ] Day {day_num} ({today_str})</b>\n\n"
                f"  현재가: ${cp:.2f} | 평단: ${state.avg_price:.2f}\n"
                f"  보유: {state.total_shares}주 | 수익률: {ret:+.2f}%\n"
                f"  LOC 평단: ${state.avg_price:.2f} | LOC 고가: ${target_price:.2f}\n"
                f"  분할: {state.splits_used:.1f}/40 | 액션: {a_str}"
            ), parse_mode="HTML")

        if is_sell and state.total_shares <= 0:
            break

    # 사이클 종료
    profit = total_sold - total_cost
    profit_pct = (profit / total_cost * 100) if total_cost > 0 else 0

    print(f"\n{'='*65}")
    print(f"  사이클 완료!")
    print(f"  소요: {day_num}일 | 투입: ${total_cost:,.2f} | 매도: ${total_sold:,.2f}")
    print(f"  수익: ${profit:+,.2f} ({profit_pct:+.2f}%)")
    print(f"{'='*65}")

    # 사이클 요약 시트
    sheets.log_cycle_summary(
        cycle_number=1, start_date="2026-01-02", end_date=today_str,
        symbol="TQQQ", total_invested=total_cost, total_sold=total_sold,
        profit_usd=profit, usd_krw_rate=1380.0, return_pct=profit_pct,
        splits_used=state.splits_used, num_splits=40,
        end_reason="quarter→매수재개→익절",
    )

    await bot.send_message(chat_id=chat_id, text=(
        f"🏁 <b>[TQQQ] Quarter 사이클 완료!</b>\n\n"
        f"  소요: {day_num}일\n"
        f"  투입: ${total_cost:,.2f}\n"
        f"  매도: ${total_sold:,.2f}\n"
        f"  <b>수익: ${profit:+,.2f} ({profit_pct:+.2f}%)</b>\n\n"
        f"  40회차 소진 → quarter 1/4 매도 → 매수 재개 → 익절"
    ), parse_mode="HTML")

    # 차트
    if chart_dates:
        img = render_return_chart(chart_dates, chart_returns, "TQQQ", "Quarter 사이클")
        if img:
            from io import BytesIO
            bio = BytesIO(img)
            bio.name = "quarter_cycle.png"
            await bot.send_photo(chat_id=chat_id, photo=bio,
                caption="<b>[TQQQ] Quarter 사이클 수익률 추이</b>", parse_mode="HTML")

    await bot.send_message(chat_id=chat_id,
        text="✅ <b>시뮬레이션 완료! 구글 시트를 확인하세요.</b>", parse_mode="HTML")

    print("\n  완료! 텔레그램과 구글 시트를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
