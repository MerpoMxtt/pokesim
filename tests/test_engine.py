"""
tests/test_engine.py
--------------------
Tests for pokesim/engine.py.

All tests are deterministic — no randomness or network calls.
"""

import pytest
from pokesim.engine import (
    Move, Pokemon, BattleResult,
    _calc_damage, _goes_first, _apply_eot, _bucket,
    run, LEVEL, BUCKET_COUNT,
)
from pokesim.data import fetch_pokemon, fetch_move


# ---------------------------------------------------------------------------
# Helpers — build test Pokémon from bundled data
# ---------------------------------------------------------------------------

def make_move(slug: str) -> Move:
    d = fetch_move(slug)
    assert d is not None, f"Move not found: {slug}"
    return Move.from_dict(slug, d)


def make_pokemon(name: str, move_slugs: list[str]) -> Pokemon:
    pdata = fetch_pokemon(name)
    assert pdata is not None, f"Pokémon not found: {name}"
    moves = [make_move(s) for s in move_slugs]
    return Pokemon.from_dict(pdata, moves)


# ---------------------------------------------------------------------------
# Move.from_dict
# ---------------------------------------------------------------------------

class TestMove:
    def test_basic_fields(self):
        m = make_move("thunderbolt")
        assert m.name == "Thunderbolt"
        assert m.power == 90
        assert m.type == "electric"
        assert m.category == "special"

    def test_priority_default_zero(self):
        m = make_move("thunderbolt")
        assert m.priority == 0

    def test_priority_quick_attack(self):
        m = make_move("quick-attack")
        assert m.priority == 1

    def test_status_move_zero_power(self):
        m = make_move("toxic")
        assert m.power == 0
        assert m.category == "status"


# ---------------------------------------------------------------------------
# Pokemon.from_dict
# ---------------------------------------------------------------------------

class TestPokemon:
    def test_name(self):
        p = make_pokemon("pikachu", ["thunderbolt"])
        assert p.name == "pikachu"

    def test_types(self):
        p = make_pokemon("rayquaza", ["outrage"])
        assert "dragon" in p.types
        assert "flying" in p.types

    def test_max_hp_positive(self):
        p = make_pokemon("snorlax", ["body-slam"])
        assert p.max_hp > 0

    def test_snorlax_has_high_hp(self):
        snorlax = make_pokemon("snorlax", ["body-slam"])
        pikachu  = make_pokemon("pikachu",  ["thunderbolt"])
        assert snorlax.max_hp > pikachu.max_hp

    def test_stats_all_positive(self):
        p = make_pokemon("mewtwo", ["psychic"])
        for attr in ("attack", "defense", "sp_atk", "sp_def", "speed"):
            assert getattr(p, attr) > 0


# ---------------------------------------------------------------------------
# _calc_damage
# ---------------------------------------------------------------------------

class TestCalcDamage:
    def setup_method(self):
        self.rayquaza = make_pokemon("rayquaza", ["outrage", "air-slash", "dragon-pulse", "extreme-speed"])
        self.dialga   = make_pokemon("dialga",   ["draco-meteor", "flash-cannon", "thunder", "aura-sphere"])
        self.pikachu  = make_pokemon("pikachu",  ["thunderbolt", "quick-attack", "iron-tail", "volt-tackle"])
        self.snorlax  = make_pokemon("snorlax",  ["body-slam", "crunch", "earthquake", "rest"])

    def test_damage_positive_for_hitting_move(self):
        move = make_move("outrage")
        dmg = _calc_damage(self.rayquaza, self.dialga, move)
        assert dmg > 0

    def test_immune_returns_zero(self):
        # Dragon move vs Fairy type → immune
        togekiss = make_pokemon("togekiss", ["air-slash"])
        move = make_move("outrage")   # Dragon type
        dmg = _calc_damage(self.rayquaza, togekiss, move)
        assert dmg == 0

    def test_stab_increases_damage(self):
        # Thunderbolt (Electric) used by Pikachu (Electric type) → STAB
        move_stab    = make_move("thunderbolt")
        move_no_stab = make_move("surf")        # Water — not Pikachu's type
        dmg_stab    = _calc_damage(self.pikachu, self.snorlax, move_stab)
        dmg_no_stab = _calc_damage(self.pikachu, self.snorlax, move_no_stab)
        # Both same base power (90), STAB version should be higher
        assert dmg_stab > dmg_no_stab

    def test_super_effective_doubles_damage(self):
        # Electric vs Water/Flying Pokémon (if we had one) — use type directly
        # Ground vs Electric type → immune, so test fire vs grass instead
        venusaur = make_pokemon("venusaur", ["solar-beam"])
        fire_move = make_move("flamethrower")
        charizard = make_pokemon("charizard", ["flamethrower"])
        dmg_se  = _calc_damage(charizard, venusaur, fire_move)   # 2× effectiveness
        dmg_neu = _calc_damage(charizard, self.snorlax, fire_move) # 1× normal
        assert dmg_se > dmg_neu

    def test_burn_halves_physical_damage(self):
        move = make_move("body-slam")
        dmg_normal = _calc_damage(self.snorlax, self.pikachu, move, attacker_status=0)
        dmg_burned = _calc_damage(self.snorlax, self.pikachu, move, attacker_status=1)
        assert dmg_burned < dmg_normal

    def test_burn_does_not_affect_special(self):
        move = make_move("thunderbolt")
        dmg_normal = _calc_damage(self.pikachu, self.snorlax, move, attacker_status=0)
        dmg_burned = _calc_damage(self.pikachu, self.snorlax, move, attacker_status=1)
        assert dmg_burned == dmg_normal

    def test_crit_increases_damage(self):
        move = make_move("outrage")
        dmg_no_crit = _calc_damage(self.rayquaza, self.dialga, move, critical=False)
        dmg_crit    = _calc_damage(self.rayquaza, self.dialga, move, critical=True)
        assert dmg_crit > dmg_no_crit

    def test_status_move_deals_no_damage(self):
        move = make_move("toxic")
        dmg = _calc_damage(self.pikachu, self.snorlax, move)
        assert dmg == 0

    def test_higher_roll_deals_more_damage(self):
        move = make_move("outrage")
        dmg_low  = _calc_damage(self.rayquaza, self.dialga, move, roll=0.85)
        dmg_high = _calc_damage(self.rayquaza, self.dialga, move, roll=1.0)
        assert dmg_high >= dmg_low


# ---------------------------------------------------------------------------
# _goes_first
# ---------------------------------------------------------------------------

class TestGoesFirst:
    def setup_method(self):
        self.fast   = make_pokemon("mewtwo",   ["psychic"])     # speed 130
        self.slow   = make_pokemon("snorlax",  ["body-slam"])   # speed 30
        self.normal = make_move("psychic")
        self.quick  = make_move("quick-attack")  # priority 1
        self.extreme = make_move("extreme-speed") # priority 2

    def test_faster_goes_first(self):
        assert _goes_first(self.fast, self.slow, self.normal, self.normal, 0, 0) is True
        assert _goes_first(self.slow, self.fast, self.normal, self.normal, 0, 0) is False

    def test_priority_overrides_speed(self):
        # Slow uses Quick Attack (priority 1) vs fast using normal move
        assert _goes_first(self.slow, self.fast, self.quick, self.normal, 0, 0) is True

    def test_higher_priority_beats_lower(self):
        # Extreme Speed (2) vs Quick Attack (1) — extreme goes first
        assert _goes_first(self.slow, self.fast, self.extreme, self.quick, 0, 0) is True


# ---------------------------------------------------------------------------
# _apply_eot
# ---------------------------------------------------------------------------

class TestApplyEot:
    def test_no_status_no_damage(self):
        assert _apply_eot(100, 200, 0) == 100

    def test_burn_deals_damage(self):
        hp = _apply_eot(100, 160, 1)   # burn (status=1)
        assert hp < 100

    def test_poison_deals_damage(self):
        hp = _apply_eot(100, 160, 3)   # poison (status=3)
        assert hp < 100

    def test_hp_does_not_go_below_zero(self):
        assert _apply_eot(1, 160, 1) == 0


# ---------------------------------------------------------------------------
# _bucket
# ---------------------------------------------------------------------------

class TestBucket:
    def test_full_hp_gives_max_bucket(self):
        assert _bucket(200, 200) == BUCKET_COUNT

    def test_zero_hp_gives_zero(self):
        assert _bucket(0, 200) == 0

    def test_half_hp_midpoint(self):
        b = _bucket(100, 200)
        assert BUCKET_COUNT // 2 - 2 <= b <= BUCKET_COUNT // 2 + 2

    def test_negative_hp_gives_zero(self):
        assert _bucket(-5, 200) == 0


# ---------------------------------------------------------------------------
# run()  — integration tests
# ---------------------------------------------------------------------------

class TestRun:
    def setup_method(self):
        self.rayquaza = make_pokemon(
            "rayquaza",
            ["outrage", "air-slash", "dragon-pulse", "extreme-speed"]
        )
        self.dialga = make_pokemon(
            "dialga",
            ["draco-meteor", "flash-cannon", "thunder", "aura-sphere"]
        )
        self.pikachu = make_pokemon(
            "pikachu",
            ["thunderbolt", "quick-attack", "iron-tail", "volt-tackle"]
        )
        self.snorlax = make_pokemon(
            "snorlax",
            ["body-slam", "crunch", "earthquake", "rest"]
        )

    def test_returns_battle_result(self):
        result = run(self.pikachu, self.snorlax, seed=42)
        assert isinstance(result, BattleResult)

    def test_total_is_sum_of_wins(self):
        result = run(self.pikachu, self.snorlax, seed=42)
        assert result.total == result.p1_wins + result.p2_wins

    def test_percentages_sum_to_100(self):
        result = run(self.pikachu, self.snorlax, seed=42)
        assert abs(result.p1_pct + result.p2_pct - 100.0) < 0.01

    def test_mode_is_valid(self):
        result = run(self.pikachu, self.snorlax, seed=42)
        assert result.mode in ("exact", "monte")

    def test_winner_field(self):
        result = run(self.pikachu, self.snorlax, seed=42)
        assert result.winner in (self.pikachu.name, self.snorlax.name, "draw")

    def test_pokemon_names_in_result(self):
        result = run(self.rayquaza, self.dialga, seed=0)
        assert result.p1_name == "rayquaza"
        assert result.p2_name == "dialga"

    def test_high_stat_pokemon_wins_more(self):
        # Mewtwo (BST 680) should beat Pikachu (BST 320) most of the time
        mewtwo  = make_pokemon("mewtwo",  ["psychic", "ice-beam", "thunderbolt", "aura-sphere"])
        pikachu = make_pokemon("pikachu", ["thunderbolt", "quick-attack", "iron-tail", "volt-tackle"])
        result = run(mewtwo, pikachu, seed=1)
        assert result.p1_pct > result.p2_pct   # Mewtwo should win more

    def test_seed_gives_reproducible_results(self):
        r1 = run(self.rayquaza, self.dialga, seed=99)
        r2 = run(self.rayquaza, self.dialga, seed=99)
        assert r1.p1_wins == r2.p1_wins
        assert r1.p2_wins == r2.p2_wins

    def test_p1_p2_names_correct(self):
        result = run(self.pikachu, self.snorlax, seed=0)
        assert result.p1_name == "pikachu"
        assert result.p2_name == "snorlax"