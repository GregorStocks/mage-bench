# Golden-Test Card Reference

Start here before a fresh Scryfall search. The first sections are anchored in
current repo goldens. The later sections are adjacent cards found with the same
search patterns when the proven package is close but not exact.

## Repo-proven packages

### Fast mana and compressed setup

- `Black Lotus`
  Why: jumps from a one-land opening hand straight into a 3- or 4-mana test
  state. It is the main reason several goldens avoid filler turns.
  Used in: `test_golden_dark_depths_combo.py`, `test_golden_clone_copies_memnite.py`,
  `test_golden_mana_drain_fact_or_fiction.py`, and
  `test_golden_emancipation_angel_trigger.py`.
  Search nearby: `game:paper unique:cards order:edhrec (t:artifact or t:creature) mv<=1 (o:"Add one mana of any color" or o:"Add three mana of any one color" or o:"Add {C}{C}")`

- `Memnite`
  Why: the cleanest free permanent for copy tests, combat setup, or stack
  targeting with minimal extra text.
  Used in: `test_golden_clone_copies_memnite.py` and
  `test_golden_bolt_on_stack.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc t:creature mv=0`

### Stack interaction and explicit spell choices

- `Lightning Bolt`
  Why: one mana, one target, clean damage math. Ideal for stack rendering,
  target prompts, and resolve-vs-not-resolve checks.
  Used in: `test_golden_bolt_on_stack.py`, `test_golden_stack_resolved.py`,
  and indirectly the board-cursor / end-of-turn goldens built on the same deck.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:spell mv<=2 function:removal`

- `Mana Drain`
  Why: compact counterspell coverage that also creates next-turn mana, so one
  card exercises both stack interaction and future resource carry-over.
  Used in: `test_golden_mana_drain_fact_or_fiction.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:spell mv<=2 function:counterspell`

- `Fact or Fiction`
  Why: a single spell creates pile splitting, hidden-zone reveal, and a crisp
  follow-up decision without needing a long setup.
  Used in: `test_golden_mana_drain_fact_or_fiction.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc (o:"separate those cards into two piles" or o:"into two piles")`

### Trigger prompts and unusual card presentation

- `Emancipation Angel`
  Why: straightforward enters-the-battlefield trigger that asks for a target
  immediately after resolution.
  Used in: `test_golden_emancipation_angel_trigger.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

- `Crashing Footfalls`
  Why: suspend gives a clean delayed-cast prompt from exile and creates a
  visible token payoff.
  Used in: `test_golden_mdfc_land_and_suspend.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc o:suspend`

- `Boggart Trawler // Boggart Bog`
  Why: MDFC land-mode play plus an enters-tapped rider creates a compact
  rendering test for alternative play modes.
  Used in: `test_golden_mdfc_land_and_suspend.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:mdfc`

### Land combos and zone oddities

- `Dark Depths` plus `Thespian's Stage`
  Why: extremely compact land combo that stresses copying, legend rule choices,
  counters, token creation, and a fast lethal attack.
  Used in: `test_golden_dark_depths_combo.py`.
  Search nearby: `game:paper unique:cards order:edhrec t:land (o:"copy target land" or o:"ice counter")`

- `Ancient Stirrings`
  Why: looked-at zone selection with a colorless filter catches short-id and
  temporary-zone bugs without needing many game actions.
  Used in: `test_golden_ancient_stirrings_conflict.py`.
  Search nearby: `game:paper unique:cards order:cmc direction:asc is:spell mv=1 o:"look at the top" o:"colorless"`

### Deterministic combat bodies

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

## Adjacent cards worth trying next

These are not the source of the current goldens, but they come from the same
query families and solve similar problems.

### More fast mana

- `Lotus Petal`: single-shot colored mana when `Black Lotus` is more explosive
  than the test needs.
- `Simian Spirit Guide`: instant-speed red burst without creating a battlefield
  permanent.
- `Elvish Spirit Guide`: same idea for green tests.

Suggested query:
`game:paper unique:cards order:edhrec mv<=1 (o:"Add one mana of any color" or o:"Add {R}" or o:"Add {G}")`

### More zero-mana bodies or clone fodder

- `Ornithopter`: still free, but flying matters if you want evasion text.
- `Phyrexian Walker`: free, colorless, and nearly textless.
- `Shield Sphere`: free wall if you want a blocker that should not attack.

Suggested query:
`game:paper unique:cards order:cmc direction:asc t:creature mv=0`

### More triggered permanents

- `Kor Skyfisher`: cheaper self-bounce cousin to `Emancipation Angel`.
- `Spirited Companion`: simple ETB card draw.
- `Thraben Inspector`: clean ETB token creation.

Suggested query:
`game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)`

### Graveyard setup

- `Faithless Looting`: single spell, immediate discard, very compact.
- `Stitcher's Supplier`: mills on ETB and death, useful when you want two
  trigger windows from one card.
- `Satyr Wayfinder`: self-mill plus a guaranteed hand update.

Suggested query:
`game:paper unique:cards order:cmc direction:asc mv<=3 (o:"mill" or o:"discard" or o:"into your graveyard")`

## Scryfall notes that matter for this repo

- Keep `game:paper` in the baseline query. That avoids Arena- or MTGO-only
  answers for cards XMage will not model the same way.
- Prefer `unique:cards` while exploring. Switch to `unique:prints` only when
  you already picked a card and want set / collector-number options.
- `order:cmc direction:asc` is usually better than popularity sorts for golden
  design because compact setup is more important than general play rate.
- `function:` tags are useful for broad roles, especially `function:clone`,
  `function:removal`, and `function:counterspell`.
- Regex Oracle queries are worth using. `o:/^When/` and `o:/^Whenever/` find
  explicit trigger prompts quickly; `o:/^{T}:/` finds tap abilities with no
  extra payment.
- Use `fo:` instead of `o:` when reminder text matters for the scenario, such
  as keyword cards where the reminder text is the only obvious description.
- Use `is:mdfc`, `is:split`, `is:transform`, and `o:suspend` when you want to
  stress UI presentation, alternate play modes, or exile-cast flows.
