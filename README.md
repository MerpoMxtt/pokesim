# pokesim

**Enumerate every possible Pokemon battle outcome — by stats, moves, and type matchups.**

pokesim is a terminal simulator that pits two Pokemon against each other and maps out every possible battle path to tell you exactly how often each one wins. No guessing, no sampling by default — full enumeration.

```
  ARCEUS          ########################################        RIBOMBEE
  88.4%                                                     11.6%

  Verdict: ARCEUS

  Arceus         wins    884,000  paths  (88.4%)
  Ribombee       wins    116,000  paths  (11.6%)
  Total paths           1,000,000

  Mode: exact  |  States visited: 2,799
```

---

## Features

- **Full path enumeration** — explores every possible sequence of moves via memoized state-space search. Reports exact win counts across all battle paths.
- **Monte Carlo fallback** — for complex matchups that exceed the state budget, runs 500,000 random battles and tallies wins.
- **All 1,300+ Pokemon** — fetches live from [PokeAPI](https://pokeapi.co); 15 fan-favourites bundled for offline use.
- **Paginated Pokemon browser** — navigate with `a`/`d`, select by number or type a name directly.
- **Type-aware default moves** — default movesets are picked by effective power vs the specific opponent, not raw power globally. Immune moves rank last.
- **Gen 8 damage formula** — base stats, STAB (1.5x), type effectiveness, burn penalty, critical hit probability weighting, damage roll range.
- **Full 18-type chart** — all types including Fairy, dual-type interactions, immunities.
- **Status conditions** — burn, paralysis, poison, freeze, sleep. Each produces distinct battle states in the enumeration tree.
- **Priority moves** — Quick Attack, Extreme Speed go first regardless of Speed.
- **Stat-only mode** — instant weighted base stat comparison, no move selection needed.
- **Zero required dependencies** — pure Python 3.11+ stdlib.

---

## Installation

```bash
git clone https://github.com/MerpoMxtt/pokesim
cd pokesim
python -m pokesim
```

No `pip install` needed for normal use.

For running tests:

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Usage

```
python -m pokesim
```

```
  [1]  Full simulation  (pick Pokemon + moves)
  [2]  Stat-only comparison  (no moves, quick)
  [3]  Quit
```

### Full simulation

1. Browse the paginated Pokemon list — `a`/`d` to flip pages, enter a number or type a name
2. Optionally swap any of the 4 default moves — the sim walks you through each slot
3. Repeat for the opponent
4. The engine enumerates every possible battle path and reports win counts

### Pokemon browser

```
  Pokemon Browser  —  page 3/66  (1302 total)
  --------------------------------------------------------
  [41]  Zubat               [51]  Oddish
  [42]  Golbat              [52]  Gloom
  ...

  [a] prev page   [d] next page   [number] select   or type a name
```

### Move swap

When prompted, go through each of the 4 move slots. For each one you can keep the current move or pick any legal move from that Pokemon's learnset. Changes are temporary — after the simulation both Pokemon reset to their defaults.

---

## How it works

### Damage formula (Gen 8)

```
Damage = floor(((2×Level/5 + 2) × Power × Atk/Def) / 50 + 2) × Modifiers
```

Modifiers: STAB (1.5x), type effectiveness, burn (-50% physical), damage roll [0.85–1.0]. Critical hits (1/24 chance, 1.5x) are probability-weighted.

### Path enumeration

Each turn both Pokemon pick one of their 4 moves. The engine enumerates every combination, branches each into miss / low roll / high roll outcomes, and recurses. States are bucketed by HP (40 slots) and status condition, then memoized — paths that converge to the same state are computed once. Win probabilities are accumulated as floats and scaled to 1,000,000 displayed paths at the root.

### Automatic mode selection

| Condition | Mode |
|-----------|------|
| ≤ 200,000 unique states | Exact enumeration |
| > 200,000 unique states | Monte Carlo (500,000 random battles) |

---

## Project structure

```
pokesim/
├── pokesim/
│   ├── __init__.py      version, author, license
│   ├── __main__.py      entry point  (python -m pokesim)
│   ├── data.py          PokeAPI fetch, type chart, bundled fallback
│   ├── engine.py        damage formula, path enumeration, Monte Carlo
│   └── cli.py           terminal UI — browser, move swap, results
├── tests/
│   ├── test_smoke.py    2 tests
│   ├── test_data.py     38 tests
│   ├── test_engine.py   38 tests
│   └── test_cli.py      19 tests
├── pyproject.toml
└── requirements.txt
```

---

## Roadmap

- [x] Repo scaffold
- [x] Data layer — PokeAPI fetch, type chart, bundled fallback
- [x] Battle engine — damage formula, exact enumeration, Monte Carlo fallback
- [x] Terminal UI — paginated browser, move swap flow, results display
- [ ] Items — held item support (Choice Band, Life Orb, etc.)
- [ ] Weather — sun/rain/sand/hail effects
- [ ] Abilities — Intimidate, Levitate, Thick Fat, etc.
- [ ] EVs/IVs — optional competitive stat customization

---

## Data credit

Pokemon data provided by [PokeAPI](https://pokeapi.co). Pokemon and all related names are trademarks of Nintendo / Game Freak. This project is not affiliated with or endorsed by Nintendo or Game Freak.

---

## License

[MIT](LICENSE)
