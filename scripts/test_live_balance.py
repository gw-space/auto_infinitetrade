"""실전투자 잔고 조회 테스트 (민감정보 출력 안함)."""
import asyncio
import os
from dotenv import load_dotenv


async def main():
    load_dotenv(".env.live", override=True)

    from src.kis.client import KISClient
    from src.kis.auth import ensure_token, TOKEN_CACHE_PATH
    from src.kis.account import get_holdings, get_available_cash

    kis_env = os.getenv("KIS_ENV", "paper").lower()
    is_paper = kis_env == "paper"
    base_url = (
        "https://openapivts.koreainvestment.com:29443"
        if is_paper
        else "https://openapi.koreainvestment.com:9443"
    )

    print(f"환경: {'모의' if is_paper else '실전'}")

    # 캐시 토큰 삭제 (만료 대비)
    if TOKEN_CACHE_PATH.exists():
        TOKEN_CACHE_PATH.unlink()
        print("캐시 토큰 삭제")

    client = KISClient(
        base_url=base_url,
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_number=os.getenv("KIS_ACCOUNT_NUMBER", ""),
        is_paper=is_paper,
    )

    try:
        await ensure_token(client)
        print("토큰 발급 성공\n")

        # 잔고 조회
        print("--- 잔고 조회 ---")
        holdings = await get_holdings(client)
        if holdings:
            for h in holdings:
                print(
                    f"  {h.symbol}: {h.quantity}주 | "
                    f"평단 ${h.avg_price:.2f} | 현재가 ${h.current_price:.2f} | "
                    f"평가금액 ${h.eval_amount:.2f} | 수익률 {h.pnl_pct:.2f}%"
                )
        else:
            print("  보유 종목 없음")

        # 주문 가능 금액
        print("\n--- 주문 가능 금액 ---")
        cash = await get_available_cash(client)
        print(f"  USD: ${cash:.2f}")

        print("\n잔고 조회 성공!")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
