"""사이클 상태 관리 (멀티 종목 지원)."""

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_PATH = Path("data/state.json")  # 기본값, set_state_path()로 변경 가능


def set_state_path(env_name: str) -> None:
    """환경에 따라 state 파일 경로를 분리한다.

    Args:
        env_name: 환경 이름 (예: "paper", "live")
    """
    global STATE_PATH
    if env_name and env_name != "default":
        STATE_PATH = Path(f"data/state_{env_name}.json")
    logger.info(f"상태 파일: {STATE_PATH}")


@dataclass
class CycleState:
    """한 종목의 사이클 상태."""
    symbol: str
    cycle_number: int = 1
    total_capital: float = 0.0      # 이번 사이클 투자 총액
    split_amount: float = 0.0       # 1회차 금액 (total_capital / num_splits)
    num_splits: int = 40
    splits_used: float = 0.0        # 실제 체결 기준 소진 분할 수
    total_shares: int = 0           # 보유 주식 수
    total_invested: float = 0.0     # 누적 매수 금액
    avg_price: float = 0.0          # 평균 매입가
    realized_pnl: float = 0.0       # 전체 누적 실현 손익
    cycle_start_date: str = ""
    last_order_date: str = ""       # 마지막 주문일 (중복 방지)
    last_action: str = ""           # "buy", "sell", "hold"
    is_paused: bool = False         # /pause 상태
    is_dryrun: bool = False         # 드라이런 모드
    pending_sell: bool = False      # 40회차 소진 후 매도 대기 상태
    profit_target_pct: float = 0.10
    over40_strategy: str = "quarter"  # 40회차 소진 전략
    over40_executed: bool = False     # 40회차 전략 실행 완료 여부
    quarter_used: bool = False        # quarter 이미 1회 사용 여부
    # 모의투자 LOC 의도 저장 (당일만 유효)
    daily_order_count: int = 0        # 당일 주문 횟수 (안전장치)
    daily_order_date: str = ""        # 주문 횟수 카운트 날짜
    paper_loc_plan: dict = None       # 모의투자 LOC 의도 저장
    processed_order_ids: list = None  # 처리 완료된 주문 ID 목록

    def __post_init__(self):
        if self.processed_order_ids is None:
            self.processed_order_ids = []
        if self.paper_loc_plan is None:
            self.paper_loc_plan = {}


@dataclass
class AllStates:
    """전체 종목 상태."""
    tickers: dict[str, CycleState] = field(default_factory=dict)


def load_states() -> AllStates:
    """state.json에서 전체 상태를 로드한다."""
    if not STATE_PATH.exists():
        return AllStates()

    try:
        with open(STATE_PATH) as f:
            data = json.load(f)

        states = AllStates()
        for symbol, state_data in data.get("tickers", {}).items():
            states.tickers[symbol] = CycleState(**state_data)
        return states
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"상태 파일 로드 실패, 초기 상태 사용: {e}")
        return AllStates()


def save_states(states: AllStates) -> None:
    """전체 상태를 state.json에 저장한다 (atomic write)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")

    data = {
        "tickers": {
            symbol: asdict(state)
            for symbol, state in states.tickers.items()
        }
    }

    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    tmp_path.rename(STATE_PATH)
    STATE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    logger.debug("상태 저장 완료")


def get_or_create_state(
    states: AllStates,
    symbol: str,
    total_capital: float,
    num_splits: int,
    profit_target_pct: float,
    today: str,
) -> CycleState:
    """종목의 상태를 가져오거나 새로 생성한다."""
    if symbol not in states.tickers:
        state = CycleState(
            symbol=symbol,
            cycle_number=1,
            total_capital=total_capital,
            split_amount=total_capital / num_splits,
            num_splits=num_splits,
            profit_target_pct=profit_target_pct,
            cycle_start_date=today,
        )
        states.tickers[symbol] = state
        logger.info(f"{symbol}: 새 사이클 시작 (자본: ${total_capital:.2f}, {num_splits}분할)")

    return states.tickers[symbol]


def reset_cycle(
    state: CycleState,
    today: str,
    available_cash: float = 0.0,
    capital_limit: float = 0.0,
) -> None:
    """사이클을 초기화한다.

    Args:
        state: 사이클 상태
        today: 오늘 날짜
        available_cash: KIS 계좌 가용 잔고 (USD). 0이면 기존 방식.
        capital_limit: settings.yaml의 total_capital 상한선. 0이면 무제한.
    """
    prev_capital = state.total_capital + state.realized_pnl
    prev_cycle = state.cycle_number

    # 잔고 기반 자본금 결정
    if available_cash > 0:
        new_capital = available_cash
    else:
        new_capital = prev_capital

    # 상한선 적용
    if capital_limit > 0:
        new_capital = min(new_capital, capital_limit)

    state.cycle_number += 1
    state.total_capital = new_capital
    state.realized_pnl = 0.0
    state.split_amount = new_capital / state.num_splits
    state.splits_used = 0.0
    state.total_shares = 0
    state.total_invested = 0.0
    state.avg_price = 0.0
    state.cycle_start_date = today
    state.last_action = ""
    state.pending_sell = False
    state.over40_executed = False
    state.quarter_used = False
    state.processed_order_ids = []

    logger.info(
        f"{state.symbol}: 사이클 {prev_cycle} → {state.cycle_number} "
        f"(새 자본: ${new_capital:.2f})"
    )
