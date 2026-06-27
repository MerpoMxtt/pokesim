"""
tests/test_cli.py
-----------------
Tests for pokesim/cli.py.

Only the non-interactive, pure-logic parts are tested here.
Interactive flows (pick_pokemon, maybe_swap_moves) require a real terminal
and are covered by manual testing.
"""

import io
import sys
import pytest

from pokesim.cli import (
    _hp_bar,
    _supports_color,
    print_results,
)
from pokesim.engine import BattleResult


# ---------------------------------------------------------------------------
# _hp_bar
# ---------------------------------------------------------------------------

class TestHpBar:
    def test_full_hp(self):
        bar = _hp_bar(200, 200)
        assert "[" in bar and "]" in bar

    def test_zero_hp(self):
        bar = _hp_bar(0, 200)
        assert "[" in bar

    def test_zero_max_returns_empty(self):
        bar = _hp_bar(0, 0)
        assert bar == ""

    def test_partial_hp(self):
        bar_full = _hp_bar(200, 200, width=20)
        bar_half = _hp_bar(100, 200, width=20)
        bar_zero = _hp_bar(0,   200, width=20)
        # Strip ANSI for length comparison
        import re
        strip = lambda s: re.sub(r'\033\[[0-9;]*m', '', s)
        # Full HP bar should have more fill characters than half
        assert strip(bar_full).count("|") >= strip(bar_half).count("|")
        assert strip(bar_half).count("|") >= strip(bar_zero).count("|")


# ---------------------------------------------------------------------------
# print_results  (smoke test - just check it doesn't crash)
# ---------------------------------------------------------------------------

class TestPrintResults:
    def _make_result(self, p1_wins, p2_wins, mode="exact") -> BattleResult:
        total = p1_wins + p2_wins
        return BattleResult(
            p1_name="rayquaza",
            p2_name="dialga",
            p1_wins=p1_wins,
            p2_wins=p2_wins,
            total=total,
            mode=mode,
            states_visited=1234,
        )

    def test_print_results_runs_without_error(self, capsys):
        result = self._make_result(64800, 35200)
        print_results(result)
        captured = capsys.readouterr()
        assert "rayquaza" in captured.out.lower() or "RAYQUAZA" in captured.out

    def test_print_results_shows_percentages(self, capsys):
        result = self._make_result(75000, 25000)
        print_results(result)
        captured = capsys.readouterr()
        assert "75.0%" in captured.out

    def test_print_results_monte_mode(self, capsys):
        result = self._make_result(60000, 40000, mode="monte")
        print_results(result)
        captured = capsys.readouterr()
        assert "monte" in captured.out

    def test_print_results_exact_mode(self, capsys):
        result = self._make_result(60000, 40000, mode="exact")
        print_results(result)
        captured = capsys.readouterr()
        assert "exact" in captured.out

    def test_print_results_close_matchup(self, capsys):
        result = self._make_result(51000, 49000)
        print_results(result)
        captured = capsys.readouterr()
        # 51% is within the "too close to call" band (<=55%)
        assert "TOO CLOSE TO CALL" in captured.out

    def test_print_results_p1_dominant(self, capsys):
        result = self._make_result(80000, 20000)
        print_results(result)
        captured = capsys.readouterr()
        assert "RAYQUAZA" in captured.out

    def test_print_results_p2_dominant(self, capsys):
        result = self._make_result(20000, 80000)
        print_results(result)
        captured = capsys.readouterr()
        assert "DIALGA" in captured.out

    def test_total_shown(self, capsys):
        result = self._make_result(64800, 35200)
        print_results(result)
        captured = capsys.readouterr()
        assert "100000" in captured.out

    def test_states_visited_shown(self, capsys):
        result = self._make_result(64800, 35200)
        print_results(result)
        captured = capsys.readouterr()
        assert "1,234" in captured.out


# ---------------------------------------------------------------------------
# BattleResult properties (via cli import path)
# ---------------------------------------------------------------------------

class TestBattleResultProperties:
    def test_p1_pct(self):
        r = BattleResult("a", "b", 75, 25, 100, "exact")
        assert r.p1_pct == 75.0

    def test_p2_pct(self):
        r = BattleResult("a", "b", 75, 25, 100, "exact")
        assert r.p2_pct == 25.0

    def test_winner_p1(self):
        r = BattleResult("rayquaza", "dialga", 75, 25, 100, "exact")
        assert r.winner == "rayquaza"

    def test_winner_p2(self):
        r = BattleResult("rayquaza", "dialga", 25, 75, 100, "exact")
        assert r.winner == "dialga"

    def test_winner_draw(self):
        r = BattleResult("a", "b", 50, 50, 100, "exact")
        assert r.winner == "draw"

    def test_zero_total_pct(self):
        r = BattleResult("a", "b", 0, 0, 0, "exact")
        assert r.p1_pct == 0.0
        assert r.p2_pct == 0.0
