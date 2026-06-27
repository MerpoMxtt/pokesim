"""
pokesim/data.py
---------------
Responsible for all data fetching and lookup:

  - fetch_pokemon(name)  →  raw Pokémon dict  (stats, types, move pool)
  - fetch_move(name)     →  move dict  (power, accuracy, pp, category, type)
  - type_multiplier(move_type, defender_types)  →  float  (0 / 0.5 / 1 / 2 / 4)
  - real_hp(base_hp, level)  →  actual HP at that level

Data sources (in priority order):
  1. PokéAPI  — https://pokeapi.co  (live fetch, any Pokémon)
  2. Bundled fallback  — 15 fan-favourites always available offline
"""

import json
import urllib.request
from functools import lru_cache

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

_BASE_URL = "https://pokeapi.co/api/v2"
_HEADERS = {"User-Agent": "pokesim/0.1.0 (github.com/MerpoMxtt/pokesim)"}
_API_AVAILABLE: bool | None = None   # cached after first probe


def _get(url: str) -> dict | None:
    """GET a JSON URL. Returns parsed dict or None on any error."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def api_available() -> bool:
    """
    Returns True if PokéAPI is reachable.
    Result is cached for the lifetime of the process.
    """
    global _API_AVAILABLE
    if _API_AVAILABLE is None:
        _API_AVAILABLE = _get(f"{_BASE_URL}/pokemon/pikachu") is not None
    return _API_AVAILABLE


# ---------------------------------------------------------------------------
# TYPE CHART  (Gen 6+, includes Fairy)
# ---------------------------------------------------------------------------
# Layout:  TYPE_CHART[attacking_type][defending_type] = multiplier
# Missing entries default to 1.0 (neutral).

TYPE_CHART: dict[str, dict[str, float]] = {
    "normal":   {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire":     {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0,
                 "bug": 2.0, "rock": 0.5, "dragon": 0.5, "steel": 2.0},
    "water":    {"fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0,
                 "rock": 2.0, "dragon": 0.5},
    "electric": {"water": 2.0, "electric": 0.5, "grass": 0.5, "ground": 0.0,
                 "flying": 2.0, "dragon": 0.5},
    "grass":    {"fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5,
                 "ground": 2.0, "flying": 0.5, "bug": 0.5, "rock": 2.0,
                 "dragon": 0.5, "steel": 0.5},
    "ice":      {"water": 0.5, "grass": 2.0, "ice": 0.5, "ground": 2.0,
                 "flying": 2.0, "dragon": 2.0, "steel": 0.5},
    "fighting": {"normal": 2.0, "ice": 2.0, "poison": 0.5, "flying": 0.5,
                 "psychic": 0.5, "bug": 0.5, "rock": 2.0, "ghost": 0.0,
                 "dark": 2.0, "steel": 2.0, "fairy": 0.5},
    "poison":   {"grass": 2.0, "poison": 0.5, "ground": 0.5, "rock": 0.5,
                 "ghost": 0.5, "steel": 0.0, "fairy": 2.0},
    "ground":   {"fire": 2.0, "electric": 2.0, "grass": 0.5, "poison": 2.0,
                 "flying": 0.0, "bug": 0.5, "rock": 2.0, "steel": 2.0},
    "flying":   {"electric": 0.5, "grass": 2.0, "fighting": 2.0, "bug": 2.0,
                 "rock": 0.5, "steel": 0.5},
    "psychic":  {"fighting": 2.0, "poison": 2.0, "psychic": 0.5,
                 "dark": 0.0, "steel": 0.5},
    "bug":      {"fire": 0.5, "grass": 2.0, "fighting": 0.5, "flying": 0.5,
                 "psychic": 2.0, "ghost": 0.5, "dark": 2.0, "steel": 0.5,
                 "fairy": 0.5},
    "rock":     {"fire": 2.0, "ice": 2.0, "fighting": 0.5, "ground": 0.5,
                 "flying": 2.0, "bug": 2.0, "steel": 0.5},
    "ghost":    {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon":   {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark":     {"fighting": 0.5, "psychic": 2.0, "ghost": 2.0,
                 "dark": 0.5, "fairy": 0.5},
    "steel":    {"fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2.0,
                 "rock": 2.0, "steel": 0.5, "fairy": 2.0, "normal": 0.5,
                 "grass": 0.5, "psychic": 0.5, "bug": 0.5,
                 "dragon": 0.5, "dark": 0.5},
    "fairy":    {"fighting": 2.0, "poison": 0.5, "bug": 0.5,
                 "dragon": 2.0, "dark": 2.0, "steel": 0.5},
}


def type_multiplier(move_type: str, defender_types: list[str]) -> float:
    """
    Calculate the combined type effectiveness multiplier.

    Examples
    --------
    >>> type_multiplier("dragon", ["steel", "dragon"])
    1.0        # 0.5 × 2.0
    >>> type_multiplier("ground", ["flying"])
    0.0        # immune
    >>> type_multiplier("electric", ["water", "flying"])
    4.0        # 2.0 × 2.0
    """
    mult = 1.0
    chart = TYPE_CHART.get(move_type, {})
    for defending_type in defender_types:
        mult *= chart.get(defending_type, 1.0)
    return mult


# ---------------------------------------------------------------------------
# BUNDLED FALLBACK DATA
# 15 Pokémon with real base stats + a curated legal moveset each.
# Used when PokéAPI is unreachable.
# ---------------------------------------------------------------------------

_BUNDLED_POKEMON: dict[str, dict] = {
    "pikachu": {
        "name": "pikachu",
        "types": ["electric"],
        "stats": {
            "hp": 35, "attack": 55, "defense": 40,
            "special-attack": 50, "special-defense": 50, "speed": 90,
        },
        "moves": ["thunderbolt", "quick-attack", "iron-tail", "volt-tackle"],
    },
    "charizard": {
        "name": "charizard",
        "types": ["fire", "flying"],
        "stats": {
            "hp": 78, "attack": 84, "defense": 78,
            "special-attack": 109, "special-defense": 85, "speed": 100,
        },
        "moves": ["flamethrower", "air-slash", "dragon-claw", "fire-blast"],
    },
    "blastoise": {
        "name": "blastoise",
        "types": ["water"],
        "stats": {
            "hp": 79, "attack": 83, "defense": 100,
            "special-attack": 85, "special-defense": 105, "speed": 78,
        },
        "moves": ["surf", "ice-beam", "skull-bash", "hydro-pump"],
    },
    "venusaur": {
        "name": "venusaur",
        "types": ["grass", "poison"],
        "stats": {
            "hp": 80, "attack": 82, "defense": 83,
            "special-attack": 100, "special-defense": 100, "speed": 80,
        },
        "moves": ["solar-beam", "sludge-bomb", "earthquake", "synthesis"],
    },
    "mewtwo": {
        "name": "mewtwo",
        "types": ["psychic"],
        "stats": {
            "hp": 106, "attack": 110, "defense": 90,
            "special-attack": 154, "special-defense": 90, "speed": 130,
        },
        "moves": ["psychic", "ice-beam", "thunderbolt", "aura-sphere"],
    },
    "rayquaza": {
        "name": "rayquaza",
        "types": ["dragon", "flying"],
        "stats": {
            "hp": 105, "attack": 150, "defense": 90,
            "special-attack": 150, "special-defense": 90, "speed": 95,
        },
        "moves": ["outrage", "air-slash", "dragon-pulse", "extreme-speed"],
    },
    "dialga": {
        "name": "dialga",
        "types": ["steel", "dragon"],
        "stats": {
            "hp": 100, "attack": 120, "defense": 120,
            "special-attack": 150, "special-defense": 100, "speed": 90,
        },
        "moves": ["draco-meteor", "flash-cannon", "thunder", "aura-sphere"],
    },
    "garchomp": {
        "name": "garchomp",
        "types": ["dragon", "ground"],
        "stats": {
            "hp": 108, "attack": 130, "defense": 95,
            "special-attack": 80, "special-defense": 85, "speed": 102,
        },
        "moves": ["outrage", "earthquake", "stone-edge", "swords-dance"],
    },
    "gengar": {
        "name": "gengar",
        "types": ["ghost", "poison"],
        "stats": {
            "hp": 60, "attack": 65, "defense": 60,
            "special-attack": 130, "special-defense": 75, "speed": 110,
        },
        "moves": ["shadow-ball", "sludge-bomb", "thunderbolt", "focus-blast"],
    },
    "tyranitar": {
        "name": "tyranitar",
        "types": ["rock", "dark"],
        "stats": {
            "hp": 100, "attack": 134, "defense": 110,
            "special-attack": 95, "special-defense": 100, "speed": 61,
        },
        "moves": ["stone-edge", "crunch", "earthquake", "ice-beam"],
    },
    "dragonite": {
        "name": "dragonite",
        "types": ["dragon", "flying"],
        "stats": {
            "hp": 91, "attack": 134, "defense": 95,
            "special-attack": 100, "special-defense": 100, "speed": 80,
        },
        "moves": ["outrage", "extreme-speed", "fire-blast", "thunder-wave"],
    },
    "lucario": {
        "name": "lucario",
        "types": ["fighting", "steel"],
        "stats": {
            "hp": 70, "attack": 110, "defense": 70,
            "special-attack": 115, "special-defense": 70, "speed": 90,
        },
        "moves": ["aura-sphere", "close-combat", "iron-tail", "psychic"],
    },
    "snorlax": {
        "name": "snorlax",
        "types": ["normal"],
        "stats": {
            "hp": 160, "attack": 110, "defense": 65,
            "special-attack": 65, "special-defense": 110, "speed": 30,
        },
        "moves": ["body-slam", "crunch", "earthquake", "rest"],
    },
    "togekiss": {
        "name": "togekiss",
        "types": ["fairy", "flying"],
        "stats": {
            "hp": 85, "attack": 50, "defense": 95,
            "special-attack": 120, "special-defense": 115, "speed": 80,
        },
        "moves": ["air-slash", "dazzling-gleam", "thunder-wave", "aura-sphere"],
    },
    "umbreon": {
        "name": "umbreon",
        "types": ["dark"],
        "stats": {
            "hp": 95, "attack": 65, "defense": 110,
            "special-attack": 60, "special-defense": 130, "speed": 65,
        },
        "moves": ["foul-play", "moonlight", "toxic", "protect"],
    },
}

# Moves we have full data for — used both as fallback and
# to filter the raw API learnset down to usable entries.
_BUNDLED_MOVES: dict[str, dict] = {
    "thunderbolt":    {"name": "Thunderbolt",    "power": 90,  "accuracy": 100, "pp": 15, "category": "special",  "type": "electric"},
    "quick-attack":   {"name": "Quick Attack",   "power": 40,  "accuracy": 100, "pp": 30, "category": "physical", "type": "normal",   "priority": 1},
    "iron-tail":      {"name": "Iron Tail",       "power": 100, "accuracy": 75,  "pp": 15, "category": "physical", "type": "steel"},
    "volt-tackle":    {"name": "Volt Tackle",     "power": 120, "accuracy": 100, "pp": 15, "category": "physical", "type": "electric", "effect": "recoil_33"},
    "flamethrower":   {"name": "Flamethrower",   "power": 90,  "accuracy": 100, "pp": 15, "category": "special",  "type": "fire",     "effect": "burn_10"},
    "air-slash":      {"name": "Air Slash",       "power": 75,  "accuracy": 95,  "pp": 15, "category": "special",  "type": "flying",   "effect": "flinch_30"},
    "dragon-claw":    {"name": "Dragon Claw",     "power": 80,  "accuracy": 100, "pp": 15, "category": "physical", "type": "dragon"},
    "fire-blast":     {"name": "Fire Blast",      "power": 110, "accuracy": 85,  "pp": 5,  "category": "special",  "type": "fire",     "effect": "burn_10"},
    "surf":           {"name": "Surf",             "power": 90,  "accuracy": 100, "pp": 15, "category": "special",  "type": "water"},
    "ice-beam":       {"name": "Ice Beam",         "power": 90,  "accuracy": 100, "pp": 10, "category": "special",  "type": "ice",      "effect": "freeze_10"},
    "skull-bash":     {"name": "Skull Bash",       "power": 130, "accuracy": 100, "pp": 10, "category": "physical", "type": "normal"},
    "hydro-pump":     {"name": "Hydro Pump",       "power": 110, "accuracy": 80,  "pp": 5,  "category": "special",  "type": "water"},
    "solar-beam":     {"name": "Solar Beam",       "power": 120, "accuracy": 100, "pp": 10, "category": "special",  "type": "grass"},
    "sludge-bomb":    {"name": "Sludge Bomb",      "power": 90,  "accuracy": 100, "pp": 10, "category": "special",  "type": "poison",   "effect": "poison_30"},
    "earthquake":     {"name": "Earthquake",       "power": 100, "accuracy": 100, "pp": 10, "category": "physical", "type": "ground"},
    "synthesis":      {"name": "Synthesis",        "power": 0,   "accuracy": 100, "pp": 5,  "category": "status",   "type": "grass",    "effect": "heal_50"},
    "psychic":        {"name": "Psychic",           "power": 90,  "accuracy": 100, "pp": 10, "category": "special",  "type": "psychic",  "effect": "spd_down_10"},
    "aura-sphere":    {"name": "Aura Sphere",       "power": 80,  "accuracy": 0,   "pp": 20, "category": "special",  "type": "fighting"},
    "outrage":        {"name": "Outrage",           "power": 120, "accuracy": 100, "pp": 10, "category": "physical", "type": "dragon"},
    "dragon-pulse":   {"name": "Dragon Pulse",      "power": 85,  "accuracy": 100, "pp": 10, "category": "special",  "type": "dragon"},
    "extreme-speed":  {"name": "Extreme Speed",     "power": 80,  "accuracy": 100, "pp": 5,  "category": "physical", "type": "normal",   "priority": 2},
    "draco-meteor":   {"name": "Draco Meteor",      "power": 130, "accuracy": 90,  "pp": 5,  "category": "special",  "type": "dragon",   "effect": "spa_down_2"},
    "flash-cannon":   {"name": "Flash Cannon",      "power": 80,  "accuracy": 100, "pp": 10, "category": "special",  "type": "steel",    "effect": "spd_down_10"},
    "thunder":        {"name": "Thunder",            "power": 110, "accuracy": 70,  "pp": 10, "category": "special",  "type": "electric", "effect": "paralysis_30"},
    "stone-edge":     {"name": "Stone Edge",         "power": 100, "accuracy": 80,  "pp": 5,  "category": "physical", "type": "rock",     "effect": "crit_high"},
    "crunch":         {"name": "Crunch",             "power": 80,  "accuracy": 100, "pp": 15, "category": "physical", "type": "dark",     "effect": "def_down_20"},
    "shadow-ball":    {"name": "Shadow Ball",        "power": 80,  "accuracy": 100, "pp": 15, "category": "special",  "type": "ghost",    "effect": "spd_down_20"},
    "focus-blast":    {"name": "Focus Blast",        "power": 120, "accuracy": 70,  "pp": 5,  "category": "special",  "type": "fighting"},
    "close-combat":   {"name": "Close Combat",       "power": 120, "accuracy": 100, "pp": 5,  "category": "physical", "type": "fighting", "effect": "def_spd_down_self"},
    "body-slam":      {"name": "Body Slam",          "power": 85,  "accuracy": 100, "pp": 15, "category": "physical", "type": "normal",   "effect": "paralysis_30"},
    "rest":           {"name": "Rest",               "power": 0,   "accuracy": 100, "pp": 5,  "category": "status",   "type": "psychic",  "effect": "full_heal_sleep"},
    "toxic":          {"name": "Toxic",              "power": 0,   "accuracy": 90,  "pp": 10, "category": "status",   "type": "poison",   "effect": "bad_poison"},
    "protect":        {"name": "Protect",            "power": 0,   "accuracy": 100, "pp": 10, "category": "status",   "type": "normal",   "effect": "protect"},
    "thunder-wave":   {"name": "Thunder Wave",       "power": 0,   "accuracy": 90,  "pp": 20, "category": "status",   "type": "electric", "effect": "paralysis_100"},
    "dazzling-gleam": {"name": "Dazzling Gleam",     "power": 80,  "accuracy": 100, "pp": 10, "category": "special",  "type": "fairy"},
    "moonlight":      {"name": "Moonlight",          "power": 0,   "accuracy": 100, "pp": 5,  "category": "status",   "type": "fairy",    "effect": "heal_50"},
    "foul-play":      {"name": "Foul Play",          "power": 95,  "accuracy": 100, "pp": 15, "category": "physical", "type": "dark"},
    "swords-dance":   {"name": "Swords Dance",       "power": 0,   "accuracy": 100, "pp": 20, "category": "status",   "type": "normal",   "effect": "atk_up_2"},
    "iron-tail":      {"name": "Iron Tail",          "power": 100, "accuracy": 75,  "pp": 15, "category": "physical", "type": "steel"},
}


# ---------------------------------------------------------------------------
# PUBLIC FETCH FUNCTIONS
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def fetch_pokemon(name: str) -> dict | None:
    """
    Return a Pokémon data dict for the given name.

    Dict shape
    ----------
    {
        "name":   str,
        "types":  list[str],          # e.g. ["dragon", "flying"]
        "stats":  {
            "hp": int, "attack": int, "defense": int,
            "special-attack": int, "special-defense": int, "speed": int
        },
        "moves":  list[str],          # move slugs we have data for
        "source": "api" | "bundled",
    }

    Returns None if the Pokémon is not found anywhere.
    """
    name = name.lower().strip()

    # ── Try live API first ──────────────────────────────────────────────────
    if api_available():
        raw = _get(f"{_BASE_URL}/pokemon/{name}")
        if raw:
            stats = {s["stat"]["name"]: s["base_stat"] for s in raw["stats"]}
            types = [t["type"]["name"] for t in raw["types"]]

            # Filter the full learnset down to moves we have data for
            all_moves = [m["move"]["name"] for m in raw["moves"]]
            known_moves = [m for m in all_moves if m in _BUNDLED_MOVES]

            return {
                "name":   name,
                "types":  types,
                "stats":  stats,
                "moves":  known_moves,
                "source": "api",
            }

    # ── Bundled fallback ────────────────────────────────────────────────────
    if name in _BUNDLED_POKEMON:
        entry = dict(_BUNDLED_POKEMON[name])
        entry["source"] = "bundled"
        return entry

    return None


@lru_cache(maxsize=512)
def fetch_move(slug: str) -> dict | None:
    """
    Return a move data dict for the given slug (e.g. "thunderbolt").

    Dict shape
    ----------
    {
        "name":     str,      # display name, e.g. "Thunderbolt"
        "power":    int,      # 0 for status moves
        "accuracy": int,      # 0 means never misses (e.g. Aura Sphere)
        "pp":       int,
        "category": str,      # "physical" | "special" | "status"
        "type":     str,      # e.g. "electric"
        "effect":   str|None, # effect code used by the engine
        "priority": int,      # default 0; positive = moves first
    }

    Returns None if the move is unknown.
    """
    slug = slug.lower().strip()

    # Bundled data is always checked first — it has richer effect metadata
    if slug in _BUNDLED_MOVES:
        entry = dict(_BUNDLED_MOVES[slug])
        entry.setdefault("effect", None)
        entry.setdefault("priority", 0)
        return entry

    # Fall back to live API for any move not in our table
    if api_available():
        raw = _get(f"{_BASE_URL}/move/{slug}")
        if raw:
            return {
                "name":     raw["name"].replace("-", " ").title(),
                "power":    raw.get("power") or 0,
                "accuracy": raw.get("accuracy") or 0,
                "pp":       raw.get("pp", 10),
                "category": raw.get("damage_class", {}).get("name", "physical"),
                "type":     raw.get("type", {}).get("name", "normal"),
                "effect":   None,   # unknown effects treated as none
                "priority": raw.get("priority", 0),
            }

    return None


# ---------------------------------------------------------------------------
# STAT HELPERS
# ---------------------------------------------------------------------------

def real_hp(base_hp: int, level: int = 50) -> int:
    """
    Calculate actual HP from base stat at a given level.
    Uses the standard formula with perfect IVs (31) and no EVs.

        HP = floor((2 × base + 31) × level / 100) + level + 10
    """
    return (2 * base_hp + 31) * level // 100 + level + 10


def real_stat(base: int, level: int = 50) -> int:
    """
    Calculate an actual non-HP stat (Attack, Defense, etc.)
    with perfect IVs and no EVs, neutral nature.

        Stat = floor((2 × base + 31) × level / 100 + 5)
    """
    return (2 * base + 31) * level // 100 + 5


# ---------------------------------------------------------------------------
# CONVENIENCE
# ---------------------------------------------------------------------------

def list_bundled_pokemon() -> list[str]:
    """Return sorted list of bundled Pokémon names."""
    return sorted(_BUNDLED_POKEMON.keys())


def list_bundled_moves() -> list[str]:
    """Return sorted list of move slugs with bundled data."""
    return sorted(_BUNDLED_MOVES.keys())
