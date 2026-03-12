---
name: golden-test
description: Create a new golden prompt test for a specific game scenario in puppeteer/tests.
---

# Add a Golden Test

Create a new golden prompt test that captures a specific game scenario.

## Background

Golden tests run real XMage games with scripted replay pilots, capture the exact messages array that would be sent to the LLM, and compare against golden files. They verify that the wire-format prompt the LLM receives is correct for a given game state.

Each golden test has three components:

1. **Deck file** (`puppeteer/tests/decks/<name>.dck`) — deterministic card draw order (skip-shuffling is enabled)
2. **Test file** (`puppeteer/tests/test_golden_<name>.py`) — defines the scripted MCP tool call sequence
3. **Golden file** (`puppeteer/tests/golden/prompts/<name>.json`) — the captured prompt (auto-generated)

Key files to understand:

- `puppeteer/tests/golden_helpers.py` — shared test infrastructure (`run_golden_scenario`, `assert_golden_prompt`, deck constants)
- `puppeteer/tests/test_golden_bolt_on_stack.py` — example: two Lightning Bolts on the stack targeting different things
- `puppeteer/tests/test_golden_clone_copies_memnite.py` — example: Clone copying a creature
- `puppeteer/tests/test_golden_initial_decision.py` — example: simplest possible test (first decision point)

## Step 1: Interview the user

Ask the user what game state they want the golden test to capture. You need to understand:

1. **What cards are involved?** (names, quantities)
2. **What's the desired end state?** (board state, stack, graveyard, etc.)
3. **What game mechanic is being tested?** (targeting, combat, ETB triggers, copy effects, etc.)

If the user's description is vague, ask clarifying questions. You need enough detail to design the deck and script.

## Step 2: Design the deck

Design a 60-card deck where the first 7 cards (the opening hand with skip-shuffling) contain exactly what's needed. Fill the rest with Plains or basic lands.

**Deck format:** `<count> [<SET>:<NUM>] <Card Name>` — one line per card/printing.

**Constraints:**

- Exactly 60 cards total
- All cards needed for the scenario must be in the first 7 positions (the opening hand)
- Use real set codes and collector numbers (check existing decks for examples)
- The 8th+ cards should be basic lands (these get drawn on subsequent turns)

**ID assignment:** IDs are assigned alphabetically by card name starting at p3 (p1=TestPlayer, p2=Opponent). Cards with the same name get consecutive IDs. Predict the IDs and note them in script comments.

**Mana planning:** Count how many turns you need based on available mana sources. Remember:

- T1 (on the play): no draw, one main phase, no combat
- T2+: untap, draw, precombat main, combat, postcombat main
- You need one untapped land producing the right color per spell

## Step 3: Design the script

The script is a list of MCP tool calls: `pass_priority`, `choose_action`, and `get_game_state`.

**Standard preamble (every test starts with this):**

```python
# Choose TestPlayer as starting player, keep hand.
{"name": "pass_priority", "arguments": {}},
{"name": "choose_action", "arguments": {"index": 0}},
{"name": "pass_priority", "arguments": {}},
{"name": "choose_action", "arguments": {"answer": False}},
```

**Bridge auto-pass behavior — critical to understand:**

The bridge has a "first pass" optimization: within each `pass_priority` call, the first callback with playable cards is auto-passed (skipped). The bridge stops on the second+ callback with playable cards. This means:

- After playing a land, the next `pass_priority` auto-passes the current main phase once. If there are sorcery-speed plays available (creatures, sorceries), it stops in main. If only instants, it advances to combat and stops there.
- After casting a spell that resolves, the next `pass_priority` auto-passes once, then stops if there are still playable cards.

**Keeping multiple spells on the stack simultaneously:**

Use **chained `choose_action` calls** (no `pass_priority` between casts). The `choose_action` method does NOT run the auto-pass loop — it handles the pending action directly and waits for the next server callback. This lets you:

1. `pass_priority` — get the GAME_SELECT with castable spells
2. `choose_action(index=0)` — cast spell #1 (response: next_action=GAME_TARGET)
3. `choose_action(id="p2")` — choose target (response: next_action=GAME_SELECT)
4. `choose_action(index=0)` — cast spell #2 without spell #1 resolving
5. `choose_action(id="pN")` — choose target for spell #2

If you used `pass_priority` between steps 3 and 4, the auto-pass loop would pass priority, the opponent would pass, and spell #1 would resolve before you could cast spell #2.

**Combat with creatures on the battlefield:**

When you have creatures, `pass_priority` after a land play will hit the Declare Attackers combat selection (which always returns, even at actionsPassed=0). Handle it with `choose_action(answer=false)` to skip attacking, then `pass_priority` again to reach postcombat main.

**Common patterns:**

- Play a land: `choose_action(id="pN")` where pN is the land's ID
- Cast a spell: `choose_action(index=N)` or `choose_action(id="pN")`
- Target a player: `choose_action(id="p1")` (you) or `choose_action(id="p2")` (opponent)
- Target a permanent: `choose_action(id="pN")` where pN is the permanent's ID (stable across zones)
- Keep hand: `choose_action(answer=false)` (false=keep, true=mulligan)
- Pass/decline: `choose_action(answer=false)`
- Skip attacking: `choose_action(answer=false)` at declare_attackers

**End with `get_game_state`** to capture the final board state in the golden prompt.

## Step 4: Write the files

1. **Create or update the deck file** in `puppeteer/tests/decks/`. If an existing deck works, use it. Add a constant in `golden_helpers.py` if creating a new deck.

2. **Create the test file** at `puppeteer/tests/test_golden_<name>.py`:

   ```python
   """Golden prompt test: <description>."""

   import pytest

   from tests.golden_helpers import (
       DECK_YOUR_DECK,
       DECK_FILLER,
       assert_golden_prompt,
       run_golden_scenario,
   )

   @pytest.mark.golden
   def test_<name>(xmage_server, tmp_path, project_root):
       """<One-line description of what this tests>."""
       server, port = xmage_server
       prompt = run_golden_scenario(
           server=server,
           port=port,
           project_root=project_root,
           game_dir=tmp_path / "<name>",
           deck_a=DECK_YOUR_DECK,
           deck_b=DECK_FILLER,
           script=[
               # ... scripted tool calls ...
           ],
       )
       assert_golden_prompt("<name>", prompt)
   ```

3. **Add the deck constant** to `puppeteer/tests/golden_helpers.py` if it's a new deck:

   ```python
   DECK_MY_NEW_DECK = "puppeteer/tests/decks/my_new_deck.dck"
   ```

## Step 5: Generate and verify

1. Run `make check` to verify lint/typecheck/unit tests pass.

2. Run `make update-golden` to generate the golden prompt JSON:

   ```bash
   make update-golden
   ```

   This starts an XMage server, runs all golden tests, and writes the golden files. If the script has errors (wrong IDs, wrong indices), the test will fail with details about what went wrong. Fix the script and re-run.

3. **Read the generated golden file** to verify the scenario played out correctly. Check:
   - The stack contains what you expect
   - The battlefield has the right permanents
   - Targets are correct
   - No error responses in the tool results

4. If the script produced errors (e.g., `"error_code":"invalid_choice"`), examine the error to understand what choices were actually available, fix the script, and re-run `make update-golden`.

## Step 6: Commit and PR

Commit the deck, test file, and generated golden JSON together. Use `/pr` to create the pull request.
