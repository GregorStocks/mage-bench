import pytest

from puppeteer.replay import execute_replay_script


@pytest.mark.asyncio
async def test_execute_replay_script_skips_assert_action_steps():
    calls: list[tuple[str, dict]] = []

    async def fake_call_tool(name: str, arguments: dict) -> str:
        calls.append((name, dict(arguments)))
        return (
            '{"action_pending": true, "action_type": "GAME_GET_MULTI_AMOUNT", '
            '"response_type": "multi_amount", "message": "Assign damage"}'
        )

    prompt = await execute_replay_script(
        fake_call_tool,
        [
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "assert_action",
                "arguments": {"action_type": "GAME_GET_MULTI_AMOUNT", "response_type": "multi_amount"},
            },
        ],
        system_prompt="System prompt",
        skip_postscript=True,
    )

    assert calls == [("pass_priority", {})]
    tool_names = [tc["function"]["name"] for msg in prompt for tc in msg.get("tool_calls", [])]
    assert tool_names == ["pass_priority"]


@pytest.mark.asyncio
async def test_execute_replay_script_assert_action_raises_on_mismatch():
    async def fake_call_tool(_name: str, _arguments: dict) -> str:
        return '{"action_pending": true, "action_type": "GAME_SELECT", "message": "Select attackers"}'

    with pytest.raises(AssertionError, match="expected action_type='GAME_GET_MULTI_AMOUNT', got 'GAME_SELECT'"):
        await execute_replay_script(
            fake_call_tool,
            [
                {"name": "pass_priority", "arguments": {}},
                {"name": "assert_action", "arguments": {"action_type": "GAME_GET_MULTI_AMOUNT"}},
            ],
            system_prompt="System prompt",
            skip_postscript=True,
        )
