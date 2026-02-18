"""Golden file tests for the exact JSON payload sent to the LLM API.

Captures the wire-format request body (messages + tools) for each scenario.
Tool result content fields are JSON strings with escaped quotes — that's
what the API actually receives.  How the provider converts this into the
token sequence the model processes is their implementation detail.

See doc/golden-prompts.md for architecture and rationale.

To update golden files:  make update-golden
To verify:               make test-golden
"""

import json
import os
from pathlib import Path

from puppeteer.pilot import _render_context

# Paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
JAVA_FIXTURES_DIR = REPO_ROOT / "Mage.Tests" / "src" / "test" / "resources" / "golden" / "mcp"
PROMPTS_JSON = REPO_ROOT / "puppeteer" / "prompts.json"
TOOLSETS_JSON = REPO_ROOT / "puppeteer" / "toolsets.json"
MCP_TOOLS_JSON = REPO_ROOT / "website" / "src" / "data" / "mcp-tools.json"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"

UPDATE_MODE = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")


def _load_system_prompt() -> str:
    prompts = json.loads(PROMPTS_JSON.read_text())
    return prompts["default"]


def _load_tools_openai() -> list[dict]:
    """Load MCP tool definitions and convert to OpenAI function-calling format."""
    mcp_tools = json.loads(MCP_TOOLS_JSON.read_text())
    toolset = json.loads(TOOLSETS_JSON.read_text())["default"]
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
            },
        }
        for tool in mcp_tools
        if tool["name"] in toolset
    ]


def _load_java_fixture(name: str) -> dict:
    """Load a Java fixture file (pass_priority_result + game_state)."""
    path = JAVA_FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def _build_initial_message(pass_priority_result: dict) -> str:
    """Replicate _prefetch_first_action's message formatting logic."""
    if not pass_priority_result.get("action_pending"):
        return "The game is starting. Call pass_priority to get your first decision."

    action_type = pass_priority_result.get("action_type", "")
    message = pass_priority_result.get("message", "")

    if "Mulligan" in message or "mulligan" in message.lower():
        return (
            f"The game is starting. Your first decision: {message}\n"
            f"Call get_action_choices to see your hand, then choose_action to decide."
        )
    elif action_type:
        return (
            f"The game is starting. Your first decision ({action_type}): {message}\n"
            f"Call get_action_choices to see your options, then choose_action to decide."
        )
    else:
        return "The game is starting. Call pass_priority to get your first decision."


def _resolve_tool_result(tool_name: str, fixture: dict) -> str:
    """Look up what an MCP tool would return from the Java fixture data."""
    if tool_name == "get_game_state":
        return json.dumps(fixture["game_state"])
    elif tool_name == "pass_priority":
        return json.dumps(fixture["pass_priority_result"])
    else:
        raise ValueError(
            f"Unsupported tool in scenario script: {tool_name!r}. Fixture only has pass_priority_result and game_state."
        )


def _assemble_prompt(
    fixture_name: str,
    ai_tool_calls: list[dict],
) -> dict:
    """Assemble the full LLM prompt for a scenario.

    Args:
        fixture_name: Name of the Java fixture file (without .json).
        ai_tool_calls: Sequence of tool calls the AI makes before the
            decision point. Each is {"name": "tool_name", "arguments": {...}}.
            Tool results are resolved from the Java fixture.

    Returns a dict with:
      - messages: the complete messages array sent to the LLM API
      - tools: the tool definitions in OpenAI format
    """
    fixture = _load_java_fixture(fixture_name)
    system_prompt = _load_system_prompt()
    tools = _load_tools_openai()

    pass_priority_result = fixture["pass_priority_result"]

    # Build the initial user message (same logic as _prefetch_first_action)
    initial_message = _build_initial_message(pass_priority_result)

    # Build conversation history with scripted tool calls
    history: list[dict] = [{"role": "user", "content": initial_message}]

    for i, call in enumerate(ai_tool_calls):
        tool_call_id = f"call_{i + 1}"

        # Assistant message with tool call (matches pilot.py format, lines 561-573)
        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("arguments", {})),
                        },
                    }
                ],
            }
        )

        # Tool result message (matches pilot.py format, lines 700-706)
        result_content = _resolve_tool_result(call["name"], fixture)
        history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_content,
            }
        )

    # Assemble the messages using the real _render_context
    messages = _render_context(history, system_prompt, state_summary="")

    return {
        "messages": messages,
        "tools": tools,
    }


def _to_sorted_json(obj: object) -> str:
    """Deterministic JSON serialization with sorted keys."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def _assert_golden(name: str, actual_json: str) -> None:
    golden_file = GOLDEN_DIR / f"{name}.json"

    if UPDATE_MODE:
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(actual_json + "\n")
        print(f"Updated golden file: {golden_file}")
        return

    assert golden_file.exists(), f"Golden file not found: {golden_file}\nRun 'make update-golden' to generate it."

    expected = golden_file.read_text().rstrip()
    if expected != actual_json:
        expected_lines = expected.split("\n")
        actual_lines = actual_json.split("\n")
        diffs = []
        max_lines = max(len(expected_lines), len(actual_lines))
        for i in range(max_lines):
            exp = expected_lines[i] if i < len(expected_lines) else "<missing>"
            act = actual_lines[i] if i < len(actual_lines) else "<missing>"
            if exp != act:
                diffs.append(f"  Line {i + 1}:\n    expected: {exp}\n    actual:   {act}")
        diff_text = "\n".join(diffs[:20])
        raise AssertionError(
            f"Golden file mismatch: {name}.json\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
        )


# ========== Test Cases ==========
#
# Each test defines a scenario: a Java fixture + scripted AI tool calls.
# The golden file captures the complete prompt (messages + tools) that
# the LLM would see right before making its decision.


def test_mulligan_seven_mountains():
    """Mulligan with 7 Mountains — AI checks game state before deciding."""
    prompt = _assemble_prompt(
        fixture_name="mulligan_seven_mountains",
        ai_tool_calls=[
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    _assert_golden("mulligan_seven_mountains", _to_sorted_json(prompt))


def test_play_or_draw():
    """Play or draw decision — AI checks game state before choosing."""
    prompt = _assemble_prompt(
        fixture_name="play_or_draw",
        ai_tool_calls=[
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    _assert_golden("play_or_draw", _to_sorted_json(prompt))


def test_t2_bolt_on_stack():
    """Turn 2 with Lightning Bolt on stack — AI checks game state."""
    prompt = _assemble_prompt(
        fixture_name="t2_bolt_on_stack",
        ai_tool_calls=[
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    _assert_golden("t2_bolt_on_stack", _to_sorted_json(prompt))
