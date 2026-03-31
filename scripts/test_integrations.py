"""텔레그램 + 구글 스프레드시트 연동 테스트.

사용법:
  python scripts/test_integrations.py telegram   # 텔레그램만
  python scripts/test_integrations.py sheets      # 구글 시트만
  python scripts/test_integrations.py all         # 전부
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_loader import load_config


# === 텔레그램 테스트 ===

async def test_telegram():
    print("\n=== 텔레그램 테스트 ===\n")
    config = load_config()

    from telegram import Bot

    bot = Bot(token=config.telegram.bot_token)
    chat_id = config.telegram.chat_id

    # 1. 기본 메시지
    print("[1/4] 기본 메시지 발송...")
    await bot.send_message(
        chat_id=chat_id,
        text="테스트: 무한매수법 봇 연동 테스트입니다.",
    )
    print("  -> 성공")

    # 2. HTML 포맷 메시지
    print("[2/4] HTML 포맷 메시지...")
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "<b>[TQQQ] 무한매수법 테스트 리포트</b>\n\n"
            "■ 보유 현황\n"
            "  평균단가: $45.32\n"
            "  보유수량: 127주\n"
            "  현재 수익률: <b>-3.25%</b>\n\n"
            "■ 오늘 LOC 주문\n"
            "  LOC 매수(평단): $45.32 x 6주\n"
            "  LOC 매수(고가): $49.85 x 5주\n"
            "  지정가 매도: $49.85 x 127주 (전량)\n\n"
            "■ 사이클: 15.0/40 분할 사용"
        ),
        parse_mode="HTML",
    )
    print("  -> 성공")

    # 3. 40회차 소진 알림 형식
    print("[3/4] 40회차 소진 알림...")
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "⚠️ <b>[TQQQ] 40회차 소진, 쿼터매도 (1/4 매도 후 매수 재개) 실행됨, "
            "현재 손익률 -8.50%</b>\n\n"
            "  평균단가: $45.32\n"
            "  보유수량: 200주\n"
            "  전략: <b>quarter</b>\n\n"
            "  수동 전량 매도: /sell TQQQ → /confirm_sell TQQQ"
        ),
        parse_mode="HTML",
    )
    print("  -> 성공")

    # 4. 차트 이미지 (matplotlib)
    print("[4/4] 차트 이미지 발송...")
    try:
        from src.charts.renderer import render_return_chart
        dates = [
            "2026-03-20", "2026-03-21", "2026-03-24", "2026-03-25",
            "2026-03-26", "2026-03-27", "2026-03-28", "2026-03-31",
        ]
        returns = [-1.2, -3.5, -2.8, -1.0, 0.5, 2.3, 4.1, 6.8]
        img = render_return_chart(dates, returns, "TQQQ", "주간")

        if img:
            from io import BytesIO
            bio = BytesIO(img)
            bio.name = "chart.png"
            await bot.send_photo(
                chat_id=chat_id,
                photo=bio,
                caption="<b>[TQQQ] 주간 수익률 차트 (테스트)</b>",
                parse_mode="HTML",
            )
            print("  -> 성공")
        else:
            print("  -> 차트 생성 실패 (빈 바이트)")
    except Exception as e:
        print(f"  -> 차트 실패: {e}")

    print("\n텔레그램 테스트 완료! 메시지를 확인하세요.\n")


# === 구글 스프레드시트 테스트 ===

def test_sheets():
    print("\n=== 구글 스프레드시트 테스트 ===\n")
    config = load_config()

    from src.logging_sheet.sheets import SheetsLogger

    sheets = SheetsLogger(
        spreadsheet_id=config.google_sheets.spreadsheet_id,
        credentials_path=config.google_sheets.credentials_path,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 연결 테스트
    print("[1/4] 구글 시트 연결...")
    try:
        sheets._connect()
        print(f"  -> 성공 (스프레드시트 ID: {config.google_sheets.spreadsheet_id[:20]}...)")
    except Exception as e:
        print(f"  -> 실패: {e}")
        return

    # 2. 일별 기록 추가
    print("[2/4] 일별 기록 추가...")
    try:
        sheets.log_daily(
            cycle_number=1,
            today=now,
            symbol="TEST",
            current_price=50.25,
            avg_price=48.30,
            quantity=127,
            loc_avg_price=48.30,
            loc_high_price=53.13,
            action="test_buy",
            fill_qty=6,
            fill_amount=301.50,
            splits_used=15.5,
            num_splits=40,
            return_pct=4.04,
            usd_krw_rate=1380.50,
            eval_amount=6381.75,
            realized_pnl=0.0,
            notes=f"연동 테스트 ({now})",
        )
        print("  -> 성공 (일별 기록 시트 확인)")
    except Exception as e:
        print(f"  -> 실패: {e}")

    # 3. 사이클 요약 추가
    print("[3/4] 사이클 요약 추가...")
    try:
        sheets.log_cycle_summary(
            cycle_number=0,
            start_date="2026-03-01",
            end_date=now,
            symbol="TEST",
            total_invested=5000.0,
            total_sold=5500.0,
            profit_usd=500.0,
            usd_krw_rate=1380.50,
            return_pct=10.0,
            splits_used=25.5,
            num_splits=40,
            end_reason=f"테스트 ({now})",
        )
        print("  -> 성공 (사이클 요약 시트 확인)")
    except Exception as e:
        print(f"  -> 실패: {e}")

    # 4. 시트 목록 확인
    print("[4/4] 시트 탭 목록...")
    try:
        worksheets = sheets._spreadsheet.worksheets()
        for ws in worksheets:
            print(f"  - {ws.title} ({ws.row_count} rows)")
        print("  -> 성공")
    except Exception as e:
        print(f"  -> 실패: {e}")

    print("\n구글 시트 테스트 완료! 스프레드시트를 확인하세요.\n")


# === 메인 ===

def main():
    if len(sys.argv) < 2:
        target = "all"
    else:
        target = sys.argv[1].lower()

    print("=" * 50)
    print("  무한매수법 봇 - 연동 테스트")
    print("=" * 50)

    # .env 확인
    env_path = Path(".env")
    if not env_path.exists():
        print("\n.env 파일이 없습니다!")
        print("cp .env.example .env 후 실제 값을 입력하세요.")
        return

    if target in ("telegram", "all"):
        asyncio.run(test_telegram())

    if target in ("sheets", "all"):
        test_sheets()

    if target not in ("telegram", "sheets", "all"):
        print(f"\n알 수 없는 대상: {target}")
        print("사용법: python scripts/test_integrations.py [telegram|sheets|all]")


if __name__ == "__main__":
    main()
