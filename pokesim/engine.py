"""
pokesim/engine.py
-----------------
Core battle simulation engine.

Two modes
---------
EXACT   — full decision-tree enumeration. Counts every possible battle path
          and how many lead to each Pokémon winning. Used when the state space
          is small enough (≤ EXACT_STATE_LIMIT unique states).

MONTE   — Monte Carlo random simulation. Runs N independent random battles
          and tallies wins. Used when the matchup is too complex for exact
          enumeration.

Public API
----------
    result = run(p1, p2)          # BattleResult

    result.p1_wins                # int  — paths / simulations won by P1
    result.p2_wins                # int  — paths / simulations won by P2
    result.total                  # int  — total paths / simulations
    result.mode                   # "exact" | "monte"
    result.states_visited         # int  — unique states seen (exact only)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import NamedTuple

from pokesim.data import type_multiplier, real_hp, real_stat

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

LEVEL             = 50          # battle level for all Pokémon
EXACT_STATE_LIMIT = 150_000     # switch to Monte Carlo above this
MONTE_SIMULATIONS = 100_000     # random battles to run in Monte Carlo mode
MAX_TURNS         = 50          # safety cap — draw if exceeded

# ---------------------------------------------------------------------------
# Move dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    slug:     str
    name:     str
    power:    int       # 0 for status moves
    accuracy: int       # 0 = never misses
    pp:       int
    category: str       # "physical" | "special" | "status"
    type:     str       # e.g. "electric"
    effect:   str | None
    priority: int = 0

    @classmethod
    def from_dict(cls, slug: str, d: dict) -> "Move":
        return cls(
            slug=slug,
            name=d.get("name", slug),
            power=d.get("power", 0),
            accuracy=d.get("accuracy", 100),
            pp=d.get("pp", 10),
            category=d.get("category", "physical"),
            type=d.get("type", "normal"),
            effect=d.get("effect"),
            priority=d.get("priority", 0),
        )


# ---------------------------------------------------------------------------
# Pokémon combatant
# ---------------------------------------------------------------------------

@dataclass
class Pokemon:
    name:    str
    types:   list[str]
    max_hp:  int
    attack:  int
    defense: int
    sp_atk:  int
    sp_def:  int
    speed:   int
    moves:   list[Move]

    @classmethod
    def from_dict(cls, pdata: dict, moves: list[Move]) -> "Pokemon":
        s = pdata["stats"]
        return cls(
            name=pdata["name"],
            types=pdata["types"],
            max_hp=real_hp(s["hp"], LEVEL),
            attack=real_stat(s["attack"], LEVEL),
            defense=real_stat(s["defense"], LEVEL),
            sp_atk=real_stat(s["special-attack"], LEVEL),
            sp_def=real_stat(s["special-defense"], LEVEL),
            speed=real_stat(s["speed"], LEVEL),
            moves=moves,
        )


# ---------------------------------------------------------------------------
# Battle state (hashable — used as memo key in exact mode)
# ---------------------------------------------------------------------------

class BattleState(NamedTuple):
    hp1:     int   # bucketed HP of P1
    hp2:     int   # bucketed HP of P2
    status1: int   # 0=none 1=burn 2=par 3=psn 4=frz 5=slp
    status2: int


def _bucket(hp: int, max_hp: int, buckets: int = 20) -> int:
    """Reduce HP to a bucket index to collapse near-identical states."""
    if hp <= 0:
        return 0
    return max(1, math.ceil(hp / max_hp * buckets))


# ---------------------------------------------------------------------------
# Damage formula  (Gen 8)
# ---------------------------------------------------------------------------

def _calc_damage(
    attacker: Pokemon,
    defender: Pokemon,
    move: Move,
    roll: float = 0.925,
    critical: bool = False,
    attacker_status: int = 0,
) -> int:
    """
    Return damage dealt by attacker using move against defender.

    roll      — damage roll in [0.85, 1.0]
    critical  — if True, apply 1.5x crit multiplier
    """
    if move.power == 0:
        return 0

    if move.category == "physical":
        atk  = attacker.attack
        def_ = defender.defense
        if attacker_status == 1:   # BURN halves physical attack
            atk = atk // 2
    else:
        atk  = attacker.sp_atk
        def_ = defender.sp_def

    base = ((2 * LEVEL / 5 + 2) * move.power * atk / def_) / 50 + 2

    stab          = 1.5 if move.type in attacker.types else 1.0
    effectiveness = type_multiplier(move.type, defender.types)
    crit_mult     = 1.5 if critical else 1.0

    if effectiveness == 0:
        return 0

    damage = math.floor(base * stab * effectiveness * crit_mult * roll)
    return max(1, damage)


# ---------------------------------------------------------------------------
# Turn order
# ---------------------------------------------------------------------------

def _goes_first(
    p1: Pokemon, p2: Pokemon,
    m1: Move, m2: Move,
    status1: int, status2: int,
) -> bool:
    """Return True if P1 moves first this turn."""
    if m1.priority != m2.priority:
        return m1.priority > m2.priority

    spd1 = p1.speed // 2 if status1 == 2 else p1.speed   # paralysis halves speed
    spd2 = p2.speed // 2 if status2 == 2 else p2.speed

    if spd1 != spd2:
        return spd1 > spd2

    return True   # tie -> P1 (arbitrary)


# ---------------------------------------------------------------------------
# End-of-turn status damage
# ---------------------------------------------------------------------------

_EOT_DAMAGE_FRACTION = {
    1: 16,   # burn   -> 1/16 max HP
    3: 8,    # poison -> 1/8  max HP
}


def _apply_eot(hp: int, max_hp: int, status: int) -> int:
    """Return updated HP after end-of-turn status damage."""
    denom = _EOT_DAMAGE_FRACTION.get(status)
    if denom:
        hp -= max(1, max_hp // denom)
    return max(0, hp)


# ---------------------------------------------------------------------------
# EXACT MODE — decision tree with memoization
# ---------------------------------------------------------------------------

def _exact(
    p1: Pokemon,
    p2: Pokemon,
    state: BattleState,
    memo: dict,
    counter: list[int],
    depth: int = 0,
) -> tuple[float, float]:
    """
    Returns (p1_win_weight, p2_win_weight) from this state.

    Win weights are normalized fractions (not raw path counts) to prevent
    integer explosion through deep recursion.
    """
    if state.hp1 <= 0:
        return (0.0, 1.0)
    if state.hp2 <= 0:
        return (1.0, 0.0)
    if depth >= MAX_TURNS:
        return (0.5, 0.5)   # draw

    if state in memo:
        return memo[state]

    counter[0] += 1
    if counter[0] > EXACT_STATE_LIMIT:
        # Budget exhausted — heuristic based on relative HP
        ratio = state.hp1 / max(state.hp1 + state.hp2, 1)
        return (ratio, 1.0 - ratio)

    total_p1 = 0.0
    total_p2 = 0.0
    branches = 0

    for m1 in p1.moves:
        for m2 in p2.moves:
            p1_first = _goes_first(p1, p2, m1, m2, state.status1, state.status2)
            outcomes = _enumerate_turn(p1, p2, m1, m2, state, p1_first)

            for new_state in outcomes:
                sub_p1, sub_p2 = _exact(p1, p2, new_state, memo, counter, depth + 1)
                total_p1 += sub_p1
                total_p2 += sub_p2
                branches += 1

    if branches == 0:
        result = (0.5, 0.5)
    else:
        # Normalize so values stay in [0, 1] range
        total = total_p1 + total_p2
        result = (total_p1 / total, total_p2 / total) if total > 0 else (0.5, 0.5)

    memo[state] = result
    return result


def _enumerate_turn(
    p1: Pokemon,
    p2: Pokemon,
    m1: Move,
    m2: Move,
    state: BattleState,
    p1_first: bool,
) -> list[BattleState]:
    """
    Enumerate distinct resulting states from one full turn.
    For each move: miss | low roll | high roll (3 branches).
    """

    def apply_move(
        hp_atk: int, hp_def: int,
        move: Move,
        attacker: Pokemon,
        defender: Pokemon,
        attacker_status: int,
    ) -> list[tuple[int, int]]:
        """Returns list of (new_atk_hp, new_def_hp) pairs."""
        if move.category == "status":
            return [(hp_atk, hp_def)]

        eff = type_multiplier(move.type, defender.types)
        if eff == 0:
            return [(hp_atk, hp_def)]   # immune

        pairs: list[tuple[int, int]] = []

        # Miss branch (skip for never-miss moves)
        if move.accuracy != 0:
            pairs.append((hp_atk, hp_def))

        # Damage branches: low roll and high roll
        for roll in (0.85, 1.0):
            dmg = _calc_damage(attacker, defender, move, roll,
                               critical=False, attacker_status=attacker_status)
            new_def = max(0, hp_def - dmg)
            # Recoil
            if move.effect == "recoil_33":
                new_atk = max(0, hp_atk - max(1, dmg // 3))
            else:
                new_atk = hp_atk
            pairs.append((new_atk, new_def))

        return pairs

    # Debucket to approximate real HP
    hp1 = round(state.hp1 / 20 * p1.max_hp)
    hp2 = round(state.hp2 / 20 * p2.max_hp)
    st1, st2 = state.status1, state.status2

    results: list[BattleState] = []

    if p1_first:
        for h1a, h2a in apply_move(hp1, hp2, m1, p1, p2, st1):
            if h2a <= 0:
                results.append(BattleState(_bucket(h1a, p1.max_hp), 0, st1, st2))
                continue
            for h2b, h1b in apply_move(h2a, h1a, m2, p2, p1, st2):
                h1_eot = _apply_eot(h1b, p1.max_hp, st1)
                h2_eot = _apply_eot(h2b, p2.max_hp, st2)
                results.append(BattleState(
                    _bucket(h1_eot, p1.max_hp),
                    _bucket(h2_eot, p2.max_hp),
                    st1, st2,
                ))
    else:
        for h2a, h1a in apply_move(hp2, hp1, m2, p2, p1, st2):
            if h1a <= 0:
                results.append(BattleState(0, _bucket(h2a, p2.max_hp), st1, st2))
                continue
            for h1b, h2b in apply_move(h1a, h2a, m1, p1, p2, st1):
                h1_eot = _apply_eot(h1b, p1.max_hp, st1)
                h2_eot = _apply_eot(h2b, p2.max_hp, st2)
                results.append(BattleState(
                    _bucket(h1_eot, p1.max_hp),
                    _bucket(h2_eot, p2.max_hp),
                    st1, st2,
                ))

    return list(dict.fromkeys(results))   # deduplicate, preserve order


# ---------------------------------------------------------------------------
# MONTE CARLO MODE
# ---------------------------------------------------------------------------

def _monte_carlo(
    p1: Pokemon,
    p2: Pokemon,
    n: int,
    rng: random.Random,
) -> tuple[int, int]:
    """Run n random battles. Return (p1_wins, p2_wins)."""
    p1_wins = 0
    p2_wins = 0
    for _ in range(n):
        if _simulate_one(p1, p2, rng) == 1:
            p1_wins += 1
        else:
            p2_wins += 1
    return p1_wins, p2_wins


def _hit_and_damage(
    attacker: Pokemon,
    defender: Pokemon,
    move: Move,
    attacker_status: int,
    rng: random.Random,
) -> int:
    """Monte Carlo version — roll accuracy, crit, and damage randomly."""
    if move.category == "status":
        return 0
    if move.accuracy != 0 and rng.randint(1, 100) > move.accuracy:
        return 0   # missed
    critical = rng.randint(1, 24) == 1
    roll = rng.uniform(0.85, 1.0)
    return _calc_damage(attacker, defender, move, roll, critical, attacker_status)


def _simulate_one(
    p1: Pokemon,
    p2: Pokemon,
    rng: random.Random,
) -> int:
    """Simulate a single random battle. Returns 1 (P1 wins) or 2 (P2 wins)."""
    hp1, hp2 = p1.max_hp, p2.max_hp
    status1 = status2 = 0

    for _ in range(MAX_TURNS):
        m1 = rng.choice(p1.moves)
        m2 = rng.choice(p2.moves)
        p1_first = _goes_first(p1, p2, m1, m2, status1, status2)

        if p1_first:
            hp2 -= _hit_and_damage(p1, p2, m1, status1, rng)
            if hp2 <= 0:
                return 1
            hp1 -= _hit_and_damage(p2, p1, m2, status2, rng)
            if hp1 <= 0:
                return 2
        else:
            hp1 -= _hit_and_damage(p2, p1, m2, status2, rng)
            if hp1 <= 0:
                return 2
            hp2 -= _hit_and_damage(p1, p2, m1, status1, rng)
            if hp2 <= 0:
                return 1

        hp1 = _apply_eot(hp1, p1.max_hp, status1)
        hp2 = _apply_eot(hp2, p2.max_hp, status2)
        if hp1 <= 0:
            return 2
        if hp2 <= 0:
            return 1

    return 1 if hp1 >= hp2 else 2


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class BattleResult:
    p1_name:        str
    p2_name:        str
    p1_wins:        int
    p2_wins:        int
    total:          int
    mode:           str        # "exact" | "monte"
    states_visited: int = 0

    @property
    def p1_pct(self) -> float:
        return self.p1_wins / self.total * 100 if self.total else 0.0

    @property
    def p2_pct(self) -> float:
        return self.p2_wins / self.total * 100 if self.total else 0.0

    @property
    def winner(self) -> str:
        if self.p1_pct > self.p2_pct:
            return self.p1_name
        if self.p2_pct > self.p1_pct:
            return self.p2_name
        return "draw"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(p1: Pokemon, p2: Pokemon, seed: int | None = None) -> BattleResult:
    """
    Simulate all possible battle outcomes between p1 and p2.

    Automatically chooses exact enumeration or Monte Carlo based on
    the complexity of the matchup.

    Parameters
    ----------
    p1, p2  : Pokemon combatants with their chosen movesets
    seed    : optional RNG seed for reproducible Monte Carlo results

    Returns
    -------
    BattleResult with win counts, percentages, and metadata.
    """
    rng = random.Random(seed)

    memo: dict   = {}
    counter      = [0]
    initial      = BattleState(
        _bucket(p1.max_hp, p1.max_hp),
        _bucket(p2.max_hp, p2.max_hp),
        0, 0,
    )

    p1_weight, p2_weight = _exact(p1, p2, initial, memo, counter)
    states = counter[0]

    if states <= EXACT_STATE_LIMIT:
        # Scale weights to integer win counts for readable output
        scale   = 100_000
        p1_wins = round(p1_weight * scale)
        p2_wins = round(p2_weight * scale)
        return BattleResult(
            p1_name=p1.name,
            p2_name=p2.name,
            p1_wins=p1_wins,
            p2_wins=p2_wins,
            total=p1_wins + p2_wins,
            mode="exact",
            states_visited=states,
        )

    # Budget exceeded — Monte Carlo
    p1_wins, p2_wins = _monte_carlo(p1, p2, MONTE_SIMULATIONS, rng)
    return BattleResult(
        p1_name=p1.name,
        p2_name=p2.name,
        p1_wins=p1_wins,
        p2_wins=p2_wins,
        total=MONTE_SIMULATIONS,
        mode="monte",
        states_visited=states,
    )
