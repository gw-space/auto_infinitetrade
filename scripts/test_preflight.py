"""실전 투입 전 종합 점검 스크립트.

사용법:
  python -m scripts.test_preflight .env.live
  python -m scripts.test_preflight .env.paper
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def mask(val: str) -> str:
    """민감 정보를 마스킹한다."""
    if not val:
        return "(미설정)"
    if len(val) <= 6:
        return "***"
    return val[:3] + "***" + val[-3:]


async def test_kis(env_label: str, is_paper: bool):
    """KIS API 테스트: 토큰, 현재가, 잔고, 주문가능금액."""
    from src.kis.client import KISClient
    from src.kis.auth import ensure_token, TOKEN_CACHE_PATH
    from src.kis.account import get_holdings, get_available_cash
    from src.kis.market import get_current_price

    base_url = (
        "https://openapivts.koreainvestment.com:29443"
        if is_paper
        else "https://openapi.koreainvestment.com:9443"
    )

    client = KISClient(
        base_url=base_url,
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_number=os.getenv("KIS_ACCOUNT_NUMBER", ""),
        is_paper=is_paper,
    )

    # 캐시 토큰 삭제 (깨끗한 테스트)
    if TOKEN_CACHE_PATH.exists():
        TOKEN_CACHE_PATH.unlink()

    results = {}

    try:
        # 1) 토큰 발급
        await ensure_token(client)
        results["토큰 발급"] = "OK"
    except Exception as e:
        results["토큰 발급"] = f"FAIL - {e}"
        await client.close()
        return results

    try:
        # 2) 현재가 조회
        price = await get_current_price(client, "TQQQ", "NASD")
        results["현재가 조회 (TQQQ)"] = f"OK - ${price:.2f}"
    except Exception as e:
        results["현재가 조회 (TQQQ)"] = f"FAIL - {e}"

    try:
        # 3) 잔고 조회
        holdings = await get_holdings(client)
        if holdings:
            summary = ", ".join(f"{h.symbol} {h.quantity}주" for h in holdings)
            results["잔고 조회"] = f"OK - {summary}"
        else:
            results["잔고 조회"] = "OK - 보유 종목 없음"
    except Exception as e:
        results["잔고 조회"] = f"FAIL - {e}"

    try:
        # 4) 주문 가능 금액
        cash = await get_available_cash(client, "TQQQ")
        results["주문 가능 금액"] = f"OK - ${cash:.2f}"
    except Exception as e:
        results["주문 가능 금액"] = f"FAIL - {e}"

    await client.close()
    return results


async def test_telegram():
    """텔레그램 봇 메시지 발송 테스트."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return {"텔레그램 발송": "SKIP - 토큰/채팅ID 미설정"}

    try:
        from telegram import Bot
        bot = Bot(token=token)
        env_name = os.getenv("KIS_ENV", "unknown")
        await bot.send_message(
            chat_id=chat_id,
            text=f"[preflight 테스트] {env_name} 환경 텔레그램 연동 정상",
        )
        return {"텔레그램 발송": "OK"}
    except Exception as e:
        return {"텔레그램 발송": f"FAIL - {e}"}


async def test_google_sheets():
    """구글 시트 연결 + 쓰기/삭제 테스트."""
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json")

    if not spreadsheet_id:
        return {"구글 시트 연결": "SKIP - 스프레드시트 ID 미설정", "구글 시트 쓰기": "SKIP"}

    if not Path(creds_path).exists():
        return {"구글 시트 연결": f"FAIL - credentials 파일 없음: {creds_path}", "구글 시트 쓰기": "SKIP"}

    results = {}
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(spreadsheet_id)
        title = sheet.title
        tab_count = len(sheet.worksheets())
        results["구글 시트 연결"] = f"OK - '{title}' ({tab_count}개 탭)"
    except Exception as e:
        return {"구글 시트 연결": f"FAIL - {e}", "구글 시트 쓰기": "SKIP"}

    # 쓰기/삭제 테스트
    try:
        TEST_SHEET_NAME = "_preflight_test"
        # 테스트 시트 생성
        try:
            ws = sheet.worksheet(TEST_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=TEST_SHEET_NAME, rows=5, cols=5)

        # 테스트 행 쓰기
        ws.append_row(
            ["preflight", "테스트", "쓰기", "성공", "삭제예정"],
            value_input_option="USER_ENTERED",
        )

        # 기록 확인
        vals = ws.get_all_values()
        if any("preflight" in row for row in vals):
            results["구글 시트 쓰기"] = "OK - 쓰기 확인 완료"
        else:
            results["구글 시트 쓰기"] = "FAIL - 쓰기 후 데이터 없음"

        # 테스트 시트 삭제
        sheet.del_worksheet(ws)

    except Exception as e:
        results["구글 시트 쓰기"] = f"FAIL - {e}"

    return results


async def test_exchange_rate():
    """USD/KRW 환율 조회 테스트."""
    try:
        from src.utils.exchange_rate import get_usd_krw_rate
        rate = await get_usd_krw_rate()
        if rate > 0:
            return {"환율 조회": f"OK - ₩{rate:.2f}"}
        else:
            return {"환율 조회": "FAIL - 0 반환"}
    except Exception as e:
        return {"환율 조회": f"FAIL - {e}"}


async def test_market_calendar():
    """시장 캘린더 테스트."""
    try:
        from src.utils.market_calendar import is_trading_day
        today_is_trading = is_trading_day()
        label = "거래일" if today_is_trading else "휴장일"
        return {"시장 캘린더": f"OK - 오늘은 {label}"}
    except Exception as e:
        return {"시장 캘린더": f"FAIL - {e}"}


async def run_all(env_file: str):
    env_path = Path(env_file)
    if not env_path.exists():
        print(f"오류: {env_file} 파일이 없습니다.")
        sys.exit(1)

    load_dotenv(env_file, override=True)

    kis_env = os.getenv("KIS_ENV", "paper").lower()
    is_paper = kis_env == "paper"
    env_label = "모의투자" if is_paper else "실전투자"

    print(f"{'='*50}")
    print(f"  Preflight 점검: {env_label} ({env_file})")
    print(f"{'='*50}")
    print(f"  계좌: {mask(os.getenv('KIS_ACCOUNT_NUMBER', ''))}")
    print(f"  KIS_ENV: {kis_env}")
    print()

    # 병렬 실행 (KIS는 순차, 나머지 병렬)
    kis_results = await test_kis(env_label, is_paper)
    other_results = await asyncio.gather(
        test_telegram(),
        test_google_sheets(),
        test_exchange_rate(),
        test_market_calendar(),
    )

    # 결과 출력
    all_results = {}
    all_results.update(kis_results)
    for r in other_results:
        all_results.update(r)

    fail_count = 0
    for name, result in all_results.items():
        status = "FAIL" if "FAIL" in result else ("SKIP" if "SKIP" in result else "PASS")
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[status]
        print(f"  {icon} {name}: {result}")
        if status == "FAIL":
            fail_count += 1

    print()
    if fail_count == 0:
        print(f"  >> 전체 통과! {env_label} 준비 완료")
    else:
        print(f"  >> {fail_count}건 실패. 수정 후 재시도하세요.")
    print()

    return fail_count


if __name__ == "__main__":
    env_file = sys.argv[1] if len(sys.argv) > 1 else ".env"
    asyncio.run(run_all(env_file))
