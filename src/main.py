"""메인 오케스트레이터 - 봇 + 스케줄러 + 전략 실행."""

import asyncio
import logging
import logging.handlers
import signal
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.kis.auth import ensure_token, set_token_path
from src.kis.client import KISClient
from src.kis.market import get_current_price
from src.kis.account import get_holdings, get_executions, get_available_cash
from src.kis.order import (
    place_loc_buy,
    place_limit_sell,
    place_immediate_buy,
    place_immediate_sell,
)
from src.strategy.infinite_buy import (
    calculate_daily_action,
    update_state_after_fill,
    apply_quarter_sell_result,
)
from src.strategy.state import (
    AllStates,
    load_states,
    save_states,
    get_or_create_state,
    reset_cycle,
    set_state_path,
)
from src.notifications.telegram import TelegramBot
from src.logging_sheet.sheets import SheetsLogger
from src.charts.renderer import render_return_chart, render_cycle_summary_chart
from src.utils.config_loader import AppConfig, load_config
from src.utils.market_calendar import is_trading_day, count_missed_days
from src.utils.exchange_rate import get_usd_krw_rate

logger = logging.getLogger(__name__)


class TradingBot:
    """무한매수법 자동매매 봇."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.states = load_states()

        # KIS 클라이언트
        self.kis = KISClient(
            base_url=config.kis.base_url,
            app_key=config.kis.app_key,
            app_secret=config.kis.app_secret,
            account_number=config.kis.account_number,
            is_paper=config.kis.is_paper,
        )

        # 구글 시트
        self.sheets = SheetsLogger(
            spreadsheet_id=config.google_sheets.spreadsheet_id,
            credentials_path=config.google_sheets.credentials_path,
        )

        # 텔레그램 봇
        self.telegram = TelegramBot(
            bot_token=config.telegram.bot_token,
            allowed_chat_id=config.telegram.chat_id,
            on_sell_confirm=self._handle_force_sell,
            on_pause=self._handle_pause,
            on_resume=self._handle_resume,
            on_dryrun_toggle=self._handle_dryrun_toggle,
            get_states=lambda: self.states,
            get_report=self._generate_report,
            allowed_symbols={t.symbol for t in config.tickers},
        )

        # 스케줄러
        self.scheduler = AsyncIOScheduler(timezone="US/Eastern")

    async def start(self) -> None:
        """봇을 시작한다."""
        logger.info("무한매수법 봇 시작")
        env_label = "모의투자" if self.config.kis.is_paper else "실전투자"
        logger.info(f"환경: {env_label}")

        # 토큰 발급
        await ensure_token(self.kis)

        # 시작 시 reconciliation
        await self._reconcile_states()

        # 시작 시 종목별 매수 가능 여부 체크
        await self._check_capital_adequacy()

        # 텔레그램 봇 설정
        app = await self.telegram.setup()

        # 스케줄 등록
        self._setup_schedules()

        # 시작 알림
        await self.telegram.notify_startup()

        # 놓친 거래일 확인
        await self._check_missed_days()

        # 봇 + 스케줄러 실행
        self.scheduler.start()

        # 시그널 핸들러
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # 텔레그램 봇 폴링 시작
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        logger.info("봇 실행 중 (Ctrl+C로 종료)")

        # 무한 대기
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """봇을 종료한다."""
        logger.info("봇 종료 중...")
        await self.telegram.notify_shutdown()

        self.scheduler.shutdown(wait=False)

        if self.telegram.app:
            await self.telegram.app.updater.stop()
            await self.telegram.app.stop()
            await self.telegram.app.shutdown()

        await self.kis.close()
        save_states(self.states)

        logger.info("봇 종료 완료")
        sys.exit(0)

    def _setup_schedules(self) -> None:
        """스케줄을 등록한다."""
        order_h, order_m = self.config.schedule.order_time.split(":")
        check_h, check_m = self.config.schedule.check_time.split(":")
        report_h, report_m = self.config.schedule.report_time.split(":")

        if self.config.kis.is_paper:
            # 모의투자: LOC 시뮬레이션
            # 09:35  LOC 주문 의도 생성 (실제 주문 X)
            # 15:30  현재가로 체결 판정 → 체결분만 지정가 주문
            self.scheduler.add_job(
                self._paper_plan_job,
                CronTrigger(hour=int(order_h), minute=int(order_m), day_of_week="mon-fri"),
                id="paper_plan",
                name="LOC 의도 생성 (모의)",
            )
            self.scheduler.add_job(
                self._paper_execute_job,
                CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
                id="paper_execute",
                name="LOC 체결 판정 (모의)",
            )
            logger.info(f"모의투자 모드: 의도생성 {order_h}:{order_m} / 체결판정 15:30 (ET)")
        else:
            # 실전: LOC 주문 직접 실행
            self.scheduler.add_job(
                self._daily_order_job,
                CronTrigger(hour=int(order_h), minute=int(order_m), day_of_week="mon-fri"),
                id="daily_order",
                name="일일 주문",
            )

        # 마감 후 체결 확인
        self.scheduler.add_job(
            self._daily_check_job,
            CronTrigger(hour=int(check_h), minute=int(check_m), day_of_week="mon-fri"),
            id="daily_check",
            name="체결 확인",
        )

        # 일일 리포트
        self.scheduler.add_job(
            self._daily_report_job,
            CronTrigger(hour=int(report_h), minute=int(report_m), day_of_week="mon-fri"),
            id="daily_report",
            name="일일 리포트",
        )

        # 월간 백업
        self.scheduler.add_job(
            self._monthly_backup_job,
            CronTrigger(day=self.config.backup.monthly_day, hour=0, minute=30),
            id="monthly_backup",
            name="월간 백업",
        )

        # 주간 차트 발송 (매주 금요일 장 마감 후)
        self.scheduler.add_job(
            self._weekly_chart_job,
            CronTrigger(day_of_week="fri", hour=17, minute=0),
            id="weekly_chart",
            name="주간 차트",
        )

        logger.info("스케줄 등록 완료")

    # === 모의투자 LOC 시뮬레이션 ===

    async def _paper_plan_job(self) -> None:
        """모의투자: LOC 주문 의도를 생성한다 (실제 주문 X)."""
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")

        if not is_trading_day():
            logger.info(f"{today}: 휴장일, 스킵")
            return

        try:
            await ensure_token(self.kis)

            for ticker_config in self.config.tickers:
                symbol = ticker_config.symbol
                exchange = ticker_config.exchange

                try:
                    cash = await get_available_cash(self.kis, symbol)
                    initial_capital = min(cash, ticker_config.total_capital) if cash > 0 else ticker_config.total_capital
                except Exception:
                    initial_capital = ticker_config.total_capital

                state = get_or_create_state(
                    self.states, symbol, initial_capital,
                    ticker_config.num_splits, ticker_config.profit_target_pct, today,
                )
                state.over40_strategy = self.config.over40_strategy

                if state.last_order_date == today:
                    logger.info(f"{symbol}: 오늘 이미 주문 의도 생성됨, 스킵")
                    continue

                current_price = await get_current_price(self.kis, symbol, exchange)
                holdings = await get_holdings(self.kis, symbol)
                existing_shares = holdings[0].quantity if holdings else 0

                action = calculate_daily_action(state, current_price, existing_shares)

                # 1회차: 즉시 지정가 매수 (LOC 불필요)
                if action.is_cold_start:
                    state.last_order_date = today
                    save_states(self.states)

                    if not state.is_dryrun:
                        result = await place_immediate_buy(
                            self.kis, symbol, exchange, action.cold_start_qty, current_price
                        )
                        if not result.success:
                            await self.telegram.notify_order_failure(
                                symbol, self.config.alerts.order_retry_count, result.message
                            )
                    await self.telegram.notify_order_placed(state, action)
                    continue

                # 40회차 전략은 즉시 실행
                if action.over40_action:
                    await self._execute_over40_strategy(state, action, exchange, current_price, today)
                    continue

                if action.should_skip:
                    logger.info(f"{symbol}: {action.skip_reason}")
                    continue

                # 지정가 매도는 즉시 주문 (LOC 아님)
                if action.limit_sell_qty > 0 and not state.is_dryrun:
                    await place_limit_sell(
                        self.kis, symbol, exchange,
                        action.limit_sell_qty, action.limit_sell_price,
                    )

                # LOC 매수 의도만 저장 (15:30에 체결 판정)
                plan = {
                    "avg_qty": action.loc_buy_avg_qty,
                    "avg_price": action.loc_buy_avg_price,
                    "high_qty": action.loc_buy_high_qty,
                    "high_price": action.loc_buy_high_price,
                }
                state.paper_loc_plan = plan
                state.last_order_date = today
                save_states(self.states)

                await self.telegram.send_message(
                    f"📋 <b>[{symbol}] 주문 (모의)</b>\n\n"
                    f"  LOC 평단: ${plan['avg_price']:.2f} x {plan['avg_qty']}주 (장종료 30분전 판정)\n"
                    f"  LOC 고가: ${plan['high_price']:.2f} x {plan['high_qty']}주 (장종료 30분전 판정)\n"
                    f"  지정가 매도: ${action.limit_sell_price:.2f} x {action.limit_sell_qty}주 (즉시 주문)"
                )
                logger.info(f"{symbol}: 매도 주문 완료 + LOC 의도 저장")

        except Exception as e:
            logger.error(f"LOC 의도 생성 오류: {e}", exc_info=True)
            await self.telegram.notify_error("LOC 의도 생성 오류 발생. 로그를 확인하세요.")

    async def _paper_execute_job(self) -> None:
        """모의투자: 15:30 현재가로 LOC 체결 판정 → 체결분만 지정가 주문."""
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")

        if not is_trading_day():
            return

        try:
            await ensure_token(self.kis)

            for ticker_config in self.config.tickers:
                symbol = ticker_config.symbol
                exchange = ticker_config.exchange
                state = self.states.tickers.get(symbol)

                if not state or not state.paper_loc_plan:
                    continue

                plan = state.paper_loc_plan
                if not plan.get("avg_qty") and not plan.get("high_qty") and not plan.get("sell_qty"):
                    continue

                # 현재가 = 가상 종가
                closing_price = await get_current_price(self.kis, symbol, exchange)

                filled = []

                # LOC 평단 체결 판정: 종가 <= 평단가
                if plan.get("avg_qty", 0) > 0 and closing_price <= plan["avg_price"]:
                    if not state.is_dryrun:
                        result = await place_loc_buy(
                            self.kis, symbol, exchange, plan["avg_qty"], plan["avg_price"]
                        )
                        if result.success:
                            filled.append(f"LOC 평단: {plan['avg_qty']}주 @ ${closing_price:.2f}")

                # LOC 고가 체결 판정: 종가 <= 고가
                if plan.get("high_qty", 0) > 0 and closing_price <= plan["high_price"]:
                    if not state.is_dryrun:
                        result = await place_loc_buy(
                            self.kis, symbol, exchange, plan["high_qty"], plan["high_price"]
                        )
                        if result.success:
                            filled.append(f"LOC 고가: {plan['high_qty']}주 @ ${closing_price:.2f}")

                # 결과 알림 (매도는 09:35에 이미 주문됨)
                if filled:
                    msg = f"📊 <b>[{symbol}] LOC 체결 (모의)</b>\n\n  가상 종가: ${closing_price:.2f}\n\n"
                    msg += "\n".join(f"  {f}" for f in filled)
                else:
                    msg = (
                        f"📊 <b>[{symbol}] LOC 미체결 (모의)</b>\n\n"
                        f"  가상 종가: ${closing_price:.2f}\n"
                        f"  평단: ${plan.get('avg_price', 0):.2f} / 고가: ${plan.get('high_price', 0):.2f}"
                    )
                await self.telegram.send_message(msg)

                # 의도 초기화
                state.paper_loc_plan = {}
                save_states(self.states)

        except Exception as e:
            logger.error(f"LOC 체결 판정 오류: {e}", exc_info=True)
            await self.telegram.notify_error("LOC 체결 판정 오류 발생. 로그를 확인하세요.")

    # === 일일 주문 (실전) ===

    async def _daily_order_job(self) -> None:
        """매일 주문을 실행한다."""
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")

        if not is_trading_day():
            logger.info(f"{today}: 휴장일, 스킵")
            return

        try:
            await ensure_token(self.kis)

            for ticker_config in self.config.tickers:
                await self._execute_ticker_order(ticker_config, today)

            save_states(self.states)

        except Exception as e:
            logger.error(f"일일 주문 오류: {e}", exc_info=True)
            await self.telegram.notify_error("일일 주문 오류 발생. 로그를 확인하세요.")

    async def _execute_ticker_order(self, ticker_config, today: str) -> None:
        """한 종목의 주문을 실행한다."""
        symbol = ticker_config.symbol
        exchange = ticker_config.exchange

        # 첫 사이클: 잔고와 설정 상한 중 작은 값 사용
        try:
            cash = await get_available_cash(self.kis, symbol)
            initial_capital = min(cash, ticker_config.total_capital) if cash > 0 else ticker_config.total_capital
        except Exception:
            initial_capital = ticker_config.total_capital

        state = get_or_create_state(
            self.states, symbol,
            initial_capital,
            ticker_config.num_splits,
            ticker_config.profit_target_pct,
            today,
        )

        # over40_strategy를 설정에서 동기화
        state.over40_strategy = self.config.over40_strategy

        # 중복 주문 방지
        if state.last_order_date == today:
            logger.info(f"{symbol}: 오늘 이미 주문 완료, 스킵")
            return

        # 현재가 조회
        current_price = await get_current_price(self.kis, symbol, exchange)

        # 실제 보유 수량 조회 (reconciliation)
        holdings = await get_holdings(self.kis, symbol)
        existing_shares = holdings[0].quantity if holdings else 0

        # 낙폭 체크
        if state.avg_price > 0 and existing_shares > 0:
            drawdown = (current_price - state.avg_price) / state.avg_price

            # 자동 일시중지 (설정값 이상 하락 시)
            if drawdown <= -self.config.alerts.auto_pause_drawdown_pct:
                state.is_paused = True
                save_states(self.states)
                await self.telegram.send_message(
                    f"🚨 <b>[{symbol}] 자동 일시중지!</b>\n\n"
                    f"  낙폭: {drawdown * 100:+.2f}% (한도: -{self.config.alerts.auto_pause_drawdown_pct * 100:.0f}%)\n"
                    f"  현재가: ${current_price:.2f} / 평단: ${state.avg_price:.2f}\n\n"
                    f"  /resume 으로 재개하세요."
                )
                logger.warning(f"{symbol}: 자동 일시중지 (낙폭 {drawdown*100:.1f}%)")
                return

            # 경고만 (auto_pause 미만)
            if drawdown <= -self.config.alerts.max_drawdown_pct:
                await self.telegram.notify_drawdown_warning(
                    state, current_price, drawdown * 100
                )

        # 안전장치: 일일 주문 횟수 제한
        if state.daily_order_date != today:
            state.daily_order_count = 0
            state.daily_order_date = today
        if state.daily_order_count >= self.config.alerts.max_daily_orders:
            logger.warning(f"{symbol}: 일일 최대 주문 횟수({self.config.alerts.max_daily_orders}) 초과, 중단")
            await self.telegram.send_message(
                f"🚨 <b>[{symbol}] 일일 주문 횟수 초과!</b>\n"
                f"  {state.daily_order_count}회 주문됨 (한도: {self.config.alerts.max_daily_orders}회)\n"
                f"  오늘 추가 주문이 중단됩니다."
            )
            return

        # 전략 판단
        action = calculate_daily_action(state, current_price, existing_shares)

        # 안전장치: 최대 주문 수량 제한
        max_qty = self.config.alerts.max_order_qty
        if action.cold_start_qty > max_qty:
            action.cold_start_qty = max_qty
            logger.warning(f"{symbol}: 1회차 수량 {action.cold_start_qty}→{max_qty} 제한")
        if action.loc_buy_avg_qty > max_qty:
            action.loc_buy_avg_qty = max_qty
            logger.warning(f"{symbol}: LOC 평단 수량 →{max_qty} 제한")
        if action.loc_buy_high_qty > max_qty:
            action.loc_buy_high_qty = max_qty
            logger.warning(f"{symbol}: LOC 고가 수량 →{max_qty} 제한")

        if action.should_skip:
            logger.info(f"{symbol}: {action.skip_reason}")
            return

        # 40회차 전략 실행
        if action.over40_action:
            await self._execute_over40_strategy(state, action, exchange, current_price, today)
            return

        # 중복 주문 방지: 주문 전에 날짜 기록
        state.last_order_date = today
        save_states(self.states)

        # 드라이런 모드
        if state.is_dryrun:
            logger.info(f"{symbol}: 드라이런 - 주문 스킵")
            await self.telegram.notify_order_placed(state, action)
            return

        # 주문 실행
        if action.is_cold_start:
            # 1회차: 즉시 지정가 매수
            result = await place_immediate_buy(
                self.kis, symbol, exchange, action.cold_start_qty, current_price
            )
            if not result.success:
                await self.telegram.notify_order_failure(
                    symbol, self.config.alerts.order_retry_count, result.message
                )
                return
        else:
            # LOC 매수(평단)
            if action.loc_buy_avg_qty > 0:
                result = await place_loc_buy(
                    self.kis, symbol, exchange,
                    action.loc_buy_avg_qty, action.loc_buy_avg_price,
                )
                if not result.success:
                    await self.telegram.notify_order_failure(
                        symbol, self.config.alerts.order_retry_count, result.message
                    )

            # LOC 매수(고가)
            if action.loc_buy_high_qty > 0:
                result = await place_loc_buy(
                    self.kis, symbol, exchange,
                    action.loc_buy_high_qty, action.loc_buy_high_price,
                )
                if not result.success:
                    await self.telegram.notify_order_failure(
                        symbol, self.config.alerts.order_retry_count, result.message
                    )

            # 지정가 매도 (전량, Day Order)
            if action.limit_sell_qty > 0:
                result = await place_limit_sell(
                    self.kis, symbol, exchange,
                    action.limit_sell_qty, action.limit_sell_price,
                )
                if not result.success:
                    await self.telegram.notify_order_failure(
                        symbol, self.config.alerts.order_retry_count, result.message
                    )

        # 주문 횟수 카운트
        state.daily_order_count += 1

        # 알림 발송
        await self.telegram.notify_order_placed(state, action)

    async def _execute_over40_strategy(
        self, state, action, exchange: str, current_price: float, today: str
    ) -> None:
        """40회차 소진 전략을 실행한다."""
        symbol = state.symbol
        strategy = state.over40_strategy
        return_pct = action.return_pct

        # 알림: 40회차 소진 + 전략명 + 손익률
        await self.telegram.notify_40_splits_exhausted(state, current_price)

        if state.is_dryrun:
            logger.info(f"{symbol}: 드라이런 - 40회차 전략 '{strategy}' 스킵")
            state.last_order_date = today
            save_states(self.states)
            return

        if strategy == "quarter" and not state.over40_executed:
            # 1/4 즉시 지정가 매도
            qty = action.quarter_sell_qty
            result = await place_immediate_sell(self.kis, symbol, exchange, qty, current_price)
            if result.success:
                # 이중 처리 방지: order_id 등록
                if result.order_id:
                    state.processed_order_ids.append(result.order_id)
                # quarter는 즉시 상태 반영이 필요
                estimated_amount = current_price * qty
                apply_quarter_sell_result(state, qty, estimated_amount)
                await self.telegram.notify_over40_strategy_result(
                    state, "quarter",
                    f"{qty}주(1/4) 매도, splits {state.splits_used:.1f}/{state.num_splits}로 복원",
                    return_pct,
                )
            else:
                await self.telegram.notify_order_failure(symbol, 3, result.message)
                # quarter 실패 시 매도 주문도 안 넣음
                state.last_order_date = today
                save_states(self.states)
                return

            # quarter 성공 시 지정가 매도도 걸어둠
            if action.limit_sell_qty > 0:
                await place_limit_sell(
                    self.kis, symbol, exchange,
                    state.total_shares, action.limit_sell_price,
                )

        elif strategy == "lower_target":
            # profit_target_pct는 이미 _handle_over40에서 5%로 변경됨
            # 새 목표가로 지정가 매도
            if action.limit_sell_qty > 0:
                await place_limit_sell(
                    self.kis, symbol, exchange,
                    action.limit_sell_qty, action.limit_sell_price,
                )
            await self.telegram.notify_over40_strategy_result(
                state, "lower_target",
                f"목표 수익률 5%로 하향, 매도가 ${action.limit_sell_price:.2f}",
                return_pct,
            )

        elif strategy == "hold":
            # 지정가 매도만 유지
            if action.limit_sell_qty > 0:
                await place_limit_sell(
                    self.kis, symbol, exchange,
                    action.limit_sell_qty, action.limit_sell_price,
                )
            await self.telegram.notify_over40_strategy_result(
                state, "hold",
                f"매수 중단, ${action.limit_sell_price:.2f} 지정가 매도 유지",
                return_pct,
            )

        elif strategy == "full_exit" and not state.over40_executed:
            # 전량 즉시 지정가 매도
            result = await place_immediate_sell(
                self.kis, symbol, exchange, action.full_exit_qty, current_price
            )
            if result.success:
                state.over40_executed = True
                if result.order_id:
                    state.processed_order_ids.append(result.order_id)
                await self.telegram.notify_over40_strategy_result(
                    state, "full_exit",
                    f"{action.full_exit_qty}주 전량 매도 실행",
                    return_pct,
                )
            else:
                await self.telegram.notify_order_failure(symbol, 3, result.message)

        state.last_order_date = today
        save_states(self.states)

    # === 체결 확인 ===

    async def _daily_check_job(self) -> None:
        """장 마감 후 체결 결과를 확인하고 상태를 업데이트한다."""
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")

        if not is_trading_day():
            return

        try:
            await ensure_token(self.kis)
            usd_krw = await get_usd_krw_rate()

            for ticker_config in self.config.tickers:
                try:
                    await self._check_ticker_fills(ticker_config, today, usd_krw)
                except Exception as e:
                    logger.error(f"{ticker_config.symbol} 체결 확인 오류: {e}", exc_info=True)
                    await self.telegram.notify_error(f"{ticker_config.symbol} 체결 확인 오류 발생. 로그를 확인하세요.")

            save_states(self.states)

        except Exception as e:
            logger.error(f"체결 확인 오류: {e}", exc_info=True)
            await self.telegram.notify_error("체결 확인 오류 발생. 로그를 확인하세요.")

    async def _check_ticker_fills(self, ticker_config, today: str, usd_krw: float) -> None:
        """한 종목의 체결 결과를 확인한다."""
        symbol = ticker_config.symbol
        state = self.states.tickers.get(symbol)
        if not state:
            return

        executions = await get_executions(self.kis, symbol)
        if not executions:
            return

        fills = []
        sell_total = 0.0
        is_full_sell = False
        processed_ids = set(state.processed_order_ids or [])

        for ex in executions:
            if ex.quantity <= 0:
                continue
            if ex.order_id and ex.order_id in processed_ids:
                continue

            update_state_after_fill(
                state, ex.quantity, ex.price, ex.amount, ex.side
            )
            if ex.order_id:
                processed_ids.add(ex.order_id)

            fills.append({
                "side": ex.side,
                "quantity": ex.quantity,
                "price": ex.price,
                "amount": ex.amount,
            })

            if ex.side == "sell":
                sell_total += ex.amount
                if state.total_shares <= 0:
                    is_full_sell = True

        state.processed_order_ids = list(processed_ids)

        # 체결 알림
        if fills:
            await self.telegram.notify_fill_result(state, fills)

        # 현재가 조회
        current_price = await get_current_price(self.kis, symbol, ticker_config.exchange)

        # 구글 시트 기록
        action_str = "/".join(set(f["side"] for f in fills)) if fills else "hold"
        fill_qty = sum(f["quantity"] for f in fills)
        fill_amount = sum(f["amount"] for f in fills)
        return_pct = 0.0
        if state.avg_price > 0 and state.total_shares > 0:
            return_pct = (current_price - state.avg_price) / state.avg_price * 100

        target_price = state.avg_price * (1 + state.profit_target_pct) if state.avg_price > 0 else 0

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self.sheets.log_daily(
                cycle_number=state.cycle_number,
                today=today,
                symbol=symbol,
                current_price=current_price,
                avg_price=state.avg_price,
                quantity=state.total_shares,
                loc_avg_price=state.avg_price,
                loc_high_price=target_price,
                action=action_str,
                fill_qty=fill_qty,
                fill_amount=fill_amount,
                splits_used=state.splits_used,
                num_splits=state.num_splits,
                return_pct=return_pct,
                usd_krw_rate=usd_krw,
                eval_amount=current_price * state.total_shares,
                realized_pnl=state.realized_pnl,
                notes="",
            ))
        except Exception as e:
            logger.error(f"구글 시트 기록 실패: {e}")

        # 전량 매도 → 사이클 종료
        if is_full_sell:
            reason = "익절" if not state.pending_sell else "40회차 소진 매도"
            await self.telegram.notify_cycle_complete(state, sell_total, reason)

            try:
                total_invested = state.total_invested or (sell_total - state.realized_pnl)
                profit = sell_total - total_invested
                return_pct_cycle = (profit / total_invested * 100) if total_invested > 0 else 0

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: self.sheets.log_cycle_summary(
                    cycle_number=state.cycle_number,
                    start_date=state.cycle_start_date,
                    end_date=today,
                    symbol=symbol,
                    total_invested=total_invested,
                    total_sold=sell_total,
                    profit_usd=profit,
                    usd_krw_rate=usd_krw,
                    return_pct=return_pct_cycle,
                    splits_used=state.splits_used,
                    num_splits=state.num_splits,
                    end_reason=reason,
                ))
            except Exception as e:
                logger.error(f"사이클 요약 기록 실패: {e}")

            # 잔여 주식 확인 (동시 체결 엣지 케이스)
            holdings = await get_holdings(self.kis, symbol)
            residual = holdings[0].quantity if holdings else 0

            # 새 사이클: 잔고 기반 자본금 (상한선 적용)
            available_cash = await get_available_cash(self.kis, symbol)
            capital_limit = next(
                (t.total_capital for t in self.config.tickers if t.symbol == symbol), 0
            )
            reset_cycle(state, today, available_cash, capital_limit)

            # 잔여분이 있으면 새 사이클에 편입
            if residual > 0:
                state.total_shares = residual
                state.total_invested = current_price * residual
                state.avg_price = current_price
                state.splits_used = state.total_invested / state.split_amount
                logger.info(f"{symbol}: 잔여 {residual}주 새 사이클에 편입")

        # 40회차 소진 체크
        if (state.num_splits - state.splits_used) < 1.0 and not state.pending_sell:
            state.pending_sell = True
            await self.telegram.notify_40_splits_exhausted(state, current_price)

        save_states(self.states)

    # === 리포트 ===

    async def _daily_report_job(self) -> None:
        """일일 리포트를 발송한다."""
        if not is_trading_day():
            return

        for symbol, state in self.states.tickers.items():
            try:
                current_price = await get_current_price(
                    self.kis, symbol,
                    next((t.exchange for t in self.config.tickers if t.symbol == symbol), "NASD"),
                )
                return_pct = 0.0
                if state.avg_price > 0 and state.total_shares > 0:
                    return_pct = (current_price - state.avg_price) / state.avg_price * 100

                target_price = state.avg_price * (1 + state.profit_target_pct)

                report = (
                    f"📊 <b>[{symbol}] 일일 리포트</b>\n\n"
                    f"■ 보유 현황\n"
                    f"  평균단가: ${state.avg_price:.2f}\n"
                    f"  보유수량: {state.total_shares}주\n"
                    f"  현재가: ${current_price:.2f}\n"
                    f"  수익률: {return_pct:+.2f}%\n\n"
                    f"■ LOC 정보\n"
                    f"  LOC 평단: ${state.avg_price:.2f}\n"
                    f"  LOC 고가: ${target_price:.2f}\n\n"
                    f"■ 사이클 #{state.cycle_number}\n"
                    f"  분할: {state.splits_used:.1f}/{state.num_splits}\n"
                    f"  누적 실현손익: ${state.realized_pnl:+.2f}"
                )

                await self.telegram.send_message(report)
            except Exception as e:
                logger.error(f"{symbol} 리포트 오류: {e}")

    async def _weekly_chart_job(self) -> None:
        """주간 수익률 차트를 생성하여 발송한다."""
        # 구글 시트에서 최근 데이터를 읽어와야 하지만,
        # state에서 간단히 생성 가능한 차트만 발송
        for symbol, state in self.states.tickers.items():
            try:
                # TODO: 구글 시트에서 최근 7일 데이터 읽기
                # 현재는 상태 정보로 간단한 요약만 발송
                summary = (
                    f"📈 <b>[{symbol}] 주간 요약</b>\n\n"
                    f"  사이클: #{state.cycle_number}\n"
                    f"  분할: {state.splits_used:.1f}/{state.num_splits}\n"
                    f"  누적 실현손익: ${state.realized_pnl:+.2f}"
                )
                await self.telegram.send_message(summary)
            except Exception as e:
                logger.error(f"{symbol} 주간 차트 오류: {e}")

    async def _monthly_backup_job(self) -> None:
        """월간 백업을 실행한다."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.sheets.create_monthly_backup)
            await self.telegram.send_message("💾 월간 백업 완료")
        except Exception as e:
            logger.error(f"월간 백업 오류: {e}")
            await self.telegram.notify_error("월간 백업 오류 발생. 로그를 확인하세요.")

    # === 콜백 핸들러 ===

    async def _handle_force_sell(self, symbol: str) -> None:
        """전량 강제 매도."""
        state = self.states.tickers.get(symbol)
        if not state:
            await self.telegram.send_message(f"❌ {symbol}: 활성 상태 없음")
            return

        holdings = await get_holdings(self.kis, symbol)
        if not holdings or holdings[0].quantity <= 0:
            await self.telegram.send_message(f"❌ {symbol}: 보유 수량 없음")
            return

        exchange = next(
            (t.exchange for t in self.config.tickers if t.symbol == symbol), "NASD"
        )

        # 강제 매도 시 현재가 조회 (모의투자 지정가 대체용)
        try:
            cur_price = await get_current_price(self.kis, symbol, exchange)
        except Exception:
            cur_price = holdings[0].current_price if holdings else 0.0

        result = await place_immediate_sell(
            self.kis, symbol, exchange, holdings[0].quantity, cur_price
        )

        if result.success:
            await self.telegram.send_message(
                f"✅ {symbol}: {holdings[0].quantity}주 즉시 매도 주문 완료"
            )
        else:
            await self.telegram.notify_error(f"{symbol} 강제 매도 실패: {result.message}")

    async def _handle_pause(self) -> None:
        """전체 매매 일시 중지."""
        for state in self.states.tickers.values():
            state.is_paused = True
        save_states(self.states)

    async def _handle_resume(self) -> None:
        """전체 매매 재개."""
        for state in self.states.tickers.values():
            state.is_paused = False
        save_states(self.states)

    async def _handle_dryrun_toggle(self, enable: bool) -> None:
        """드라이런 모드 토글."""
        for state in self.states.tickers.values():
            state.is_dryrun = enable
        save_states(self.states)

    async def _generate_report(self) -> str:
        """누적 수익 리포트를 생성한다."""
        lines = ["📊 <b>누적 수익 리포트</b>\n"]

        for symbol, state in self.states.tickers.items():
            win_count = state.cycle_number - 1  # 완료된 사이클 수
            lines.extend([
                f"\n<b>[{symbol}]</b>",
                f"  현재 사이클: #{state.cycle_number}",
                f"  완료 사이클: {win_count}",
                f"  누적 실현손익: ${state.realized_pnl:+.2f}",
                f"  현재 투자금: ${state.total_capital:.2f}",
            ])

        return "\n".join(lines)

    # === 유틸 ===

    async def _reconcile_states(self) -> None:
        """시작 시 KIS 실제 잔고와 state.json을 동기화한다."""
        logger.info("상태 동기화(reconciliation) 시작")

        for ticker_config in self.config.tickers:
            symbol = ticker_config.symbol
            state = self.states.tickers.get(symbol)
            if not state:
                continue

            try:
                holdings = await get_holdings(self.kis, symbol)
                actual_qty = holdings[0].quantity if holdings else 0
                actual_avg = holdings[0].avg_price if holdings else 0.0

                if actual_qty != state.total_shares:
                    logger.warning(
                        f"{symbol}: 수량 불일치 (state={state.total_shares}, "
                        f"KIS={actual_qty}) → KIS 기준으로 보정"
                    )
                    state.total_shares = actual_qty
                    if actual_avg > 0:
                        state.avg_price = actual_avg
                        state.total_invested = actual_avg * actual_qty
                        if state.split_amount > 0:
                            state.splits_used = state.total_invested / state.split_amount
            except Exception as e:
                logger.error(f"{symbol} reconciliation 실패: {e}")

        save_states(self.states)
        logger.info("상태 동기화 완료")

    async def _check_capital_adequacy(self) -> None:
        """시작 시 종목별 현재가 대비 매수 가능 여부를 체크한다."""
        for ticker_config in self.config.tickers:
            symbol = ticker_config.symbol
            exchange = ticker_config.exchange
            split_amount = ticker_config.total_capital / ticker_config.num_splits
            half_split = split_amount * 0.5

            try:
                price = await get_current_price(self.kis, symbol, exchange)
                one_round_qty = int(split_amount // price)
                half_qty = int(half_split // price)

                if one_round_qty == 0:
                    msg = (
                        f"[{symbol}] 1회차 금액(${split_amount:.2f})으로 "
                        f"현재가(${price:.2f}) 1주도 매수 불가! "
                        f"total_capital을 늘리거나 num_splits를 줄이세요."
                    )
                    logger.warning(msg)
                    await self.telegram.send_message(f"⚠️ {msg}")
                elif half_qty == 0:
                    msg = (
                        f"[{symbol}] 0.5회차 금액(${half_split:.2f})으로 "
                        f"현재가(${price:.2f}) 1주도 매수 불가. "
                        f"LOC 주문 시 0주가 됩니다."
                    )
                    logger.warning(msg)
                    await self.telegram.send_message(f"⚠️ {msg}")
            except Exception as e:
                logger.warning(f"{symbol} 자본금 체크 실패: {e}")

    async def _check_missed_days(self) -> None:
        """놓친 거래일을 확인하고 알린다."""
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")

        for symbol, state in self.states.tickers.items():
            if state.last_order_date:
                missed = count_missed_days(state.last_order_date, today)
                if missed > 0:
                    await self.telegram.notify_missed_days(missed)
                    logger.info(f"{symbol}: {missed}일 거래 누락, 이어서 진행")


def setup_logging() -> None:
    """로깅을 설정한다."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                "logs/bot.log",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )


def main():
    """엔트리포인트.

    사용법:
      python -m src.main                    # .env 사용 (기본)
      python -m src.main .env.paper         # 모의투자
      python -m src.main .env.live          # 실전투자
    """
    import sys as _sys

    setup_logging()

    env_path = _sys.argv[1] if len(_sys.argv) > 1 else ".env"
    logger.info(f"환경 파일: {env_path}")

    # .env.paper → "paper", .env.live → "live", .env → "default"
    env_name = Path(env_path).suffix.lstrip(".") or "default"
    set_state_path(env_name)
    set_token_path(env_name)

    # settings.yaml도 환경별 분리: config/settings_paper.yaml, config/settings_live.yaml
    if env_name != "default":
        config_path = f"config/settings_{env_name}.yaml"
    else:
        config_path = "config/settings.yaml"

    if not Path(config_path).exists():
        # 환경별 파일 없으면 기본 파일 사용
        config_path = "config/settings.yaml"
        logger.info(f"환경별 설정 없음, 기본 사용: {config_path}")
    else:
        logger.info(f"설정 파일: {config_path}")

    config = load_config(config_path=config_path, env_path=env_path)
    bot = TradingBot(config)

    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
