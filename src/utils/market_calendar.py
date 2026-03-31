"""미국 시장 캘린더 (휴일, 조기 폐장 체크)."""

import logging
from datetime import date, datetime, timedelta

import exchange_calendars as xcals

logger = logging.getLogger(__name__)

_calendar = xcals.get_calendar("XNYS")


def is_trading_day(d: date | None = None) -> bool:
    """주어진 날짜가 미국 시장 거래일인지 확인한다."""
    if d is None:
        d = date.today()

    ts = datetime(d.year, d.month, d.day)
    result = _calendar.is_session(ts)
    if not result:
        logger.info(f"{d} 은 미국 시장 휴장일입니다.")
    return result


def is_early_close(d: date | None = None) -> bool:
    """주어진 날짜가 조기 폐장일인지 확인한다."""
    if d is None:
        d = date.today()

    ts = datetime(d.year, d.month, d.day)
    if not _calendar.is_session(ts):
        return False

    close_time = _calendar.session_close(ts)
    # 정규 폐장 16:00 ET 보다 일찍 끝나면 조기 폐장
    return close_time.hour < 16


def get_next_trading_day(d: date | None = None) -> date:
    """다음 거래일을 반환한다."""
    if d is None:
        d = date.today()

    ts = datetime(d.year, d.month, d.day)
    sessions = _calendar.sessions_in_range(ts, ts + timedelta(days=10))

    for s in sessions:
        s_date = s.date()
        if s_date > d:
            return s_date

    return d


def count_missed_days(last_date: str, today: str) -> int:
    """마지막 주문일과 오늘 사이의 놓친 거래일 수를 계산한다."""
    if not last_date:
        return 0

    from_date = date.fromisoformat(last_date)
    to_date = date.fromisoformat(today)

    if from_date >= to_date:
        return 0

    from_dt = datetime(from_date.year, from_date.month, from_date.day)
    to_dt = datetime(to_date.year, to_date.month, to_date.day)
    sessions = _calendar.sessions_in_range(from_dt, to_dt)

    # last_date와 today 제외
    missed = sum(1 for s in sessions if from_date < s.date() < to_date)
    return missed
