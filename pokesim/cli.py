# -*- coding: utf-8 -*-
"""
pokesim/cli.py
--------------
Terminal interface for pokesim.

Flow
----
1. Main menu  (full sim / stat-only / quit)
2. Pick Pokemon A  ->  swap moves?  ->  go through each move slot
3. Pick Pokemon B  ->  swap moves?  ->  go through each move slot
4. Run simulation with spinner
5. Show results
6. Both Pokemon reset to original moves  ->  loop back to menu
"""

import sys
import time
import threading

from pokesim.data import (
    fetch_pokemon,
    fetch_move,
    fetch_all_pokemon_names,
    list_bundled_pokemon,
    api_available,
    type_multiplier,
)
from pokesim.engine import Pokemon, Move, BattleResult, run

# ---------------------------------------------------------------------------
# ANSI color helpers  (graceful fallback on terminals that don't support it)
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_COLOR = _supports_color()

R  = "\033[91m" if _COLOR else ""   # red
G  = "\033[92m" if _COLOR else ""   # green
Y  = "\033[93m" if _COLOR else ""   # yellow
B  = "\033[94m" if _COLOR else ""   # blue
M  = "\033[95m" if _COLOR else ""   # magenta
C  = "\033[96m" if _COLOR else ""   # cyan
W  = "\033[97m" if _COLOR else ""   # white
DIM = "\033[2m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""
RST  = "\033[0m" if _COLOR else ""

TYPE_COLORS = {
    "fire": "\033[38;5;202m", "water": "\033[38;5;33m",
    "grass": "\033[38;5;34m", "electric": "\033[38;5;220m",
    "psychic": "\033[38;5;201m", "ice": "\033[38;5;51m",
    "dragon": "\033[38;5;57m", "dark": "\033[38;5;239m",
    "fairy": "\033[38;5;213m", "fighting": "\033[38;5;166m",
    "poison": "\033[38;5;128m", "ground": "\033[38;5;136m",
    "rock": "\033[38;5;100m", "bug": "\033[38;5;70m",
    "ghost": "\033[38;5;99m", "steel": "\033[38;5;250m",
    "normal": "\033[38;5;250m", "flying": "\033[38;5;111m",
}

def type_tag(t: str) -> str:
    col = TYPE_COLORS.get(t, W) if _COLOR else ""
    return f"{col}{BOLD}[{t.upper()}]{RST}"

def col(color: str, text: str) -> str:
    return f"{color}{text}{RST}"

# ---------------------------------------------------------------------------
# Basic I/O helpers
# ---------------------------------------------------------------------------

def _divider(char: str = "-", width: int = 56):
    print(col(DIM, char * width))

def _header(title: str, width: int = 56):
    pad = max(0, width - len(title) - 4)
    left = pad // 2
    right = pad - left
    print(col(M + BOLD, "=" * left + "  " + title + "  " + "=" * right))

def _ask(prompt: str) -> str:
    try:
        return input(col(G + BOLD, f"\n  > {prompt}: ") + RST).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

def _pick_number(prompt: str, lo: int, hi: int) -> int:
    while True:
        raw = _ask(prompt)
        if raw.isdigit() and lo <= int(raw) <= hi:
            return int(raw)
        print(col(R, f"  Please enter a number between {lo} and {hi}."))

def _confirm(prompt: str) -> bool:
    raw = _ask(f"{prompt} (y/n)").lower()
    return raw in ("y", "yes")

def _info(msg: str):
    print(col(C, f"\n  {msg}"))

def _ok(msg: str):
    print(col(G, f"  + {msg}"))

def _err(msg: str):
    print(col(R, f"  ! {msg}"))

# ---------------------------------------------------------------------------
# Spinner  (runs in a background thread while simulation computes)
# ---------------------------------------------------------------------------

def _spinner(label: str, stop_flag: list[bool]):
    frames = ["|", "/", "-", "\\"]
    i = 0
    while not stop_flag[0]:
        frame = frames[i % len(frames)]
        sys.stdout.write(col(C, f"\r  {frame}  {label} ") + RST)
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    sys.stdout.write("\r" + " " * (len(label) + 10) + "\r")
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# HP bar
# ---------------------------------------------------------------------------

def _hp_bar(current: int, maximum: int, width: int = 24) -> str:
    if maximum == 0:
        return ""
    filled = int(width * current / maximum)
    empty  = width - filled
    pct    = current / maximum
    bar_col = G if pct > 0.5 else Y if pct > 0.25 else R
    bar = col(bar_col, "|" * filled) + col(DIM, "." * empty)
    return f"[{bar}]"

# ---------------------------------------------------------------------------
# Pokemon stat card
# ---------------------------------------------------------------------------

def _print_pokemon_card(name: str, pdata: dict, moves: list[dict]):
    types_str = "  ".join(type_tag(t) for t in pdata["types"])
    source    = pdata.get("source", "bundled")
    s         = pdata["stats"]

    print()
    print(f"  {col(W + BOLD, name.upper())}  {types_str}  {col(DIM, f'({source})')}")
    print()

    stat_rows = [
        ("HP",     s["hp"],              G),
        ("ATK",    s["attack"],          R),
        ("DEF",    s["defense"],         Y),
        ("SP.ATK", s["special-attack"],  B),
        ("SP.DEF", s["special-defense"], C),
        ("SPE",    s["speed"],           M),
    ]
    for label, val, c_ in stat_rows:
        bar = col(c_, "|" * min(30, val // 5))
        print(f"  {col(DIM, label.ljust(7))} {bar} {col(W, str(val))}")

    print()
    print(f"  {col(BOLD, 'Moves:')}")
    for i, m in enumerate(moves):
        mdata = fetch_move(m) if isinstance(m, str) else m
        if mdata:
            _print_move_row(i + 1, mdata)

# ---------------------------------------------------------------------------
# Move row
# ---------------------------------------------------------------------------

def _print_move_row(idx: int | None, mdata: dict):
    cat_col  = {"physical": R, "special": B, "status": G}.get(mdata.get("category", ""), W)
    mtype    = mdata.get("type", "normal")
    power    = str(mdata["power"]) if mdata.get("power") else "--"
    accuracy = (str(mdata["accuracy"]) + "%") if mdata.get("accuracy") else "always"
    cat      = mdata.get("category", "???")

    prefix = col(Y, f"  [{idx}]") if idx is not None else col(DIM, "     ")
    print(
        f"{prefix}  {col(W + BOLD, mdata['name'].ljust(16))}"
        f"  {type_tag(mtype)}"
        f"  {col(cat_col, cat.ljust(8))}"
        f"  PWR:{col(W, power.rjust(3))}"
        f"  ACC:{col(W, accuracy.rjust(6))}"
    )

# ---------------------------------------------------------------------------
# Pokemon picker
# ---------------------------------------------------------------------------

PAGE_SIZE = 20   # Pokemon per page in the browser


def _show_pokemon_page(names: list[str], page: int) -> int:
    """
    Print one page of the Pokemon list. Returns total page count.
    """
    total_pages = max(1, -(-len(names) // PAGE_SIZE))   # ceiling division
    page        = max(0, min(page, total_pages - 1))    # clamp

    start = page * PAGE_SIZE
    chunk = names[start:start + PAGE_SIZE]

    print()
    _divider()
    print(
        col(BOLD, f"  Pokemon Browser") +
        col(DIM,  f"  —  page {page + 1}/{total_pages}  ({len(names)} total)")
    )
    _divider()
    print()

    # Two-column layout
    mid = -(-len(chunk) // 2)   # ceiling half
    left_col  = chunk[:mid]
    right_col = chunk[mid:]

    for i, name in enumerate(left_col):
        num_l  = start + i + 1
        left   = f"  {col(Y, f'[{num_l}]')}  {name.title():<18}"
        if i < len(right_col):
            num_r  = start + mid + i + 1
            right  = f"  {col(Y, f'[{num_r}]')}  {right_col[i].title()}"
        else:
            right  = ""
        print(left + right)

    print()
    print(
        col(DIM, "  [a] prev page") +
        col(DIM, "   [d] next page") +
        col(DIM, "   [number] select") +
        col(DIM, "   or type a name")
    )
    _divider()
    return total_pages


def pick_pokemon(slot: str) -> dict:
    """
    Interactively pick a Pokemon using a paginated browser.

    Controls
    --------
    a / d       — previous / next page
    number      — select Pokemon by list number
    name        — type any Pokemon name directly (skips browser)
    """
    print()
    _info(f"Loading Pokemon list...")
    all_names   = fetch_all_pokemon_names()
    page        = 0
    total_pages = _show_pokemon_page(all_names, page)

    while True:
        raw = _ask(f"Pokemon {slot}").strip().lower()

        # Navigation
        if raw == "a":
            page = max(0, page - 1)
            total_pages = _show_pokemon_page(all_names, page)
            continue
        if raw == "d":
            page = min(total_pages - 1, page + 1)
            total_pages = _show_pokemon_page(all_names, page)
            continue

        # Number selection
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(all_names):
                name = all_names[idx]
            else:
                _err(f"Enter a number between 1 and {len(all_names)}.")
                continue
        else:
            # Typed name — search for closest match
            name = raw
            # Fuzzy-ish: check if it's a substring of any known name
            if name not in all_names:
                matches = [n for n in all_names if n.startswith(name)]
                if len(matches) == 1:
                    name = matches[0]
                    _info(f"Matched: {name.title()}")
                elif len(matches) > 1:
                    _info(f"Multiple matches: {', '.join(m.title() for m in matches[:8])}"
                          + ("..." if len(matches) > 8 else ""))
                    name = _ask("Which one? (type full name)").strip().lower()

        _info(f"Looking up {name.title()}...")
        pdata = fetch_pokemon(name)

        if pdata is None:
            _err(f"'{name}' not found. Try again.")
            total_pages = _show_pokemon_page(all_names, page)
            continue

        _print_pokemon_card(pdata["name"], pdata, pdata["moves"])

        if _confirm("Use this Pokemon?"):
            return pdata

        # Declined — redisplay the page
        total_pages = _show_pokemon_page(all_names, page)


# ---------------------------------------------------------------------------
# Move swap flow
# ---------------------------------------------------------------------------

def maybe_swap_moves(pdata: dict, opponent_pdata: dict | None = None) -> list[dict]:
    """
    Ask if the user wants to change moves.
    Goes through each of the 4 slots one by one.
    opponent_pdata: if provided, default move selection accounts for type matchups.
    Returns a list of 4 move dicts (the final chosen moveset).
    Returns original moves unchanged if user declines.
    """
    # Build the default moveset from pdata
    # Build default moveset.
    # Prefer moves that actually deal damage vs the opponent's typing,
    # sorted by effective power (base power x type multiplier).
    # Falls back to raw power if opponent types are unknown.
    all_move_data = [(s, fetch_move(s)) for s in pdata["moves"] if fetch_move(s)]

    opponent_types = opponent_pdata["types"] if opponent_pdata else []

    def effective_power(move_dict: dict) -> float:
        """Base power weighted by type effectiveness vs the opponent."""
        power = move_dict.get("power", 0)
        if power == 0:
            return 0.0
        if opponent_types:
            eff = type_multiplier(move_dict.get("type", "normal"), opponent_types)
        else:
            eff = 1.0
        return power * eff

    damaging = [(s, m) for s, m in all_move_data if m.get("power", 0) > 0]
    status   = [(s, m) for s, m in all_move_data if m.get("power", 0) == 0]

    # Sort by effective power descending (accounts for immunities and resistances)
    damaging.sort(key=lambda x: effective_power(x[1]), reverse=True)
    selected = damaging[:4]

    if len(selected) < 4:
        selected += status[:4 - len(selected)]

    while len(selected) < 4:
        selected.append(("quick-attack", fetch_move("quick-attack")))

    current_moves = [m for _, m in selected]

    print()
    if not _confirm(f"Would you like to change any of {pdata['name'].title()}'s moves?"):
        return current_moves

    # Build the full legal move pool
    all_slugs  = pdata["moves"]
    full_pool  = [fetch_move(s) for s in all_slugs if fetch_move(s)]

    print()
    _info("Going through each move slot. Choose a replacement or keep the current one.")

    for slot_idx in range(4):
        current = current_moves[slot_idx]
        print()
        _divider()
        print(col(BOLD, f"  Move slot {slot_idx + 1} (current):"))
        _print_move_row(None, current)
        _divider()
        print()

        # Show pool excluding already-chosen moves
        chosen_names = {m["name"] for m in current_moves}
        available = [m for m in full_pool if m["name"] not in chosen_names
                     or m["name"] == current["name"]]

        print(col(BOLD, "  Available moves:\n"))
        for i, m in enumerate(available):
            marker = col(G, " *") if m["name"] == current["name"] else "  "
            _print_move_row(i + 1, m)
            if m["name"] == current["name"]:
                # Also print as "keep" option at the end
                pass

        # Add explicit "keep" option at bottom
        keep_idx = len(available) + 1
        keep_label = current["name"]
        print(f"\n  {col(Y, f'[{keep_idx}]')}  {col(DIM, f'Keep current  ({keep_label})')}")

        choice = _pick_number(f"Choose move for slot {slot_idx + 1}", 1, keep_idx)

        if choice == keep_idx:
            _ok(f"Kept {current['name']}")
        else:
            picked = available[choice - 1]
            current_moves[slot_idx] = picked
            _ok(f"Changed to {picked['name']}")

    print()
    print(col(BOLD, "  Final moveset:"))
    for i, m in enumerate(current_moves):
        _print_move_row(i + 1, m)

    return current_moves

# ---------------------------------------------------------------------------
# Results display
# ---------------------------------------------------------------------------

def print_results(result: BattleResult):
    print()
    _header("SIMULATION RESULTS")
    print()

    bar_width = 40
    p1_fill   = int(bar_width * result.p1_wins / result.total) if result.total else 0
    p2_fill   = bar_width - p1_fill

    p1_bar = col(G + BOLD, "#" * p1_fill)
    p2_bar = col(R + BOLD, "#" * p2_fill)

    n1 = result.p1_name.upper().ljust(14)
    n2 = result.p2_name.upper().rjust(14)

    print(f"  {col(G, n1)}  {p1_bar}{p2_bar}  {col(R, n2)}")
    print(
        f"  {col(G + BOLD, f'{result.p1_pct:.1f}%'.ljust(16))}"
        f"{' ' * bar_width}"
        f"  {col(R + BOLD, f'{result.p2_pct:.1f}%')}"
    )
    print()
    _divider()
    print()

    if result.p1_pct > 55:
        verdict = col(G + BOLD, result.p1_name.upper())
    elif result.p2_pct > 55:
        verdict = col(R + BOLD, result.p2_name.upper())
    else:
        verdict = col(Y + BOLD, "TOO CLOSE TO CALL")

    print(f"  Verdict:  {verdict}")
    print()
    print(f"  {col(W + BOLD, result.p1_name.title().ljust(14))} wins  "
          f"{col(G, str(result.p1_wins).rjust(8))}  paths  "
          f"({col(G + BOLD, f'{result.p1_pct:.1f}%')})")
    print(f"  {col(W + BOLD, result.p2_name.title().ljust(14))} wins  "
          f"{col(R, str(result.p2_wins).rjust(8))}  paths  "
          f"({col(R + BOLD, f'{result.p2_pct:.1f}%')})")
    print(f"  {'Total paths'.ljust(14)}        "
          f"{col(W, str(result.total).rjust(8))}")
    print()
    _divider()
    print()
    print(f"  {col(DIM, 'Mode: ' + result.mode)}"
          f"  {col(DIM, f'  |  States visited: {result.states_visited:,}')}")

    # Type effectiveness notes
    print()
    print(col(BOLD, "  Type notes:"))
    # We need the Pokemon types — stored on result indirectly; print known matchups
    print(col(DIM, "  (see individual move details above for type matchups)"))
    print()

# ---------------------------------------------------------------------------
# Stat-only comparison
# ---------------------------------------------------------------------------

def stat_only_flow():
    _header("STAT-ONLY COMPARISON")
    p1_data = pick_pokemon("A")
    p2_data = pick_pokemon("B")

    dummy  = fetch_move("quick-attack")
    p1spec = Pokemon.from_dict(p1_data, [Move.from_dict("quick-attack", dummy)])
    p2spec = Pokemon.from_dict(p2_data, [Move.from_dict("quick-attack", dummy)])

    def bst(spec: Pokemon) -> int:
        return spec.attack + spec.defense + spec.sp_atk + spec.sp_def + spec.speed + spec.max_hp

    s1, s2 = bst(p1spec), bst(p2spec)
    prob1  = s1 / (s1 + s2)
    prob2  = 1 - prob1

    print()
    _header("STAT COMPARISON")
    print()
    print(f"  {col(W + BOLD, p1_data['name'].upper().ljust(14))}  BST: {col(G, str(s1))}")
    print(f"  {col(W + BOLD, p2_data['name'].upper().ljust(14))}  BST: {col(R, str(s2))}")
    print()

    bar_width = 40
    p1_fill   = int(bar_width * prob1)
    p2_fill   = bar_width - p1_fill
    print(f"  {col(G + BOLD, '#' * p1_fill)}{col(R + BOLD, '#' * p2_fill)}")
    print(f"  {col(G + BOLD, f'{prob1*100:.1f}%')}  vs  {col(R + BOLD, f'{prob2*100:.1f}%')}")
    print()

    winner = p1_data["name"] if prob1 > prob2 else p2_data["name"]
    print(f"  Edge: {col(BOLD, winner.upper())} by base stats")
    print()

# ---------------------------------------------------------------------------
# Full simulation flow
# ---------------------------------------------------------------------------

def full_sim_flow():
    _header("FULL SIMULATION")

    # Pokemon A (opponent not yet known, use global best moves)
    p1_data  = pick_pokemon("A")

    # Pokemon B
    p2_data  = pick_pokemon("B")

    # Now both are known — build movesets with opponent type awareness
    p1_moves = maybe_swap_moves(p1_data, opponent_pdata=p2_data)
    p2_moves = maybe_swap_moves(p2_data, opponent_pdata=p1_data)

    # Build specs
    p1spec = Pokemon.from_dict(
        p1_data,
        [Move.from_dict(m.get("slug", m["name"].lower().replace(" ", "-")), m)
         for m in p1_moves]
    )
    p2spec = Pokemon.from_dict(
        p2_data,
        [Move.from_dict(m.get("slug", m["name"].lower().replace(" ", "-")), m)
         for m in p2_moves]
    )

    # Print matchup summary
    print()
    _header("MATCHUP")
    print()
    print(f"  {col(G + BOLD, p1spec.name.upper())}  vs  {col(R + BOLD, p2spec.name.upper())}")
    print()
    print(col(G, f"  {p1spec.name.title()} moves:"))
    for m in p1spec.moves:
        _print_move_row(None, {
            "name": m.name, "power": m.power, "accuracy": m.accuracy,
            "pp": m.pp, "category": m.category, "type": m.type,
        })
    print()
    print(col(R, f"  {p2spec.name.title()} moves:"))
    for m in p2spec.moves:
        _print_move_row(None, {
            "name": m.name, "power": m.power, "accuracy": m.accuracy,
            "pp": m.pp, "category": m.category, "type": m.type,
        })
    print()

    # Type effectiveness summary
    _divider()
    print(col(BOLD, "\n  Type effectiveness:\n"))
    for m in p1spec.moves:
        eff = type_multiplier(m.type, p2spec.types)
        if eff != 1.0:
            tag = col(G + BOLD, f"{eff}x") if eff > 1 else \
                  col(R, f"{eff}x") if eff > 0 else col(DIM, "immune")
            print(f"  {p1spec.name.title()} {m.name} ({m.type}) -> {tag} vs {p2spec.name.title()}")
    for m in p2spec.moves:
        eff = type_multiplier(m.type, p1spec.types)
        if eff != 1.0:
            tag = col(G + BOLD, f"{eff}x") if eff > 1 else \
                  col(R, f"{eff}x") if eff > 0 else col(DIM, "immune")
            print(f"  {p2spec.name.title()} {m.name} ({m.type}) -> {tag} vs {p1spec.name.title()}")
    print()

    # Run simulation
    result_box: dict = {}
    stop_flag  = [False]

    def compute():
        result_box["result"] = run(p1spec, p2spec)
        stop_flag[0] = True

    label = f"Simulating {p1spec.name.title()} vs {p2spec.name.title()}..."
    t = threading.Thread(target=compute, daemon=True)
    t.start()
    _spinner(label, stop_flag)
    t.join()

    print_results(result_box["result"])

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu() -> int:
    print()
    _header("POKESIM - Pokemon Battle Path Simulator")
    print()
    api_status = col(G, "PokeAPI connected") if api_available() \
                 else col(Y, "Offline - using bundled data")
    print(f"  {api_status}")
    print()
    _divider()
    print(f"  {col(Y, '[1]')}  Full simulation  (pick Pokemon + moves)")
    print(f"  {col(Y, '[2]')}  Stat-only comparison  (no moves, quick)")
    print(f"  {col(Y, '[3]')}  Quit")
    _divider()
    return _pick_number("Select option", 1, 3)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print(col(M + BOLD, "\n  pokesim  |  Pokemon Battle Path Simulator"))
    print(col(DIM,      "  Enumerating every possible battle outcome\n"))

    while True:
        choice = main_menu()

        if choice == 1:
            full_sim_flow()
        elif choice == 2:
            stat_only_flow()
        elif choice == 3:
            print()
            _info("Goodbye!")
            print()
            sys.exit(0)

        print()
        if not _confirm("Run another simulation?"):
            _info("Goodbye!")
            print()
            sys.exit(0)