"""수익률 차트 생성 (matplotlib → PNG bytes)."""

import logging
from io import BytesIO

import matplotlib
matplotlib.use("Agg")  # 디스플레이 없이 렌더링
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

logger = logging.getLogger(__name__)

# 한글 폰트 설정
plt.rcParams["font.family"] = ["AppleGothic", "NanumGothic", "Malgun Gothic", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def render_return_chart(
    dates: list[str],
    returns: list[float],
    symbol: str,
    period: str = "주간",
) -> bytes:
    """수익률 차트를 PNG 바이트로 생성한다.

    Args:
        dates: 날짜 문자열 리스트 (YYYY-MM-DD)
        returns: 수익률(%) 리스트
        symbol: 종목 코드
        period: "주간" 또는 "월간"

    Returns:
        PNG 이미지 바이트
    """
    if not dates or not returns:
        return b""

    try:
        fig, ax = plt.subplots(figsize=(10, 5))

        x_dates = [datetime.strptime(d, "%Y-%m-%d") for d in dates]

        # 수익률 선 그래프
        ax.plot(x_dates, returns, color="#3498db", linewidth=2, marker="o", markersize=4)

        # 0% 기준선
        ax.axhline(y=0, color="#95a5a6", linestyle="--", linewidth=1)

        # 영역 채우기
        ax.fill_between(
            x_dates, returns, 0,
            where=[r >= 0 for r in returns],
            color="#2ecc71", alpha=0.2,
        )
        ax.fill_between(
            x_dates, returns, 0,
            where=[r < 0 for r in returns],
            color="#e74c3c", alpha=0.2,
        )

        ax.set_title(f"{symbol} {period} 수익률 추이", fontsize=14, fontweight="bold")
        ax.set_ylabel("수익률 (%)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.grid(True, alpha=0.3)

        # 최신 수익률 표시
        if returns:
            latest = returns[-1]
            ax.annotate(
                f"{latest:+.2f}%",
                xy=(x_dates[-1], latest),
                fontsize=11,
                fontweight="bold",
                color="#e74c3c" if latest < 0 else "#2ecc71",
            )

        plt.tight_layout()

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)

        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"차트 생성 실패: {e}")
        return b""


def render_cycle_summary_chart(
    cycles: list[dict],
    symbol: str,
) -> bytes:
    """사이클별 수익 요약 차트를 생성한다.

    Args:
        cycles: [{"cycle": 1, "return_pct": 5.2, "days": 15}, ...]
        symbol: 종목 코드

    Returns:
        PNG 이미지 바이트
    """
    if not cycles:
        return b""

    try:
        fig, ax = plt.subplots(figsize=(10, 5))

        cycle_nums = [f"#{c['cycle']}" for c in cycles]
        returns = [c["return_pct"] for c in cycles]
        colors = ["#2ecc71" if r >= 0 else "#e74c3c" for r in returns]

        bars = ax.bar(cycle_nums, returns, color=colors, edgecolor="white", linewidth=0.5)

        # 막대 위에 수치 표시
        for bar, ret in zip(bars, returns):
            y_pos = bar.get_height() if ret >= 0 else bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2, y_pos,
                f"{ret:+.1f}%",
                ha="center", va="bottom" if ret >= 0 else "top",
                fontsize=9, fontweight="bold",
            )

        ax.axhline(y=0, color="#95a5a6", linestyle="-", linewidth=0.5)
        ax.set_title(f"{symbol} 사이클별 수익률", fontsize=14, fontweight="bold")
        ax.set_ylabel("수익률 (%)")
        ax.grid(True, axis="y", alpha=0.3)

        # 평균 수익률 표시
        avg_return = sum(returns) / len(returns)
        ax.axhline(y=avg_return, color="#3498db", linestyle="--", linewidth=1, label=f"평균: {avg_return:+.1f}%")
        ax.legend()

        plt.tight_layout()

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)

        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"사이클 차트 생성 실패: {e}")
        return b""
