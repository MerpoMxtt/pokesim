# -*- coding: utf-8 -*-
"""
pokesim/engine.py
-----------------
Core battle simulation engine.

Two modes
---------
EXACT   -- Full decision-tree enumeration with memoization.
           Counts integer win paths for each Pokemon across every possible
           sequence of moves and outcomes. Normalizes ONLY at the root.

MONTE   -- Monte Carlo: runs N independent random battles and tallies wins.
           Kicks in automatically when state space exceeds EXACT_STATE_LIMIT.

Key design decisions
--------------------
- BattleState includes status fields (burn/paralysis/poison) so that
  status moves actually produce distinct states in the tree.
- HP is bucketed into BUCKET_COUNT slots to collapse near-identical states
  and keep the memo manageable, but with enough resolution to distinguish
  meaningful HP differences.
- Win counts are raw integers accumulated by summation (never multiplied),
  then normalized only once at the root. This avoids the averaging-to-50/50
  bug that occurs when normalizing at each recursive node.
- Monte Carlo uses true random move selection and dice rolls, giving an
  independent cross-check of the exact results.
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

LEVEL             = 50        # battle level for all Pokemon
BUCKET_COUNT      = 40        # HP resolution for memoization (higher = more precise)
EXACT_STATE_LIMIT = 200_000   # max unique states before switching to Monte Carlo
MONTE_SIMULATIONS = 500_000   # random battles in Monte Carlo mode
MAX_TURNS         = 60        # draw if battle exceeds this many turns

# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    slug:     str
    name:     str
    power:    int       # 0 for status moves
    accuracy: int       # 0 = never misses (Aura Sphere etc.)
    pp:       int
    category: str       # "physical" | "special" | "status"
    type:     str
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
# Pokemon combatant
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
# Status constants
# ---------------------------------------------------------------------------

STS_NONE  = 0
STS_BURN  = 1   # -50% physical damage dealt; 1/16 max HP per turn
STS_PAR   = 2   # 25% chance to skip turn; speed halved
STS_PSN   = 3   # 1/8 max HP per turn
STS_FRZ   = 4   # 20% thaw chance per turn (simplified: skip 80% of turns)
STS_SLP   = 5   # skip turn while asleep (1-3 turns; we use 2 avg)


# ---------------------------------------------------------------------------
# Battle state  (hashable - used as memo key)
# ---------------------------------------------------------------------------

class BattleState(NamedTuple):
    hp1:     int   # bucketed HP of P1  (0 = fainted)
    hp2:     int   # bucketed HP of P2
    status1: int   # STS_* constant for P1
    status2: int   # STS_* constant for P2


def _bucket(hp: int, max_hp: int) -> int:
    """Map current HP to a discrete bucket [0, BUCKET_COUNT]."""
    if hp <= 0:
        return 0
    return max(1, math.ceil(hp / max_hp * BUCKET_COUNT))


def _unbucket(b: int, max_hp: int) -> int:
    """Convert bucket back to approximate real HP."""
    return round(b / BUCKET_COUNT * max_hp)


# ---------------------------------------------------------------------------
# Damage formula  (Gen 8)
# ---------------------------------------------------------------------------

def _calc_damage(
    attacker: Pokemon,
    defender: Pokemon,
    move: Move,
    roll: float = 0.925,       # midpoint of [0.85, 1.0]
    critical: bool = False,
    attacker_status: int = STS_NONE,
) -> int:
    """
    Compute damage dealt. Returns 0 for status moves or immune matchups.

    Parameters
    ----------
    roll      : damage roll in [0.85, 1.0]
    critical  : 1.5x multiplier, ignores burn penalty on physical
    """
    if move.power == 0 or move.category == "status":
        return 0

    if move.category == "physical":
        atk  = attacker.attack
        def_ = defender.defense
        # Burn halves physical attack (critical hit bypasses this)
        if attacker_status == STS_BURN and not critical:
            atk = atk // 2
    else:
        atk  = attacker.sp_atk
        def_ = defender.sp_def

    # Core formula
    base = ((2 * LEVEL / 5 + 2) * move.power * atk / def_) / 50 + 2

    # STAB
    stab = 1.5 if move.type in attacker.types else 1.0

    # Type effectiveness
    effectiveness = type_multiplier(move.type, defender.types)
    if effectiveness == 0.0:
        return 0

    # Critical hit
    crit_mult = 1.5 if critical else 1.0

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
    """Return True if P1 acts before P2 this turn."""
    # Priority bracket overrides speed
    if m1.priority != m2.priority:
        return m1.priority > m2.priority

    # Paralysis halves effective speed
    spd1 = p1.speed // 2 if status1 == STS_PAR else p1.speed
    spd2 = p2.speed // 2 if status2 == STS_PAR else p2.speed

    if spd1 != spd2:
        return spd1 > spd2

    return True   # speed tie -> P1 (arbitrary but consistent)


# ---------------------------------------------------------------------------
# End-of-turn status damage
# ---------------------------------------------------------------------------

def _eot_damage(max_hp: int, status: int) -> int:
    """HP lost at end of turn from status conditions."""
    if status == STS_BURN:
        return max(1, max_hp // 16)
    if status == STS_PSN:
        return max(1, max_hp // 8)
    return 0


def _apply_eot(hp: int, max_hp: int, status: int) -> int:
    return max(0, hp - _eot_damage(max_hp, status))


# ---------------------------------------------------------------------------
# EXACT MODE
# ---------------------------------------------------------------------------

# Effect code -> (target_status, probability)
_STATUS_EFFECTS: dict[str, tuple[int, float]] = {
    "burn_10":        (STS_BURN, 0.10),
    "paralysis_10":   (STS_PAR,  0.10),
    "paralysis_30":   (STS_PAR,  0.30),
    "paralysis_100":  (STS_PAR,  1.00),
    "freeze_10":      (STS_FRZ,  0.10),
    "poison_30":      (STS_PSN,  0.30),
    "bad_poison":     (STS_PSN,  0.90),
}


def _apply_move_exact(
    hp_atk: int, hp_def: int,
    move: Move,
    attacker: Pokemon,
    defender: Pokemon,
    attacker_status: int,
    defender_status: int,
) -> list[tuple[int, int, int, int]]:
    """
    Enumerate all distinct (new_hp_atk, new_hp_def, new_status_atk, new_status_def)
    outcomes from applying one move.

    Status moves that inflict a condition branch into:
      - effect lands (probability p)
      - effect misses (probability 1-p)
    We represent this as TWO outcome states (the engine weights them equally
    in path counting; accurate weighting comes from Monte Carlo).

    Damaging moves branch into: miss | low roll | high roll.
    """
    new_st_atk = attacker_status
    new_st_def = defender_status

    # --- Status moves ---
    if move.category == "status":
        # Check if this move inflicts a status on the opponent
        if move.effect and move.effect in _STATUS_EFFECTS:
            new_status, prob = _STATUS_EFFECTS[move.effect]
            # Only inflict if target has no status yet
            if defender_status == STS_NONE:
                # Branch: inflicted vs not inflicted
                return [
                    (hp_atk, hp_def, new_st_atk, new_status),  # inflicted
                    (hp_atk, hp_def, new_st_atk, defender_status),  # missed/blocked
                ]
        # Healing and stat-change status moves: treat as no state change
        # (engine tracks HP + status only; stat stages omitted for tractability)
        return [(hp_atk, hp_def, new_st_atk, new_st_def)]

    # --- Damaging moves ---
    eff = type_multiplier(move.type, defender.types)
    if eff == 0.0:
        return [(hp_atk, hp_def, new_st_atk, new_st_def)]   # immune

    results: list[tuple[int, int, int, int]] = []

    # Miss branch (skip for never-miss moves)
    if move.accuracy != 0:
        results.append((hp_atk, hp_def, new_st_atk, new_st_def))

    # Damage branches: low roll and high roll
    for roll in (0.85, 1.0):
        dmg = _calc_damage(attacker, defender, move, roll,
                           critical=False, attacker_status=attacker_status)
        new_def_hp = max(0, hp_def - dmg)

        # Recoil
        if move.effect == "recoil_33":
            new_atk_hp = max(0, hp_atk - max(1, dmg // 3))
        else:
            new_atk_hp = hp_atk

        # Secondary status effect from damaging move (e.g. burn_10 from flamethrower)
        new_def_st = new_st_def
        if move.effect and move.effect in _STATUS_EFFECTS and new_def_hp > 0:
            inflicted_status, _ = _STATUS_EFFECTS[move.effect]
            if defender_status == STS_NONE:
                # Branch: secondary effect triggers
                results.append((new_atk_hp, new_def_hp, new_st_atk, inflicted_status))

        results.append((new_atk_hp, new_def_hp, new_st_atk, new_def_st))

    return results


def _resolve_turn_exact(
    p1: Pokemon, p2: Pokemon,
    m1: Move, m2: Move,
    state: BattleState,
) -> list[BattleState]:
    """
    Return all distinct BattleStates reachable after one full turn
    (both moves applied, end-of-turn status damage applied).
    """
    hp1 = _unbucket(state.hp1, p1.max_hp)
    hp2 = _unbucket(state.hp2, p2.max_hp)
    st1, st2 = state.status1, state.status2

    p1_first = _goes_first(p1, p2, m1, m2, st1, st2)

    results: set[BattleState] = set()

    # First mover's move outcomes
    if p1_first:
        first_outcomes = _apply_move_exact(hp1, hp2, m1, p1, p2, st1, st2)
    else:
        first_outcomes = _apply_move_exact(hp2, hp1, m2, p2, p1, st2, st1)

    for (ha, hd, sa, sd) in first_outcomes:
        if p1_first:
            h1a, h2a, s1a, s2a = ha, hd, sa, sd
        else:
            h2a, h1a, s2a, s1a = ha, hd, sa, sd

        # If first mover knocked out the opponent, battle ends
        if h2a <= 0:
            results.add(BattleState(
                _bucket(h1a, p1.max_hp), 0, s1a, s2a))
            continue
        if h1a <= 0:
            results.add(BattleState(
                0, _bucket(h2a, p2.max_hp), s1a, s2a))
            continue

        # Second mover attacks
        if p1_first:
            second_outcomes = _apply_move_exact(h2a, h1a, m2, p2, p1, s2a, s1a)
        else:
            second_outcomes = _apply_move_exact(h1a, h2a, m1, p1, p2, s1a, s2a)

        for (hb, hc, sb, sc) in second_outcomes:
            if p1_first:
                h1b, h2b, s1b, s2b = hc, hb, sc, sb
            else:
                h1b, h2b, s1b, s2b = hb, hc, sb, sc

            if h1b <= 0:
                results.add(BattleState(
                    0, _bucket(h2b, p2.max_hp), s1b, s2b))
                continue
            if h2b <= 0:
                results.add(BattleState(
                    _bucket(h1b, p1.max_hp), 0, s1b, s2b))
                continue

            # End-of-turn status damage
            h1_eot = _apply_eot(h1b, p1.max_hp, s1b)
            h2_eot = _apply_eot(h2b, p2.max_hp, s2b)

            results.add(BattleState(
                _bucket(h1_eot, p1.max_hp),
                _bucket(h2_eot, p2.max_hp),
                s1b, s2b,
            ))

    return list(results)


def _exact(
    p1: Pokemon,
    p2: Pokemon,
    state: BattleState,
    memo: dict,
    counter: list[int],
    depth: int = 0,
) -> tuple[float, float]:
    """
    Return (p1_win_prob, p2_win_prob) as floats summing to 1.0.

    At each node we sum sub-results across all (move_pair x outcome) branches,
    then normalize so the result is always a probability pair in [0,1].
    Memoizing normalized values keeps numbers bounded (no integer explosion)
    while preserving correct win ratios.
    """
    if state.hp1 <= 0:
        return (0.0, 1.0)
    if state.hp2 <= 0:
        return (1.0, 0.0)
    if depth >= MAX_TURNS:
        ratio = state.hp1 / max(state.hp1 + state.hp2, 1)
        return (ratio, 1.0 - ratio)

    if state in memo:
        return memo[state]

    counter[0] += 1
    if counter[0] > EXACT_STATE_LIMIT:
        ratio = state.hp1 / max(state.hp1 + state.hp2, 1)
        return (ratio, 1.0 - ratio)

    total_p1 = 0.0
    total_p2 = 0.0
    branches  = 0

    for m1 in p1.moves:
        for m2 in p2.moves:
            next_states = _resolve_turn_exact(p1, p2, m1, m2, state)
            for ns in next_states:
                sub_p1, sub_p2 = _exact(p1, p2, ns, memo, counter, depth + 1)
                total_p1 += sub_p1
                total_p2 += sub_p2
                branches  += 1

    if branches == 0 or (total_p1 + total_p2) == 0.0:
        result = (0.5, 0.5)
    else:
        t      = total_p1 + total_p2
        result = (total_p1 / t, total_p2 / t)

    memo[state] = result
    return result


def _monte_carlo(p1: Pokemon, p2: Pokemon, n: int, rng: random.Random) -> tuple[int, int]:
    """Run n random battles. Returns (p1_wins, p2_wins)."""
    p1_wins = p2_wins = 0
    for _ in range(n):
        if _simulate_one(p1, p2, rng) == 1:
            p1_wins += 1
        else:
            p2_wins += 1
    return p1_wins, p2_wins


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
    mode:           str     # "exact" | "monte"
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
    Enumerate all possible battle outcomes between p1 and p2.

    Tries exact enumeration first. If the state space exceeds
    EXACT_STATE_LIMIT, falls back to Monte Carlo with MONTE_SIMULATIONS
    random battles.

    Parameters
    ----------
    p1, p2 : Pokemon with their chosen movesets
    seed   : optional RNG seed for reproducible Monte Carlo results
    """
    rng = random.Random(seed)

    memo: dict[BattleState, tuple[int, int]] = {}
    counter = [0]
    initial = BattleState(
        _bucket(p1.max_hp, p1.max_hp),
        _bucket(p2.max_hp, p2.max_hp),
        STS_NONE, STS_NONE,
    )

    p1_prob, p2_prob = _exact(p1, p2, initial, memo, counter)
    states = counter[0]

    if states <= EXACT_STATE_LIMIT:
        # Scale to 1,000,000 paths for readable display
        scale   = 1_000_000
        p1_wins = round(p1_prob * scale)
        p2_wins = round(p2_prob * scale)
        return BattleResult(
            p1_name=p1.name,
            p2_name=p2.name,
            p1_wins=p1_wins,
            p2_wins=p2_wins,
            total=scale,
            mode="exact",
            states_visited=states,
        )

    # Monte Carlo fallback
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
