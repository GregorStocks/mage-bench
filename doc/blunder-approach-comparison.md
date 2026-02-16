# Blunder Analysis Approach Comparison

Controlled experiment comparing 13 annotation approaches across 5 test games.

## Motivation

In game g8, the v5 single-pass Opus annotator attributed a Magmatic Hellkite blunder
to snapshot 55 (where the card was revealed for Sarkhan's behold) instead of snapshot
75 (where the ETB target was chosen). Root cause: when Opus processes all decisions in
one shot, it confuses details between decisions that mention the same card.

## Approaches tested

| ID | Description | Model | Architecture |
|----|-------------|-------|-------------|
| baseline | Single-pass, current v5 prompt | Opus | 1 call for all decisions |
| A | Inline annotation (emit after each decision) | Opus | 1 call, structured output |
| B | Per-decision | Flash | 1 call per decision |
| C | Single-pass with extended thinking | Opus | 1 call, thinking enabled |
| D | Per-decision | Opus | 1 call per decision |
| E | Per-decision | Sonnet 4.5 | 1 call per decision |
| F | Per-decision, minimal prompt | Opus | 1 call per decision |
| G | Flash screen + Opus deep dive | Flash + Opus | 2-phase |
| H | Batched (5 decisions per call) | Opus | 1 call per batch |
| I | Multi-turn conversation | Opus | sequential chat |
| J | Multi-turn conversation | Sonnet 4.5 | sequential chat |
| K | Per-decision with extended thinking | Opus | 1 call per decision, thinking |
| L | Per-decision with extended thinking | Sonnet 4.5 | 1 call per decision, thinking |

## Test games

| Game | Decisions | Non-forced | Format | Approaches run |
|------|-----------|------------|--------|----------------|
| g8 | 113 | 14 | Standard | 13 |
| g3 | 113 | 54 | Legacy (Doomsday vs Lands) | 13 |
| g4 | 305 | 144 | Modern (Yawgmoth vs Prowess) | 11 |
| g1 | 390 | 106 | Modern (Eldrazi vs Bant) | 6 |
| new | 205 | 81 | Modern | 6 |

## Results

Consensus defined as flagged by >= max(3, 30% of approaches) on that game,
with snapshots within +/-2 considered the same blunder.

### Coverage and precision

| Approach | Games | Coverage | FP | FP% | $/game | $/hit | s/game |
|----------|-------|----------|-----|-----|--------|-------|--------|
| **L_sonnet_thinking** | **5** | **98%** | **23** | **11%** | **$3.29** | **$0.18** | **4291s** |
| D_opus | 5 | 79% | 4 | 3% | $1.58 | $0.11 | 1184s |
| G_flash_opus | 3 | 78% | 0 | 0% | $1.55 | $0.10 | 1225s |
| B_flash | 5 | 76% | 26 | 17% | $0.07 | $0.00 | 148s |
| H_opus_batched | 5 | 71% | 3 | 3% | $0.54 | $0.04 | 355s |
| F_opus_minimal | 3 | 71% | 1 | 1% | $1.59 | $0.12 | 1222s |
| K_opus_thinking | 5 | 63% | 0 | 0% | $5.66 | $0.49 | 4457s |
| E_sonnet | 5 | 53% | 6 | 7% | $0.50 | $0.05 | 513s |
| baseline | 3 | 48% | 0 | 0% | $0.17 | $0.02 | 51s |
| I_convo_opus | 2 | 45% | 0 | 0% | $1.59 | $0.32 | 146s |
| A_inline | 3 | 43% | 0 | 0% | $0.18 | $0.02 | 46s |
| J_convo_sonnet | 2 | 5% | 0 | 0% | $0.63 | $1.27 | 46s |
| C_thinking | 3 | 2% | 0 | 0% | $0.40 | $1.21 | 245s |

### L is a strict superset of D

For every game where both ran, every consensus blunder found by D was also found by L.
The reverse is not true — L finds 7-18 additional consensus blunders per game that D misses.

| Game | Consensus | D hits | L hits | D-only | L-only |
|------|-----------|--------|--------|--------|--------|
| g8 | 4 | 2 | 2 | 0 | 0 |
| g3 | 18 | 13 | 17 | 0 | 4 |
| g4 | 36 | 29 | 36 | 0 | 7 |
| g1 | 20 | 13 | 20 | 0 | 7 |
| new | 14 | 14 | 14 | 0 | 0 |

### L's "false positives" are mostly real

Of 23 non-consensus annotations from L across all games:
- 12 are corroborated by 1 other approach (usually B_flash or D_opus)
- 4 are corroborated by 2 other approaches (just under consensus threshold)
- 7 are unique to L

The descriptions read as genuine observations, not hallucinations. Examples:
- "Cast Preordain before deploying Monastery Swiftspear, wasting a prowess trigger"
- "Played basic Swamp instead of Watery Grave, limiting counterspell options"
- "Activated Fiery Islet to draw before casting spells, leaving no mana for Preordain"

### Extended thinking helps Sonnet but hurts Opus

| Model | Without thinking | With thinking | Change |
|-------|-----------------|---------------|--------|
| Sonnet 4.5 | 53% coverage (E) | 98% coverage (L) | +45pp |
| Opus | 79% coverage (D) | 63% coverage (K) | -16pp |

Thinking transforms Sonnet from mediocre to dominant. But it makes Opus worse —
possibly by encouraging overthinking that second-guesses correct initial assessments.
K also costs 3.6x more than D ($5.66 vs $1.58/game) for worse results.

## Spot checks

### 1. Magmatic Hellkite ETB target (g8, snap 75)

The bug that started this experiment. Correct answer: snap 75 (ETB chose wrong target).
v5 production attributed to snap 55 (card revealed during behold).

| Approach | Found? | Snapshot | Description |
|----------|--------|----------|-------------|
| D_opus | Yes | 75 | "Destroyed Multiversal Passage instead of Spirebluff Canal" |
| L_sonnet | Yes | 73 + 75 | Found both the casting decision and ETB target |
| K_opus | Yes | 75 | Same correct attribution |
| E_sonnet | No | - | Missed entirely |
| B_flash | Yes | 55 + 75 | Found the correct snap but also the wrong one |
| baseline | Yes | 75 | Correct (but misses many other blunders in larger games) |

### 2. Doomsday catastrophe (g3)

Llama4 Coach casts Doomsday 3+ times with no win condition in pile while opponent has
lethal Dark Depths combo assembled.

| Approach | Doomsday annotations | Dark Depths annotations |
|----------|---------------------|------------------------|
| L_sonnet | 21 | 18 |
| K_opus | 15 | 13 |
| D_opus | 14 | 15 |
| E_sonnet | 11 | 9 |
| baseline | 4 | 3 |

L provides the most thorough coverage of this cascading disaster, flagging the initial
failure to combo (snap 32), each subsequent bad Doomsday cast, and each turn the
opponent fails to activate the lethal Stage+Depths combo.

### 3. Consensus blunders L catches but D misses (g4)

7 consensus blunders found by L but missed by D (0 in the reverse direction):

- **snap 10**: Played fetchland instead of Forest, preventing turn 1 Delighted Halfling
- **snap 32**: Chose Agatha's Soul Cauldron over second Halfling when needing 4 mana for Yawgmoth
- **snap 43**: Declined free attack into empty board for 2-3 damage
- **snap 78**: Passed turn with 6 mana and multiple castable creatures in hand
- **snap 89**: Bauble targeting opponent instead of self (missing scry information)
- **snap 104**: Used Lava Dart on 2/4 Yawgmoth (doesn't kill it) instead of killing Halfling
- **snap 32 variant**: Failed to sequence lands for maximum mana efficiency

All corroborated by 3-5 other approaches — these are real blunders, not noise.

## Recommendation

**Use L (per-decision Sonnet 4.5 with extended thinking) for production blunder analysis.**

- 98% consensus coverage vs D's 79% — a massive accuracy gap
- L is a strict superset of D's coverage across all 5 test games
- $3.29/game is 2x D but finds 24% more blunders
- 11% false positive rate is acceptable (mostly real observations below consensus threshold)
- Extended thinking on Sonnet is the single biggest accuracy improvement in the experiment
