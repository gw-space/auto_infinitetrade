"""40회차 전체 사이클을 시뮬레이션하고 텔레그램 + 구글 시트에 기록한다."""

import asyncio
import math
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_loader import load_config
from src.strategy.infinite_buy import calculate_daily_action, update_state_after_fill
from src.strategy.state import CycleState, reset_cycle
from src.logging_sheet.sheets import SheetsLogger
from src.charts.renderer import render_return_chart


def generate_prices(days: int = 50, start: float = 50.0) -> list[float]:
    """하락 → 횡보 → 반등 시나리오 (익절 발생)."""
    prices = []
    price = start
    for i in range(days):
        if i < 15:
            price *= random.uniform(0.975, 0.998)
        elif i < 30:
            price *= random.uniform(0.995, 1.005)
        else:
            price *= random.uniform(1.008, 1.025)
        prices.append(round(price, 2))
    return prices


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


async def main():
    random.seed(42)
    config = load_config()

    print("=" * 60)
    print("  40회차 전체 사이클 시뮬레이션")
    print("  텔레그램 알림 + 구글 시트 기록")
    print("=" * 60)

    # 텔레그램 봇
    from telegram import Bot
    bot = Bot(token=config.telegram.bot_token)
    chat_id = config.telegram.chat_id

    # 구글 시트
    sheets = SheetsLogger(
        spreadsheet_id=config.google_sheets.spreadsheet_id,
        credentials_path=config.google_sheets.credentials_path,
    )

    await bot.send_message(chat_id=chat_id, text="🚀 <b>40회차 전체 사이클 시뮬레이션 시작</b>", parse_mode="HTML")

    # 상태 초기화
    total_capital = 10000.0
    num_splits = 40
    profit_target_pct = 0.10

    state = CycleState(
        symbol="TQQQ",
        cycle_number=1,
        total_capital=total_capital,
        split_amount=total_capital / num_splits,
        num_splits=num_splits,
        profit_target_pct=profit_target_pct,
        over40_strategy="quarter",
        cycle_start_date="2026-01-02",
    )

    prices = generate_prices(days=50, start=50.0)
    start_date = date(2026, 1, 2)
    total_cost = 0.0
    total_sold = 0.0
    chart_dates = []
    chart_returns = []

    for day_idx, closing_price in enumerate(prices):
        today = (start_date + timedelta(days=day_idx)).isoformat()
        day_num = day_idx + 1

        action = calculate_daily_action(state, closing_price, state.total_shares)

        if action.should_skip and not action.over40_action:
            continue

        if action.over40_action:
            # 40회차 소진 시 중단
            break

        fills = simulate_loc_fills(action, closing_price)
        if not fills:
            # 미체결 기록
            return_pct = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
            target_price = state.avg_price * (1 + profit_target_pct) if state.avg_price > 0 else 0

            sheets.log_daily(
                cycle_number=1, today=today, symbol="TQQQ",
                current_price=closing_price, avg_price=state.avg_price,
                quantity=state.total_shares,
                loc_avg_price=state.avg_price,
                loc_high_price=target_price,
                action="미체결",
                fill_qty=0, fill_amount=0,
                splits_used=state.splits_used, num_splits=num_splits,
                return_pct=return_pct, usd_krw_rate=1380.0,
                eval_amount=closing_price * state.total_shares,
                realized_pnl=state.realized_pnl, notes="",
            )
            time.sleep(2)
            chart_dates.append(today)
            chart_returns.append(round(return_pct, 2))
            print(f"  Day {day_num:>2} | ${closing_price:>7.2f} | 미체결       | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {return_pct:>+7.2f}%")
            continue

        # 체결 처리
        fill_qty = 0
        fill_amount = 0.0
        action_str = ""
        is_sell = False

        for f in fills:
            if f["side"] == "buy":
                total_cost += f["amount"]
            update_state_after_fill(state, f["quantity"], f["price"], f["amount"], f["side"])
            fill_qty += f["quantity"]
            fill_amount += f["amount"]
            if f["side"] == "sell":
                total_sold += f["amount"]
                is_sell = True

        if action.is_cold_start:
            action_str = "시장가매수"
        elif is_sell:
            action_str = "익절매도"
        elif len([f for f in fills if f["side"] == "buy"]) == 2:
            action_str = "LOC양쪽"
        else:
            action_str = "LOC고가만"

        return_pct = (closing_price - state.avg_price) / state.avg_price * 100 if state.avg_price > 0 else 0
        target_price = state.avg_price * (1 + profit_target_pct) if state.avg_price > 0 else 0

        # 구글 시트 기록
        sheets.log_daily(
            cycle_number=1, today=today, symbol="TQQQ",
            current_price=closing_price, avg_price=state.avg_price,
            quantity=state.total_shares,
            loc_avg_price=state.avg_price,
            loc_high_price=target_price,
            action=action_str,
            fill_qty=fill_qty, fill_amount=fill_amount,
            splits_used=state.splits_used, num_splits=num_splits,
            return_pct=return_pct, usd_krw_rate=1380.0,
            eval_amount=closing_price * state.total_shares,
            realized_pnl=state.realized_pnl, notes="",
        )
        time.sleep(2)

        chart_dates.append(today)
        chart_returns.append(round(return_pct, 2))

        print(f"  Day {day_num:>2} | ${closing_price:>7.2f} | {action_str:<10} | {state.total_shares:>5}주 | {state.splits_used:>5.1f}/40 | {return_pct:>+7.2f}%")

        # 매일 텔레그램 알림 (5일마다 + 첫날/마지막)
        if day_num == 1 or day_num % 5 == 0 or is_sell:
            msg = (
                f"📊 <b>[TQQQ] Day {day_num} ({today})</b>\n\n"
                f"  현재가: ${closing_price:.2f}\n"
                f"  평균단가: ${state.avg_price:.2f}\n"
                f"  보유수량: {state.total_shares}주\n"
                f"  LOC 평단: ${state.avg_price:.2f}\n"
                f"  LOC 고가: ${target_price:.2f}\n"
                f"  분할: {state.splits_used:.1f}/{num_splits}\n"
                f"  수익률: {return_pct:+.2f}%\n"
                f"  액션: {action_str}"
            )
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

        # 익절 시 사이클 종료
        if is_sell and state.total_shares <= 0:
            profit = total_sold - total_cost
            profit_pct = (profit / total_cost * 100) if total_cost > 0 else 0

            # 사이클 요약 기록
            sheets.log_cycle_summary(
                cycle_number=1,
                start_date="2026-01-02",
                end_date=today,
                symbol="TQQQ",
                total_invested=total_cost,
                total_sold=total_sold,
                profit_usd=profit,
                usd_krw_rate=1380.0,
                return_pct=profit_pct,
                splits_used=state.splits_used,
                num_splits=num_splits,
                end_reason="익절",
            )

            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🏁 <b>[TQQQ] 사이클 #1 완료!</b>\n\n"
                    f"  소요일: {day_num}일\n"
                    f"  분할: {state.splits_used:.1f}/{num_splits}\n"
                    f"  투입: ${total_cost:,.2f}\n"
                    f"  매도: ${total_sold:,.2f}\n"
                    f"  <b>수익: ${profit:+,.2f} ({profit_pct:+.2f}%)</b>"
                ),
                parse_mode="HTML",
            )
            break

    # 차트 생성 + 발송
    if chart_dates and chart_returns:
        img = render_return_chart(chart_dates, chart_returns, "TQQQ", "사이클 #1")
        if img:
            from io import BytesIO
            bio = BytesIO(img)
            bio.name = "cycle_chart.png"
            await bot.send_photo(
                chat_id=chat_id,
                photo=bio,
                caption="<b>[TQQQ] 사이클 #1 수익률 추이</b>",
                parse_mode="HTML",
            )

    await bot.send_message(chat_id=chat_id, text="✅ <b>시뮬레이션 완료! 구글 시트를 확인하세요.</b>", parse_mode="HTML")
    print("\n  완료! 텔레그램과 구글 시트를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
