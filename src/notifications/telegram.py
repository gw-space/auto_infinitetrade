"""텔레그램 봇 - 알림 발송 + 명령어 핸들러 + 접근 제어."""

import asyncio
import logging
import time
from io import BytesIO

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.strategy.state import AllStates, CycleState

logger = logging.getLogger(__name__)


class TelegramBot:
    """텔레그램 봇: 알림 발송 + 명령어 수신."""

    def __init__(
        self,
        bot_token: str,
        allowed_chat_id: str,
        on_sell_confirm=None,
        on_pause=None,
        on_resume=None,
        on_dryrun_toggle=None,
        get_states=None,
        get_report=None,
        allowed_symbols: set[str] | None = None,
    ):
        self.bot_token = bot_token
        self.allowed_chat_id = str(allowed_chat_id)
        self._allowed_symbols = allowed_symbols or set()
        self.app: Application | None = None

        # 콜백 함수들
        self._on_sell_confirm = on_sell_confirm
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_dryrun_toggle = on_dryrun_toggle
        self._get_states = get_states
        self._get_report = get_report

        # /sell 확인 대기 상태
        self._sell_pending: dict[str, float] = {}  # symbol -> timestamp
        self._sell_timeout = 30  # 초

    def _check_auth(self, update: Update) -> bool:
        """허용된 chat_id인지 확인."""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.allowed_chat_id:
            logger.warning(f"비인가 접근 시도: chat_id={chat_id}")
            return False
        return True

    async def setup(self) -> Application:
        """봇을 초기화하고 명령어 핸들러를 등록한다."""
        self.app = Application.builder().token(self.bot_token).build()

        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("sell", self._cmd_sell))
        self.app.add_handler(CommandHandler("confirm_sell", self._cmd_confirm_sell))
        self.app.add_handler(CommandHandler("pause", self._cmd_pause))
        self.app.add_handler(CommandHandler("resume", self._cmd_resume))
        self.app.add_handler(CommandHandler("dryrun", self._cmd_dryrun))
        self.app.add_handler(CommandHandler("report", self._cmd_report))
        self.app.add_handler(CommandHandler("help", self._cmd_help))

        return self.app

    # === 알림 발송 ===

    async def send_message(self, text: str) -> None:
        """텔레그램 메시지를 발송한다."""
        try:
            if self.app and self.app.bot:
                await self.app.bot.send_message(
                    chat_id=self.allowed_chat_id,
                    text=text,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"텔레그램 메시지 발송 실패: {e}")

    async def send_photo(self, image_bytes: bytes, caption: str = "") -> None:
        """이미지를 텔레그램으로 발송한다."""
        try:
            if self.app and self.app.bot:
                bio = BytesIO(image_bytes)
                bio.name = "chart.png"
                await self.app.bot.send_photo(
                    chat_id=self.allowed_chat_id,
                    photo=bio,
                    caption=caption,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"텔레그램 이미지 발송 실패: {e}")

    async def notify_startup(self) -> None:
        """봇 시작 알림."""
        await self.send_message("🟢 <b>무한매수법 봇 시작됨</b>")

    async def notify_shutdown(self) -> None:
        """봇 종료 알림."""
        await self.send_message("🔴 <b>무한매수법 봇 종료됨</b>")

    async def notify_order_placed(self, state: CycleState, action) -> None:
        """주문 실행 알림."""
        lines = [
            f"📊 <b>[{state.symbol}] 주문 실행</b>",
            "",
            "■ 보유 현황",
            f"  평균단가: ${state.avg_price:.2f}",
            f"  보유수량: {state.total_shares}주",
            f"  현재 수익률: {action.return_pct:+.2f}%",
            "",
            "■ 오늘 주문",
        ]

        if action.is_cold_start:
            lines.append(f"  즉시 매수: {action.cold_start_qty}주 (1회차)")
        else:
            if action.loc_buy_avg_qty > 0:
                lines.append(
                    f"  LOC 매수(평단): ${action.loc_buy_avg_price:.2f} × {action.loc_buy_avg_qty}주"
                )
            if action.loc_buy_high_qty > 0:
                lines.append(
                    f"  LOC 매수(고가): ${action.loc_buy_high_price:.2f} × {action.loc_buy_high_qty}주"
                )
            if action.limit_sell_qty > 0:
                lines.append(
                    f"  지정가 매도: ${action.limit_sell_price:.2f} × {action.limit_sell_qty}주 (전량)"
                )

        lines.extend([
            "",
            f"■ 사이클: {state.splits_used:.1f}/{state.num_splits} 분할 사용",
        ])

        if state.is_dryrun:
            lines.append("\n⚠️ <b>드라이런 모드 (실주문 없음)</b>")

        await self.send_message("\n".join(lines))

    async def notify_fill_result(self, state: CycleState, fills: list[dict]) -> None:
        """체결 결과 알림."""
        lines = [
            f"📋 <b>[{state.symbol}] 체결 결과</b>",
            "",
        ]

        for fill in fills:
            side = "매수" if fill["side"] == "buy" else "매도"
            lines.append(
                f"  {side}: {fill['quantity']}주 @ ${fill['price']:.2f} "
                f"(${fill['amount']:.2f})"
            )

        lines.extend([
            "",
            f"  평균단가: ${state.avg_price:.2f}",
            f"  보유수량: {state.total_shares}주",
            f"  분할: {state.splits_used:.1f}/{state.num_splits}",
        ])

        if state.avg_price > 0 and state.total_shares > 0:
            lines.append(f"  현재 수익률: {((fills[0]['price'] - state.avg_price) / state.avg_price * 100):+.2f}%")

        await self.send_message("\n".join(lines))

    async def notify_cycle_complete(
        self, state: CycleState, sell_amount: float, reason: str
    ) -> None:
        """사이클 종료 알림."""
        pnl = sell_amount - state.total_invested
        pnl_pct = (pnl / state.total_invested * 100) if state.total_invested > 0 else 0

        lines = [
            f"🏁 <b>[{state.symbol}] 사이클 {state.cycle_number} 종료</b>",
            "",
            f"  종료 사유: {reason}",
            f"  투입 총액: ${state.total_invested:.2f}",
            f"  매도 총액: ${sell_amount:.2f}",
            f"  <b>총 수익: ${pnl:+.2f} ({pnl_pct:+.2f}%)</b>",
            f"  사용 분할: {state.splits_used:.1f}/{state.num_splits}",
        ]

        await self.send_message("\n".join(lines))

    async def notify_40_splits_exhausted(
        self, state: CycleState, current_price: float = 0.0
    ) -> None:
        """40회차 소진 + 전략 실행 알림."""
        strategy = state.over40_strategy
        return_pct = 0.0
        if state.avg_price > 0 and current_price > 0:
            return_pct = (current_price - state.avg_price) / state.avg_price * 100

        strategy_names = {
            "quarter": "쿼터매도 (1/4 매도 후 매수 재개)",
            "lower_target": "목표 수익률 5% 하향",
            "hold": "매수 중단, 매도만 유지",
            "full_exit": "전량 매도 후 새 사이클",
        }
        strategy_label = strategy_names.get(strategy, strategy)

        await self.send_message(
            f"⚠️ <b>[{state.symbol}] 40회차 소진, {strategy_label} 실행됨, "
            f"현재 손익률 {return_pct:+.2f}%</b>\n\n"
            f"  평균단가: ${state.avg_price:.2f}\n"
            f"  보유수량: {state.total_shares}주\n"
            f"  전략: <b>{strategy}</b>\n\n"
            f"  수동 전량 매도: /sell {state.symbol} → /confirm_sell {state.symbol}"
        )

    async def notify_over40_strategy_result(
        self, state: CycleState, strategy: str, detail: str, return_pct: float
    ) -> None:
        """40회차 전략 실행 결과 알림."""
        strategy_names = {
            "quarter": "쿼터매도",
            "lower_target": "목표 하향",
            "hold": "홀딩",
            "full_exit": "전량 매도",
        }
        await self.send_message(
            f"📌 <b>[{state.symbol}] 40회차 전략 결과</b>\n\n"
            f"  전략: {strategy_names.get(strategy, strategy)}\n"
            f"  {detail}\n"
            f"  현재 손익률: {return_pct:+.2f}%"
        )

    async def notify_drawdown_warning(
        self, state: CycleState, current_price: float, drawdown_pct: float
    ) -> None:
        """최대 낙폭 경고."""
        await self.send_message(
            f"🚨 <b>[{state.symbol}] 낙폭 경고!</b>\n\n"
            f"  현재가: ${current_price:.2f}\n"
            f"  평균단가: ${state.avg_price:.2f}\n"
            f"  <b>낙폭: {drawdown_pct:+.2f}%</b>"
        )

    async def notify_missed_days(self, missed: int) -> None:
        """놓친 거래일 알림."""
        await self.send_message(
            f"⏭️ <b>{missed}일 거래 누락됨</b>\n이어서 진행합니다."
        )

    async def notify_error(self, error_msg: str) -> None:
        """오류 알림."""
        await self.send_message(f"❌ <b>오류 발생</b>\n\n{error_msg}")

    async def notify_order_failure(self, symbol: str, retries: int, error: str) -> None:
        """주문 실패 긴급 알림."""
        await self.send_message(
            f"🚨 <b>[{symbol}] 주문 실패!</b>\n\n"
            f"  {retries}회 재시도 후 실패\n"
            f"  오류: {error}\n\n"
            f"  수동 확인이 필요합니다."
        )

    # === 명령어 핸들러 ===

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재 보유 현황 조회."""
        if not self._check_auth(update):
            return

        if not self._get_states:
            await update.message.reply_text("상태 조회 불가")
            return

        states: AllStates = self._get_states()
        if not states.tickers:
            await update.message.reply_text("활성 종목 없음")
            return

        for symbol, state in states.tickers.items():
            lines = [
                f"📊 <b>[{symbol}]</b>",
                f"  사이클: #{state.cycle_number}",
                f"  평균단가: ${state.avg_price:.2f}",
                f"  보유수량: {state.total_shares}주",
                f"  분할: {state.splits_used:.1f}/{state.num_splits}",
                f"  누적 실현손익: ${state.realized_pnl:+.2f}",
                f"  상태: {'일시중지' if state.is_paused else '매도대기' if state.pending_sell else '진행중'}",
                f"  드라이런: {'ON' if state.is_dryrun else 'OFF'}",
            ]
            await update.message.reply_html("\n".join(lines))

    async def _cmd_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sell [symbol] - 전량 매도 1단계."""
        if not self._check_auth(update):
            return

        args = context.args
        if not args:
            await update.message.reply_text("사용법: /sell TQQQ")
            return

        symbol = args[0].upper()
        if self._allowed_symbols and symbol not in self._allowed_symbols:
            await update.message.reply_text(f"{symbol}: 등록되지 않은 종목입니다.")
            return
        self._sell_pending[symbol] = time.time()

        await update.message.reply_html(
            f"⚠️ <b>{symbol} 전량 매도 확인</b>\n\n"
            f"30초 내에 /confirm_sell {symbol} 을 입력하세요."
        )

        # 30초 후 자동 취소 (이벤트 루프 차단하지 않음)
        asyncio.get_running_loop().call_later(
            self._sell_timeout,
            lambda s=symbol: asyncio.ensure_future(self._expire_sell(s)),
        )

    async def _expire_sell(self, symbol: str) -> None:
        """매도 확인 타임아웃 처리."""
        if symbol in self._sell_pending:
            del self._sell_pending[symbol]
            await self.send_message(f"⏰ {symbol} 매도 요청 시간 초과 (취소됨)")

    async def _cmd_confirm_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/confirm_sell [symbol] - 전량 매도 2단계 확인."""
        if not self._check_auth(update):
            return

        args = context.args
        if not args:
            await update.message.reply_text("사용법: /confirm_sell TQQQ")
            return

        symbol = args[0].upper()

        if symbol not in self._sell_pending:
            await update.message.reply_text(
                f"{symbol}: 매도 요청이 없거나 시간 초과되었습니다.\n"
                f"먼저 /sell {symbol} 을 입력하세요."
            )
            return

        del self._sell_pending[symbol]

        if self._on_sell_confirm:
            await self._on_sell_confirm(symbol)
            await update.message.reply_html(f"✅ <b>{symbol} 전량 매도 실행</b>")
        else:
            await update.message.reply_text("매도 처리기가 등록되지 않았습니다.")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/pause - 매매 일시 중지."""
        if not self._check_auth(update):
            return

        if self._on_pause:
            await self._on_pause()
        await update.message.reply_html("⏸️ <b>매매 일시 중지됨</b>")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/resume - 매매 재개."""
        if not self._check_auth(update):
            return

        if self._on_resume:
            await self._on_resume()
        await update.message.reply_html("▶️ <b>매매 재개됨</b>")

    async def _cmd_dryrun(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/dryrun on|off - 드라이런 모드 토글."""
        if not self._check_auth(update):
            return

        args = context.args
        if not args or args[0].lower() not in ("on", "off"):
            await update.message.reply_text("사용법: /dryrun on 또는 /dryrun off")
            return

        enable = args[0].lower() == "on"
        if self._on_dryrun_toggle:
            await self._on_dryrun_toggle(enable)

        status = "ON (실주문 없음)" if enable else "OFF (실주문 모드)"
        await update.message.reply_html(f"🔄 <b>드라이런: {status}</b>")

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/report - 누적 수익 리포트."""
        if not self._check_auth(update):
            return

        if self._get_report:
            report = await self._get_report()
            await update.message.reply_html(report)
        else:
            await update.message.reply_text("리포트 조회 불가")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """명령어 도움말."""
        if not self._check_auth(update):
            return

        await update.message.reply_html(
            "<b>📖 명령어 목록</b>\n\n"
            "/status - 보유 현황 조회\n"
            "/sell [종목] - 전량 매도 (1단계)\n"
            "/confirm_sell [종목] - 매도 확인 (2단계)\n"
            "/pause - 매매 일시 중지\n"
            "/resume - 매매 재개\n"
            "/dryrun on|off - 드라이런 모드\n"
            "/report - 누적 수익 리포트\n"
            "/help - 이 도움말"
        )
