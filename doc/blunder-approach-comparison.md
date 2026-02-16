# Blunder Analysis Approach Comparison

Controlled experiment comparing 17 annotation approaches across 5 test games.

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
| L | Per-decision with extended thinking | Sonnet 4.5 | 1 call per decision, thinking=high |
| M | Batched with medium thinking | Sonnet 4.5 | 5 per call, thinking=medium |
| N | Batched with high thinking | Sonnet 4.5 | 5 per call, thinking=high |
| O | Per-decision with medium thinking | Sonnet 4.5 | 1 call per decision, thinking=medium |
| P | Per-decision with low thinking | Sonnet 4.5 | 1 call per decision, thinking=low |

## Test games

| Game | Decisions | Non-forced | Format | Approaches run |
|------|-----------|------------|--------|----------------|
| g8 | 23 | 14 | Standard | 17 |
| g3 | 113 | 54 | Legacy (Doomsday vs Lands) | 17 |
| g4 | 186 | 144 | Modern (Yawgmoth vs Prowess) | 15 |
| g1 | 150 | 68 | Modern (Eldrazi vs Bant) | 10 |
| 003230 | 134 | 70 | Modern | 10 |

## Results

Consensus defined as flagged by >= max(3, 30% of approaches) on that game,
with snapshots within +/-2 considered the same blunder.

### Coverage and precision

Coverage = consensus blunder hits / (hits + misses). FP = non-consensus annotations.

| Approach | Games | Coverage | FP% | $/game | s/game |
|----------|-------|----------|-----|--------|--------|
| **L_sonnet_thinking** | **5** | **91%** | **11%** | **$3.29** | **4291s** |
| **O_sonnet_medium** | **5** | **87%** | **15%** | **$2.97** | **3929s** |
| P_sonnet_low | 5 | 83% | 14% | $1.50 | 1899s |
| D_opus | 5 | 66% | 5% | $1.58 | 1184s |
| G_flash_opus | 3 | 64% | 6% | $1.55 | 1225s |
| B_flash | 5 | 56% | 33% | $0.07 | 148s |
| F_opus_minimal | 3 | 61% | 7% | $1.59 | 1222s |
| K_opus_thinking | 5 | 51% | 3% | $5.66 | 4457s |
| N_sonnet_batched_high | 5 | 50% | 12% | $0.89 | 1132s |
| M_sonnet_batched_medium | 5 | 48% | 10% | $0.87 | 1098s |
| H_opus_batched | 5 | 47% | 5% | $0.54 | 355s |
| E_sonnet | 5 | 37% | 22% | $0.50 | 513s |
| baseline | 3 | 32% | 6% | $0.17 | 51s |
| I_convo_opus | 2 | 45% | 7% | $1.59 | 146s |
| A_inline | 3 | 31% | 0% | $0.18 | 46s |
| J_convo_sonnet | 2 | 0% | 100% | $0.63 | 46s |
| C_thinking | 3 | 1% | 0% | $0.40 | 245s |

Note: coverage percentages changed from Phase 1 because adding 4 more approaches
(M-P) shifted the consensus pool. The relative ordering is preserved.

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

## Phase 2: Cost optimization

L costs $3.29/game, up from ~$0.17 for the v5 baseline. We tested whether we could
reduce cost without losing much accuracy by isolating two factors:

1. **Batching**: sending multiple decisions per API call (reduces per-call overhead)
2. **Thinking effort**: high vs medium vs low extended thinking

### Factor isolation

| Approach | Architecture | Thinking | Coverage | $/game |
|----------|-------------|----------|----------|--------|
| L | per-decision | high | 91% | $3.29 |
| O | per-decision | medium | 87% | $2.97 |
| P | per-decision | low | 83% | $1.50 |
| N | batched(5) | high | 50% | $0.89 |
| M | batched(5) | medium | 48% | $0.87 |

### Key findings

**Batching is the accuracy killer.** Comparing matched pairs:
- L (pd+high, 91%) vs N (batch+high, 50%): **-41pp** from batching
- O (pd+medium, 87%) vs M (batch+medium, 48%): **-39pp** from batching

**Thinking level has modest impact.** Comparing per-decision approaches:
- High → Medium (L→O): -4pp coverage, saves $0.32/game (10%)
- Medium → Low (O→P): -4pp coverage, saves $1.47/game (49%)
- High → Low (L→P): -8pp coverage, saves $1.79/game (54%)

**The `llmReasoning` field was also removed** from the annotation schema in Phase 2.
This field asked the model to explain why the LLM made the mistake. Removing it
saved ~20% of output tokens with no impact on annotation quality.

## Recommendation

**Use O (per-decision Sonnet 4.5 with medium thinking) for production blunder analysis.**

- 87% consensus coverage vs L's 91% — a modest 4pp trade
- $2.97/game vs $3.29/game — 10% cheaper
- If cost pressure increases, P (low thinking) at $1.50/game with 83% coverage
  is a viable fallback — 54% cheaper than L with only 8pp less coverage
- Batching should never be used — it halves accuracy regardless of thinking level
- Extended thinking on Sonnet remains the single biggest accuracy improvement
