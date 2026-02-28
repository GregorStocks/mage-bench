from tests.golden_helpers import _is_short_id, _normalize_embedded_json, _normalize_prompt_for_golden, _strip_volatile


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


def test_strip_volatile_sorts_llm_events_by_seq_player():
    data = {
        "llmEvents": [
            {"ts": "2025-01-01T00:00:00.000002", "player": "B", "seq": 2, "type": "llm_response"},
            {"ts": "2025-01-01T00:00:00.000003", "player": "A", "seq": 2, "type": "llm_response"},
            {"ts": "2025-01-01T00:00:00.000001", "player": "A", "seq": 1, "type": "game_start"},
            {"ts": "2025-01-01T00:00:00.000000", "player": "B", "seq": 1, "type": "game_start"},
        ],
        "llmTrace": [
            {"ts": "2025-01-01T00:00:00.000001", "player": "B", "seq": 1},
            {"ts": "2025-01-01T00:00:00.000000", "player": "A", "seq": 1},
        ],
    }

    _strip_volatile(data)

    # Sorted by (seq, player); ts stripped before sorting (it's volatile)
    events = data["llmEvents"]
    assert all("ts" not in e for e in events)
    assert [(e["player"], e["seq"]) for e in events] == [
        ("A", 1),
        ("B", 1),
        ("A", 2),
        ("B", 2),
    ]

    trace = data["llmTrace"]
    assert all("ts" not in e for e in trace)
    assert [(e["player"], e["seq"]) for e in trace] == [("A", 1), ("B", 1)]
