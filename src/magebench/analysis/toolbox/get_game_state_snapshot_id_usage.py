"""Analyze whether exported LLM tool calls actually reuse get_game_state tokens.

Usage:
    uv run python -m magebench.analysis.toolbox.get_game_state_snapshot_id_usage
    uv run python -m magebench.analysis.toolbox.get_game_state_snapshot_id_usage website/public/games
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pyjson5

from magebench.common.json5_utils import loads_json5
from magebench.game.game_exports import GAMES_DIR, glob_game_export_paths, load_raw_game_export


@dataclass(slots=True)
class SnapshotUsageExample:
    game_id: str
    player: str
    model: str | None
    arguments: dict[str, object]


@dataclass(slots=True)
class SnapshotUsageReport:
    files_scanned: int = 0
    llm_response_events_scanned: int = 0
    get_game_state_calls: int = 0
    get_game_state_calls_with_any_args: int = 0
    get_game_state_calls_with_cursor: int = 0
    get_game_state_calls_with_snapshot_id: int = 0
    parse_failures: int = 0
    argument_key_counts: Counter[str] = field(default_factory=Counter)
    examples_with_args: list[SnapshotUsageExample] = field(default_factory=list)


def analyze_games(games_dir: Path) -> SnapshotUsageReport:
    report = SnapshotUsageReport()
    for export_path in glob_game_export_paths(games_dir):
        report.files_scanned += 1
        export = load_raw_game_export(export_path)
        game_id = str(export.get("id", export_path.stem))
        model_by_player = {
            str(player.get("name")): _optional_str(player.get("model"))
            for player in _require_list(export.get("players"), f"{export_path}: players")
            if isinstance(player, dict)
        }
        for event in _require_list(export.get("llm_events"), f"{export_path}: llm_events"):
            if not isinstance(event, dict) or event.get("type") != "llm_response":
                continue
            report.llm_response_events_scanned += 1
            player_name = str(event.get("player", "?"))
            tool_calls = event.get("tool_calls")
            if tool_calls is None:
                continue
            for tool_call in _require_list(tool_calls, f"{export_path}: llm_response.tool_calls"):
                if not isinstance(tool_call, dict) or tool_call.get("name") != "get_game_state":
                    continue
                report.get_game_state_calls += 1
                raw_arguments = tool_call.get("arguments")
                arguments = _parse_tool_arguments(raw_arguments)
                if arguments is None:
                    report.parse_failures += 1
                    continue
                if arguments:
                    report.get_game_state_calls_with_any_args += 1
                    if len(report.examples_with_args) < 10:
                        report.examples_with_args.append(
                            SnapshotUsageExample(
                                game_id=game_id,
                                player=player_name,
                                model=model_by_player.get(player_name),
                                arguments=dict(arguments),
                            )
                        )
                for key in arguments:
                    report.argument_key_counts[key] += 1
                if "cursor" in arguments:
                    report.get_game_state_calls_with_cursor += 1
                if "snapshot_id" in arguments:
                    report.get_game_state_calls_with_snapshot_id += 1
    return report


def _parse_tool_arguments(raw_arguments: object) -> dict[str, object] | None:
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return None
    if raw_arguments.strip() == "":
        return {}
    try:
        parsed = loads_json5(raw_arguments)
    except pyjson5.Json5DecoderException:
        return None
    if parsed is None:
        return {}
    return parsed if isinstance(parsed, dict) else None


def _require_list(value: object, message: str) -> list[object]:
    assert isinstance(value, list), message
    return value


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _format_report(report: SnapshotUsageReport) -> str:
    lines = [
        f"files_scanned: {report.files_scanned}",
        f"llm_response_events_scanned: {report.llm_response_events_scanned}",
        f"get_game_state_calls: {report.get_game_state_calls}",
        f"get_game_state_calls_with_any_args: {report.get_game_state_calls_with_any_args}",
        f"get_game_state_calls_with_cursor: {report.get_game_state_calls_with_cursor}",
        f"get_game_state_calls_with_snapshot_id: {report.get_game_state_calls_with_snapshot_id}",
        f"parse_failures: {report.parse_failures}",
    ]
    if report.argument_key_counts:
        lines.append("argument_keys:")
        for key, count in report.argument_key_counts.most_common():
            lines.append(f"  {key}: {count}")
    if report.examples_with_args:
        lines.append("examples_with_args:")
        for example in report.examples_with_args:
            model_suffix = f" model={example.model}" if example.model is not None else ""
            lines.append(f"  {example.game_id} player={example.player}{model_suffix} args={example.arguments}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(f"Usage: {argv[0]} [games_dir]", file=sys.stderr)
        return 2
    games_dir = Path(argv[1]) if len(argv) == 2 else GAMES_DIR
    report = analyze_games(games_dir)
    print(_format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
