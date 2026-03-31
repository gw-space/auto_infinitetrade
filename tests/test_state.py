"""상태 관리 테스트."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.strategy.state import (
    AllStates,
    CycleState,
    get_or_create_state,
    load_states,
    save_states,
)


class TestStatePersistence:
    """상태 저장/로드 테스트."""

    def test_save_and_load(self, tmp_path):
        """저장 후 로드하면 동일한 상태."""
        state_file = tmp_path / "state.json"

        states = AllStates()
        states.tickers["TQQQ"] = CycleState(
            symbol="TQQQ",
            cycle_number=2,
            total_capital=10500.0,
            split_amount=262.5,
            num_splits=40,
            splits_used=15.5,
            total_shares=127,
            total_invested=4200.0,
            avg_price=33.07,
            realized_pnl=500.0,
            cycle_start_date="2026-03-01",
            last_order_date="2026-03-28",
            last_action="buy",
        )

        with patch("src.strategy.state.STATE_PATH", state_file):
            save_states(states)
            loaded = load_states()

        assert "TQQQ" in loaded.tickers
        s = loaded.tickers["TQQQ"]
        assert s.cycle_number == 2
        assert s.total_shares == 127
        assert s.avg_price == pytest.approx(33.07, abs=0.01)

    def test_load_missing_file(self, tmp_path):
        """파일 없으면 빈 상태."""
        state_file = tmp_path / "nonexistent.json"

        with patch("src.strategy.state.STATE_PATH", state_file):
            states = load_states()

        assert len(states.tickers) == 0

    def test_load_corrupted_file(self, tmp_path):
        """손상된 파일이면 빈 상태."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not json at all")

        with patch("src.strategy.state.STATE_PATH", state_file):
            states = load_states()

        assert len(states.tickers) == 0

    def test_atomic_write(self, tmp_path):
        """atomic write: tmp 파일 후 rename."""
        state_file = tmp_path / "state.json"

        states = AllStates()
        states.tickers["QQQ"] = CycleState(symbol="QQQ")

        with patch("src.strategy.state.STATE_PATH", state_file):
            save_states(states)

        # tmp 파일이 남아있지 않아야 함
        assert not (tmp_path / "state.tmp").exists()
        assert state_file.exists()


class TestGetOrCreate:
    """get_or_create_state 테스트."""

    def test_create_new(self):
        states = AllStates()
        state = get_or_create_state(states, "TQQQ", 10000.0, 40, 0.10, "2026-04-01")

        assert state.symbol == "TQQQ"
        assert state.total_capital == 10000.0
        assert state.split_amount == 250.0
        assert "TQQQ" in states.tickers

    def test_get_existing(self):
        states = AllStates()
        states.tickers["TQQQ"] = CycleState(
            symbol="TQQQ", cycle_number=3, total_capital=15000.0
        )

        state = get_or_create_state(states, "TQQQ", 10000.0, 40, 0.10, "2026-04-01")
        assert state.cycle_number == 3  # 기존 상태 유지
        assert state.total_capital == 15000.0
