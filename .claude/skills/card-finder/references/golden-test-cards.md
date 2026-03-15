# Golden-Test Card Reference

Start here before a fresh Scryfall search. This file is for reusable building
blocks you are likely to want across many goldens, not an index of every narrow
card that appears in the current suite.

Current repo goldens are still the evidence for what earns inclusion here. If a
card only exists to satisfy one specific scenario, prefer rediscovering it with
the helper script instead of memorizing it as a staple.

## Core utility cards

### Fast mana and compressed setup

- `Black Lotus`
  Why: jumps from a one-land opening hand straight into a 3- or 4-mana test
  state. It is the main reason several goldens avoid filler turns.
  Used in: `test_golden_dark_depths_combo.py`,
  `test_golden_clone_copies_memnite.py`,
  `test_golden_mana_drain_fact_or_fiction.py`, and
  `test_golden_emancipation_angel_trigger.py`.
  Search nearby: `game:paper unique:cards order:edhrec (t:artifact or t:creature) mv<=1 (o:"Add one mana of any color" or o:"Add three mana of any one color" or o:"Add {C}{C}")`

- `Lotus Petal`
  Why: same role as `Black Lotus`, but less explosive and often cleaner when
  you only need one extra mana.
  Search nearby: `game:paper unique:cards order:edhrec mv<=1 o:"Add one mana of any color"`

- `Simian Spirit Guide`
  Why: instant-speed red burst without adding a permanent or extra battlefield
  text.
  Search nearby: `game:paper unique:cards order:edhrec mv<=3 o:"Add {R}"`

- `Elvish Spirit Guide`
  Why: green version of the same trick for suspend, self-mill, or creature
  setup tests.
  Search nearby: `game:paper unique:cards order:edhrec mv<=3 o:"Add {G}"`

- `Dark Ritual`
  Why: the cleanest one-card jump from one black mana to three.
  Search nearby: `game:paper unique:cards order:edhrec mv=1 o:"Add {B}{B}{B}"`

### Free and low-noise bodies

- `Memnite`
  Why: the cleanest free permanent for copy tests, combat setup, or stack
  targeting with minimal extra text.
  Used in: `test_golden_clone_copies_memnite.py` and
  `test_golden_bolt_on_stack.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

- `Ornithopter`
  Why: still free, but flying matters when you want evasion or an obvious
  non-ground blocker.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

- `Phyrexian Walker`
  Why: free and almost textless. Good when even flying on `Ornithopter` is more
  rules text than you want.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

- `Shield Sphere`
  Why: free body that naturally stays defensive if you want a blocker and not a
  credible attacker.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

### Clean combat and targeting pieces

- `Lightning Bolt`
  Why: one mana, one target, clean damage math. Ideal for stack rendering,
  target prompts, and resolve-vs-not-resolve checks.
  Used in: `test_golden_bolt_on_stack.py`, `test_golden_stack_resolved.py`,
  and indirectly the board-cursor / end-of-turn goldens built on the same deck.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:spell mv<=2 function:removal`

- `Savannah Lions`
  Why: cheap 2/1 body with essentially no rules baggage. Good for attack/block
  prompts and blocker-ID checks.
  Used in: `test_golden_savannah_lions_trade.py` and
  `test_golden_savannah_lions_blocker_ids.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv<=2 pow>=2 tou<=2 -o:/^When/ -o:/^Whenever/ -o:/dies/`

- `Grizzly Bears`
  Why: simple 2/2 body that pairs well with multiple-blocker and damage
  distribution tests.
  Used in: `test_golden_multi_amount_combat.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=2 pow=2 tou=2 -o:/^When/ -o:/^Whenever/`

### Simple prompt generators

- `Emancipation Angel`
  Why: straightforward enters-the-battlefield trigger that asks for a target
  immediately after resolution.
  Used in: `test_golden_emancipation_angel_trigger.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

- `Kor Skyfisher`
  Why: cheaper self-bounce cousin to `Emancipation Angel` for the same class of
  ETB prompt.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

- `Thraben Inspector`
  Why: compact ETB token creation with very little surrounding noise.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

## Useful search buckets

- Fast mana:
  `game:paper unique:cards order:edhrec (t:artifact or t:creature or t:instant) mv<=1 (o:"Add one mana of any color" or o:"Add three mana of any one color" or o:"Add {C}{C}" or o:"Add {B}{B}{B}")`

- Zero-mana bodies:
  `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

- Clean removal / stack tests:
  `game:paper unique:cards order:cmc direction:asc is:spell mv<=2 function:removal`

- Clean counterspells:
  `game:paper unique:cards order:cmc direction:asc is:spell mv<=2 function:counterspell`

- ETB trigger prompts:
  `game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

- Graveyard setup:
  `game:paper unique:cards order:cmc direction:asc mv<=3 (o:"mill" or o:"discard" or o:"into your graveyard")`

- UI-heavy special cards:
  `game:paper unique:cards order:cmc direction:asc (is:mdfc or is:split or is:transform or o:suspend)`

## Existing goldens also prove niche cards

Current goldens include scenario-specific packages such as `Dark Depths` plus
`Thespian's Stage`, `Ancient Stirrings`, `Mana Drain` plus `Fact or Fiction`,
`Crashing Footfalls`, and `Boggart Trawler // Boggart Bog`. Those are useful
when you need exactly that mechanic, but they are intentionally not part of the
main staple list above.
