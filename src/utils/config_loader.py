"""설정 로더 - YAML + .env 파일 로딩 및 검증."""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

import yaml
from dotenv import load_dotenv


@dataclass
class TickerConfig:
    symbol: str
    exchange: str
    total_capital: float
    num_splits: int = 40
    profit_target_pct: float = 0.10


@dataclass
class ScheduleConfig:
    order_time: str = "09:35"
    check_time: str = "16:15"
    report_time: str = "16:30"


@dataclass
class AlertConfig:
    max_drawdown_pct: float = 0.20
    order_retry_count: int = 3
    max_order_qty: int = 10           # 1회 최대 주문 수량 안전장치 (LOC 평단/고가 각각)
    max_daily_orders: int = 2         # 일일 최대 주문 횟수 안전장치 (정상=1, 2회째 차단)
    auto_pause_drawdown_pct: float = 0.30  # 자동 일시중지 낙폭 (30%)


@dataclass
class BackupConfig:
    monthly_day: int = 1


@dataclass
class KISConfig:
    app_key: str = ""
    app_secret: str = ""
    account_number: str = ""
    is_paper: bool = True
    base_url: str = ""

    def __post_init__(self):
        if self.is_paper:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class GoogleSheetsConfig:
    spreadsheet_id: str = ""
    credentials_path: str = "credentials/service_account.json"


VALID_OVER40_STRATEGIES = ("quarter", "lower_target", "hold", "full_exit")


@dataclass
class AppConfig:
    kis: KISConfig = field(default_factory=KISConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    google_sheets: GoogleSheetsConfig = field(default_factory=GoogleSheetsConfig)
    tickers: list[TickerConfig] = field(default_factory=list)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    over40_strategy: str = "quarter"


def load_config(
    config_path: str = "config/settings.yaml",
    env_path: str = ".env",
) -> AppConfig:
    """YAML 설정 파일과 .env 환경변수를 로드하여 AppConfig를 반환한다."""
    load_dotenv(env_path)

    yaml_config: dict = {}
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file) as f:
            yaml_config = yaml.safe_load(f) or {}

    # KIS 설정 (.env에서)
    kis_env = os.getenv("KIS_ENV", "paper").lower()
    kis = KISConfig(
        app_key=os.getenv("KIS_APP_KEY", ""),
        app_secret=os.getenv("KIS_APP_SECRET", ""),
        account_number=os.getenv("KIS_ACCOUNT_NUMBER", ""),
        is_paper=(kis_env == "paper"),
    )

    # 텔레그램 설정 (.env에서)
    telegram = TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    # 구글 시트 설정 (.env에서)
    google_sheets = GoogleSheetsConfig(
        spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        credentials_path=os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
        ),
    )

    # 종목 설정 (YAML에서)
    tickers = []
    for t in yaml_config.get("tickers", []):
        tickers.append(
            TickerConfig(
                symbol=t["symbol"],
                exchange=t["exchange"],
                total_capital=float(t["total_capital"]),
                num_splits=int(t.get("num_splits", 40)),
                profit_target_pct=float(t.get("profit_target_pct", 0.10)),
            )
        )

    # 스케줄 설정
    sched = yaml_config.get("schedule", {})
    schedule = ScheduleConfig(
        order_time=sched.get("order_time", "09:35"),
        check_time=sched.get("check_time", "16:15"),
        report_time=sched.get("report_time", "16:30"),
    )

    # 알림 설정
    alert = yaml_config.get("alerts", {})
    alerts = AlertConfig(
        max_drawdown_pct=float(alert.get("max_drawdown_pct", 0.20)),
        order_retry_count=int(alert.get("order_retry_count", 3)),
        max_order_qty=int(alert.get("max_order_qty", 10)),
        max_daily_orders=int(alert.get("max_daily_orders", 2)),
        auto_pause_drawdown_pct=float(alert.get("auto_pause_drawdown_pct", 0.30)),
    )

    # 백업 설정
    bkup = yaml_config.get("backup", {})
    backup = BackupConfig(monthly_day=int(bkup.get("monthly_day", 1)))

    # 40회차 소진 전략 (.env 우선, YAML 폴백)
    over40_strategy = os.getenv(
        "OVER40_STRATEGY",
        yaml_config.get("over40_strategy", "quarter"),
    ).lower()

    config = AppConfig(
        kis=kis,
        telegram=telegram,
        google_sheets=google_sheets,
        tickers=tickers,
        schedule=schedule,
        alerts=alerts,
        backup=backup,
        over40_strategy=over40_strategy,
    )

    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    """필수 설정값 검증. 문제 시 ValueError 발생."""
    errors: list[str] = []

    if not config.kis.app_key:
        errors.append("KIS_APP_KEY가 설정되지 않았습니다.")
    if not config.kis.app_secret:
        errors.append("KIS_APP_SECRET가 설정되지 않았습니다.")
    if not config.kis.account_number:
        errors.append("KIS_ACCOUNT_NUMBER가 설정되지 않았습니다.")
    if not config.telegram.bot_token:
        errors.append("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")
    if not config.telegram.chat_id:
        errors.append("TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
    if not config.google_sheets.spreadsheet_id:
        errors.append("GOOGLE_SHEETS_SPREADSHEET_ID가 설정되지 않았습니다.")
    if not config.tickers:
        errors.append("매매 대상 종목이 설정되지 않았습니다.")

    ticker_re = re.compile(r"^[A-Z0-9]{1,10}$")
    valid_exchanges = {"NASD", "NYSE", "AMEX"}

    for i, t in enumerate(config.tickers):
        if not ticker_re.match(t.symbol):
            errors.append(f"종목 코드 형식 오류: '{t.symbol}' (영문대문자/숫자 1~10자)")
        if t.exchange not in valid_exchanges:
            errors.append(f"거래소 코드 오류: '{t.exchange}' (NASD/NYSE/AMEX)")
        if t.total_capital <= 0:
            errors.append(f"종목 {t.symbol}: total_capital은 0보다 커야 합니다.")
        if t.num_splits <= 0:
            errors.append(f"종목 {t.symbol}: num_splits는 0보다 커야 합니다.")
        if t.profit_target_pct <= 0:
            errors.append(f"종목 {t.symbol}: profit_target_pct는 0보다 커야 합니다.")

        # 1회차 금액으로 1주도 못 사는지 경고
        split_amount = t.total_capital / t.num_splits
        half_split = split_amount * 0.5
        if half_split < 1.0:
            errors.append(
                f"종목 {t.symbol}: 0.5회차 금액(${half_split:.2f})이 너무 작습니다. "
                f"total_capital을 늘리거나 num_splits를 줄이세요."
            )
        elif split_amount < 10.0:
            logger.warning(
                f"종목 {t.symbol}: 1회차 금액 ${split_amount:.2f} — "
                f"주가가 ${half_split:.2f} 이상이면 0주 매수됩니다. "
                f"total_capital({t.total_capital})과 종목 주가를 확인하세요."
            )

    time_re = re.compile(r"^\d{2}:\d{2}$")
    for field_name, val in [
        ("order_time", config.schedule.order_time),
        ("check_time", config.schedule.check_time),
        ("report_time", config.schedule.report_time),
    ]:
        if not time_re.match(val):
            errors.append(f"schedule.{field_name}은 HH:MM 형식이어야 합니다: '{val}'")

    if config.google_sheets.credentials_path:
        creds_path = Path(config.google_sheets.credentials_path).resolve()
        allowed_root = Path("credentials").resolve()
        if not creds_path.is_relative_to(allowed_root):
            errors.append(
                f"credentials_path는 'credentials/' 내부여야 합니다: {creds_path}"
            )
        elif not creds_path.exists():
            errors.append(f"Google credentials 파일을 찾을 수 없습니다: {creds_path}")

    if config.over40_strategy not in VALID_OVER40_STRATEGIES:
        errors.append(
            f"OVER40_STRATEGY '{config.over40_strategy}'는 유효하지 않습니다. "
            f"선택 가능: {', '.join(VALID_OVER40_STRATEGIES)}"
        )

    if errors:
        raise ValueError("설정 오류:\n" + "\n".join(f"  - {e}" for e in errors))
