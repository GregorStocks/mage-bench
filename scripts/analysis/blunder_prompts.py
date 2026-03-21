"""Static prompt components for blunder analysis."""

from pathlib import Path

from scripts.json5_utils import loads_json5

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

BLUNDER_EXAMPLES = """\
## Examples of Blunders

Here are some examples of the kinds of mistakes to flag:

- Not attacking for lethal, missing combo kills, burn in hand at low life
- Casting spells that accomplish nothing, cards with no valid targets, declining pure-upside abilities
- Removing the wrong threat, fetching the wrong land, naming the wrong card
- Casting spells before playing lands, creatures before combat when holding tricks
- Poor attack/block decisions, attacking into unfavorable blocks
- Missing land drops, not using mana sinks at end of opponent's turn
- Fundamentally wrong game plan decisions, not countering must-answer threats
- Overextending into board wipes, running best threat into open counter mana
- Passing priority in the postcombat main phase (with nothing on the stack) when \
there are still sorcery-speed actions available this turn — e.g. unplayed land drops, \
castable creatures or sorceries in hand, planeswalker abilities to activate. Passing \
here ends the turn and wastes those opportunities. Note: the choices list shows the \
exact legal actions available — if a player passes (Chosen: False) with playable lands \
or castable spells among the choices, that is strong evidence of a blunder."""

SHARED_SEVERITY = """\
## Severity Levels

- **questionable**: Probably suboptimal but debatable. A human reviewing the game would \
find this interesting to think about. Use this when there's at least a ~30% chance the \
play was wrong. Low bar — when in doubt, include as questionable rather than omitting.
- **minor**: Clearly suboptimal — a small amount of value was lost (e.g. slightly wrong \
sequencing, fetching a less optimal land, missing a minor advantage).
- **moderate**: A real mistake with meaningful consequences — wasted a card, missed a \
significant line, or gave the opponent an unnecessary opening.
- **major**: Game-losing or close to it — threw away a winning position, wasted multiple \
cards for nothing, missed lethal, or made an error that directly led to losing."""

ANNOTATION_SCHEMA = """\
{
  "severity": "questionable" | "minor" | "moderate" | "major",
  "description": "<what went wrong in concrete game terms>",
  "actionTaken": "<what they actually did>",
  "betterLine": "<what they should have done>"
}"""

CHOSEN_FALSE_GUIDANCE = """\
## Understanding "Chosen: False"

"Chosen: False" means the player passed priority — they declined to act. \
If the stack is empty, passing means moving to the next phase (e.g. main phase \
to combat, or postcombat main to end step — ending the turn). If the stack has \
items, passing lets those items resolve without responding.

## Understanding "Chosen: (no response)"

"Chosen: (no response)" means the player failed to respond in time (timeout) \
or their client did not send a valid action. The game engine chose a default \
for them — typically passing or skipping. Treat this like "Chosen: False" \
for blunder evaluation: if skipping was wrong given the available choices, \
flag it.

## Understanding batch/text decisions

Some decisions (attack/block declarations, color choices) use batch or text \
parameters instead of selecting from a numbered list. These show as \
"Chosen: Attack with: ...", "Chosen: Block with: ...", or "Chosen: Text: ..." \
instead of a choice name. These are valid responses — the player DID act."""


def _build_tool_reference() -> str:
    """Build a tool reference section from the MCP tool spec for choose_action."""
    mcp_tools_path = REPO_ROOT / "website" / "src" / "data" / "mcp-tools.json5"
    mcp_tools = loads_json5(mcp_tools_path.read_text())
    tool = next((t for t in mcp_tools if t["name"] == "choose_action"), None)
    assert tool is not None, "choose_action not found in mcp-tools.json5"

    lines = [
        "## Tool Reference: choose_action",
        "",
        f"Players respond to each pending action by calling choose_action. {tool['description']}",
        "",
        "Parameters:",
    ]
    for name, schema in tool["inputSchema"]["properties"].items():
        desc = schema.get("description")
        type_ = schema.get("type")
        lines.append(f"- {name} ({type_ if type_ else ''}): {desc if desc else ''}")

    return "\n".join(lines)


TOOL_REFERENCE = _build_tool_reference()

PER_DECISION_SYSTEM = f"""\
You are a Magic: The Gathering expert evaluating a single decision from a game replay.

Analyze the decision below. If the play was reasonable, return null.
If it was a blunder, return a JSON annotation object.

Most decisions are reasonable — only flag clear mistakes or questionable choices.

You may be given prior context showing the board state from earlier and the action log \
since then. Use this to understand how the game reached the current state.

{BLUNDER_EXAMPLES}

{CHOSEN_FALSE_GUIDANCE}

{SHARED_SEVERITY}

## Output Format

Return ONLY valid JSON — either `null` (no blunder) or a single annotation object:
{ANNOTATION_SCHEMA}"""
