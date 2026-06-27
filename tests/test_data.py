"""
tests/test_data.py
------------------
Tests for pokesim/data.py.

All tests run fully offline — no PokéAPI calls made.
Bundled data is used exclusively.
"""

import pytest
from pokesim.data import (
    type_multiplier,
    fetch_pokemon,
    fetch_move,
    real_hp,
    real_stat,
    list_bundled_pokemon,
    list_bundled_moves,
)


# ---------------------------------------------------------------------------
# type_multiplier
# ---------------------------------------------------------------------------

class TestTypeMultiplier:
    def test_neutral(self):
        assert type_multiplier("normal", ["normal"]) == 1.0

    def test_super_effective(self):
        assert type_multiplier("fire", ["grass"]) == 2.0

    def test_not_very_effective(self):
        assert type_multiplier("fire", ["water"]) == 0.5

    def test_immune(self):
        assert type_multiplier("ground", ["flying"]) == 0.0

    def test_dual_type_super(self):
        # Electric vs Water + Flying → 2 × 2 = 4
        assert type_multiplier("electric", ["water", "flying"]) == 4.0

    def test_dual_type_cancel(self):
        # Dragon vs Steel + Dragon → 0.5 × 2 = 1.0
        assert type_multiplier("dragon", ["steel", "dragon"]) == 1.0

    def test_dual_type_immune(self):
        # Dragon vs Fairy + Flying → 0 × 1 = 0
        assert type_multiplier("dragon", ["fairy", "flying"]) == 0.0

    def test_fairy_vs_dragon(self):
        assert type_multiplier("fairy", ["dragon"]) == 2.0

    def test_unknown_type_defaults_neutral(self):
        assert type_multiplier("???", ["normal"]) == 1.0


# ---------------------------------------------------------------------------
# fetch_pokemon  (bundled only — no network)
# ---------------------------------------------------------------------------

class TestFetchPokemon:
    def test_known_bundled(self):
        p = fetch_pokemon("rayquaza")
        assert p is not None
        assert p["name"] == "rayquaza"

    def test_types_present(self):
        p = fetch_pokemon("rayquaza")
        assert "dragon" in p["types"]
        assert "flying" in p["types"]

    def test_all_six_stats_present(self):
        p = fetch_pokemon("pikachu")
        for key in ("hp", "attack", "defense",
                    "special-attack", "special-defense", "speed"):
            assert key in p["stats"], f"Missing stat: {key}"

    def test_stats_are_positive(self):
        p = fetch_pokemon("mewtwo")
        for val in p["stats"].values():
            assert val > 0

    def test_moves_list_not_empty(self):
        p = fetch_pokemon("charizard")
        assert len(p["moves"]) > 0

    def test_case_insensitive(self):
        p = fetch_pokemon("PIKACHU")
        assert p is not None
        assert p["name"] == "pikachu"

    def test_unknown_returns_none(self):
        p = fetch_pokemon("notapokemon123")
        assert p is None

    def test_source_field_present(self):
        p = fetch_pokemon("gengar")
        assert "source" in p


# ---------------------------------------------------------------------------
# fetch_move  (bundled only — no network)
# ---------------------------------------------------------------------------

class TestFetchMove:
    def test_known_move(self):
        m = fetch_move("thunderbolt")
        assert m is not None
        assert m["name"] == "Thunderbolt"

    def test_power_is_int(self):
        m = fetch_move("earthquake")
        assert isinstance(m["power"], int)
        assert m["power"] == 100

    def test_status_move_zero_power(self):
        m = fetch_move("toxic")
        assert m["power"] == 0
        assert m["category"] == "status"

    def test_accuracy_field(self):
        m = fetch_move("hydro-pump")
        assert m["accuracy"] == 80

    def test_never_miss_accuracy_zero(self):
        # Aura Sphere never misses — encoded as accuracy = 0
        m = fetch_move("aura-sphere")
        assert m["accuracy"] == 0

    def test_priority_field_default(self):
        m = fetch_move("thunderbolt")
        assert m["priority"] == 0

    def test_priority_field_positive(self):
        m = fetch_move("quick-attack")
        assert m["priority"] == 1

    def test_effect_field_present(self):
        m = fetch_move("flamethrower")
        assert "effect" in m

    def test_type_field(self):
        m = fetch_move("surf")
        assert m["type"] == "water"

    def test_unknown_move_returns_none(self):
        m = fetch_move("notamove999")
        assert m is None

    def test_case_insensitive(self):
        m = fetch_move("EARTHQUAKE")
        assert m is not None


# ---------------------------------------------------------------------------
# real_hp / real_stat
# ---------------------------------------------------------------------------

class TestStatFormulas:
    def test_real_hp_pikachu(self):
        # Pikachu base HP = 35, level 50
        # (2×35 + 31) × 50 // 100 + 50 + 10 = 101×50//100 + 60 = 50 + 60 = 110
        assert real_hp(35, 50) == 110

    def test_real_hp_snorlax(self):
        # Snorlax base HP = 160
        # (2×160 + 31) × 50 // 100 + 50 + 10 = 351×50//100 + 60 = 175 + 60 = 235
        assert real_hp(160, 50) == 235

    def test_real_stat_basic(self):
        # base 100, level 50 → (200+31)×50//100 + 5 = 231×50//100 + 5 = 115 + 5 = 120
        assert real_stat(100, 50) == 120

    def test_real_hp_increases_with_level(self):
        assert real_hp(100, 100) > real_hp(100, 50)

    def test_real_stat_increases_with_level(self):
        assert real_stat(100, 100) > real_stat(100, 50)


# ---------------------------------------------------------------------------
# list helpers
# ---------------------------------------------------------------------------

class TestListHelpers:
    def test_bundled_pokemon_not_empty(self):
        names = list_bundled_pokemon()
        assert len(names) >= 15

    def test_bundled_pokemon_sorted(self):
        names = list_bundled_pokemon()
        assert names == sorted(names)

    def test_bundled_moves_not_empty(self):
        moves = list_bundled_moves()
        assert len(moves) >= 20

    def test_known_pokemon_in_list(self):
        assert "rayquaza" in list_bundled_pokemon()
        assert "pikachu" in list_bundled_pokemon()

    def test_known_move_in_list(self):
        assert "earthquake" in list_bundled_moves()
        assert "thunderbolt" in list_bundled_moves()
