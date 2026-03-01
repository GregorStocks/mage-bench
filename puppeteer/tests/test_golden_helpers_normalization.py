from tests.golden_helpers import (
    _is_short_id,
    _json_diff,
    _normalize_embedded_json,
    _normalize_prompt_for_golden,
    _strip_volatile,
)


def test_is_short_id_server_prefix():
    assert _is_short_id("p1")
    assert _is_short_id("p99")
    assert not _is_short_id("p")
    assert not _is_short_id("")
    assert not _is_short_id("x5")
    assert not _is_short_id(42)


def test_is_short_id_local_prefix():
    assert _is_short_id("l1")
    assert _is_short_id("l42")
    assert not _is_short_id("l")


def test_normalize_prompt_strips_local_short_ids():
    """Local-prefix short IDs (lN) should be normalized the same as server ones (pN)."""
    payload = [{"content": '{"id":"l5","name":"Lotus Petal"}'}]

    normalized = _normalize_prompt_for_golden(payload)
    assert normalized[0]["content"]["id"] == "_"


def test_normalize_embedded_json_sorts_keys():
    payload = {"result": '{"b":2,"a":1}'}

    normalized = _normalize_embedded_json(payload)
    assert normalized == {"result": {"a": 1, "b": 2}}


def test_normalize_embedded_json_preserves_short_ids():
    payload = {"result": '{"players":[{"id":"p3"},{"id":"p11"}]}'}

    normalized = _normalize_embedded_json(payload)
    assert normalized["result"] == {"players": [{"id": "p3"}, {"id": "p11"}]}


def test_normalize_embedded_json_handles_nested_json_strings():
    payload = {"result": '{"outer":"{\\"id\\":\\"p9\\",\\"k\\":2}","id":"p1"}'}

    normalized = _normalize_embedded_json(payload)

    assert normalized["result"]["id"] == "p1"
    assert normalized["result"]["outer"] == {"id": "p9", "k": 2}


def test_normalize_prompt_preserves_game_seq():
    payload = [{"content": '{"game_seq":77,"id":"p3","nested":{"game_seq":12}}'}]

    normalized = _normalize_prompt_for_golden(payload)

    assert normalized[0]["content"] == {"game_seq": 77, "id": "_", "nested": {"game_seq": 12}}


def test_strip_volatile_strips_ts_from_llm_events():
    data = {
        "llmEvents": [
            {"ts": "2025-01-01T00:00:00.000002", "player": "B", "seq": 2, "type": "llm_response"},
            {"ts": "2025-01-01T00:00:00.000001", "player": "A", "seq": 1, "type": "game_start"},
        ],
        "llmTrace": [
            {"ts": "2025-01-01T00:00:00.000001", "player": "B", "seq": 1},
            {"ts": "2025-01-01T00:00:00.000000", "player": "A", "seq": 2},
        ],
    }

    _strip_volatile(data)

    assert all("ts" not in e for e in data["llmEvents"])
    assert all("ts" not in e for e in data["llmTrace"])
    # Order is preserved, not re-sorted
    assert [e["seq"] for e in data["llmEvents"]] == [2, 1]
    assert [e["seq"] for e in data["llmTrace"]] == [1, 2]


# --- _json_diff tests ---


def test_json_diff_identical():
    assert _json_diff({"a": 1}, {"a": 1}) == []


def test_json_diff_scalar_change():
    diffs = _json_diff({"name": "Bolt"}, {"name": "Strike"})
    assert diffs == ["  name: 'Bolt' -> 'Strike'"]


def test_json_diff_dict_key_added():
    diffs = _json_diff({"a": 1}, {"a": 1, "b": 2})
    assert diffs == ["  b: + 2"]


def test_json_diff_dict_key_removed():
    diffs = _json_diff({"a": 1, "b": 2}, {"a": 1})
    assert diffs == ["  b: - 2"]


def test_json_diff_list_element_changed():
    diffs = _json_diff([1, 2, 3], [1, 99, 3])
    assert diffs == ["  [1]: 2 -> 99"]


def test_json_diff_list_element_added():
    diffs = _json_diff([1, 2], [1, 2, 3])
    assert len(diffs) == 2
    assert "2 items -> 3 items" in diffs[0]
    assert "[2]: + 3" in diffs[1]


def test_json_diff_list_element_removed():
    diffs = _json_diff([1, 2, 3], [1, 2])
    assert len(diffs) == 2
    assert "3 items -> 2 items" in diffs[0]
    assert "[2]: - 3" in diffs[1]


def test_json_diff_nested_path():
    exp = {"decisions": [{"choices": [{"name": "Bolt"}]}]}
    act = {"decisions": [{"choices": [{"name": "Strike"}]}]}
    diffs = _json_diff(exp, act)
    assert diffs == ["  decisions[0].choices[0].name: 'Bolt' -> 'Strike'"]


def test_json_diff_type_mismatch():
    diffs = _json_diff({"x": 1}, {"x": "one"})
    assert diffs == ["  x: 1 -> 'one'"]


def test_json_diff_truncates_long_strings():
    long_str = "a" * 200
    diffs = _json_diff({"s": long_str}, {"s": "short"})
    assert len(diffs) == 1
    # The long string repr should be truncated
    assert "..." in diffs[0]


def test_json_diff_max_diffs():
    exp = {f"k{i}": i for i in range(50)}
    act = {f"k{i}": i + 1 for i in range(50)}
    diffs = _json_diff(exp, act, max_diffs=5)
    # 5 actual diffs + 1 truncation message
    assert len(diffs) == 6
    assert "truncated" in diffs[-1]
