"""Weird test: ratchet cross-module private Python helper imports."""

import ast
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUPPETEER_DIR = REPO_ROOT / "puppeteer"

_PRIVATE_IMPORT_SCAN_ROOTS = (
    REPO_ROOT / "puppeteer" / "src",
    REPO_ROOT / "scripts",
    REPO_ROOT / "schemas",
)

_ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS = {
    ("puppeteer.batch_coordination", "puppeteer.game_finalization", "_ensure_game_over_event"),
    ("puppeteer.batch_coordination", "puppeteer.game_finalization", "_git"),
    ("puppeteer.batch_coordination", "puppeteer.game_finalization", "_print_game_summary"),
    ("puppeteer.batch_coordination", "puppeteer.game_finalization", "_write_error_log"),
    ("puppeteer.batch_coordination", "puppeteer.game_finalization", "_write_game_meta"),
    ("puppeteer.batch_coordination", "puppeteer.game_processes", "_wait_for_game_start"),
    ("puppeteer.batch_coordination", "puppeteer.game_processes", "_wait_for_spectator_table"),
    ("puppeteer.deck_choice", "puppeteer.config", "_DECK_TYPE_TO_FORMAT_DIR"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_ELO_START"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_ModelEntry"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_exhibition_sort_key"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_player_key"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_rated_sort_key"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_serialize_model_entries"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_elo", "_split_key"),
    ("puppeteer.leaderboard", "puppeteer.leaderboard_registry", "_load_inactive_statuses"),
    ("puppeteer.leaderboard_stats", "puppeteer.leaderboard_elo", "_player_key"),
    ("puppeteer.leaderboard_stats", "puppeteer.leaderboard_elo", "_split_key"),
    ("puppeteer.orchestrator", "puppeteer.batch_coordination", "_finalize_game"),
    ("puppeteer.orchestrator", "puppeteer.batch_coordination", "_setup_game"),
    ("puppeteer.orchestrator", "puppeteer.batch_coordination", "_wait_for_all_games"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_ensure_game_over_event"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_git"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_print_game_summary"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_print_run_cost_summary"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_write_error_log"),
    ("puppeteer.orchestrator", "puppeteer.game_finalization", "_write_game_meta"),
    ("puppeteer.orchestrator", "puppeteer.game_processes", "_wait_for_game_start"),
    ("puppeteer.orchestrator", "puppeteer.game_processes", "_wait_for_spectator_table"),
    ("puppeteer.orchestrator", "puppeteer.game_processes", "_wait_with_pilot_monitoring"),
    ("puppeteer.orchestrator", "puppeteer.post_game_analysis", "_save_youtube_url"),
    ("puppeteer.orchestrator", "puppeteer.post_game_analysis", "_update_website_youtube_url"),
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_build_pilot_decision"),
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_build_pilot_snapshot"),
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_record_tool_execution_failure"),
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_tool_execution_error_result"),
    ("puppeteer.pilot", "puppeteer.pilot_game_state", "_extract_oracle_texts_from_board"),
    ("puppeteer.pilot", "puppeteer.pilot_game_state", "_normalize_context_token"),
    ("puppeteer.pilot", "puppeteer.pilot_game_state", "_parse_context_metadata"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_classify_permanent_llm_failure"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_handle_timeout"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_handle_truncated_response"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_recover_from_stall"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_build_reset_message"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_extract_last_reasoning"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_fetch_state_summary"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_find_cache_breakpoint_idx"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_find_tool_name"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_message_text"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_render_context"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_render_for_pilot"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_summarize_tool_result"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_with_cache_control"),
    ("puppeteer.pilot", "puppeteer.pilot_state", "_reset_context"),
    ("puppeteer.pilot_bridge", "puppeteer.pilot_game_state", "_parse_context_metadata"),
    ("puppeteer.pilot_recovery", "puppeteer.pilot_state", "_reset_context"),
    ("puppeteer.pilot_rendering", "puppeteer.pilot_bridge", "_build_pilot_decision"),
    ("puppeteer.pilot_rendering", "puppeteer.pilot_bridge", "_build_pilot_snapshot"),
    ("puppeteer.pilot_rendering", "puppeteer.pilot_game_state", "_extract_oracle_texts_from_board"),
    ("puppeteer.pilot_state", "puppeteer.pilot_rendering", "_build_reset_message"),
    ("puppeteer.pilot_state", "puppeteer.pilot_rendering", "_extract_last_reasoning"),
    ("puppeteer.replay", "puppeteer.pilot", "_render_context"),
    ("puppeteer.replay", "puppeteer.pilot", "_render_for_pilot"),
    ("schemas.migrations.v2_to_v3", "scripts.export_card_data", "_build_card_data"),
    ("schemas.migrations.v7_to_v8", "schemas.game_export_types", "_coerce_snapshot"),
    ("scripts.analysis.blunder_analysis", "puppeteer.decision_renderer", "_chosen_display"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_actions_by_turn"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_collect_card_names"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_format_current_turn_actions"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_format_prior_context"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_game_overview"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_context", "_get_oracle_texts"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_llm", "_LLM_REQUIRED_FIELDS"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_llm", "_call_llm"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_llm", "_compute_cost"),
    ("scripts.analysis.blunder_analysis", "scripts.analysis.blunder_llm", "_parse_annotation"),
    ("scripts.analysis.blunder_audit", "scripts.analysis.blunder_analysis", "_eval_one_decision"),
    ("scripts.analysis.blunder_audit_web", "scripts.analysis.blunder_audit", "_get_current_annotation"),
    ("scripts.analysis.blunder_eval", "scripts.analysis.blunder_analysis", "_eval_one_decision"),
    ("scripts.analysis.toolbox.blunder_experiment", "puppeteer.decision_renderer", "_chosen_display"),
    ("scripts.analysis.toolbox.blunder_experiment", "puppeteer.decision_renderer", "_format_choice"),
    ("scripts.analysis.toolbox.blunder_experiment", "scripts.analysis.blunder_analysis", "_game_overview"),
    ("scripts.analysis.toolbox.blunder_experiment", "scripts.analysis.blunder_analysis", "_load_game"),
    ("scripts.analysis.toolbox.dump_sample_prompt", "scripts.analysis.blunder_analysis", "_actions_by_turn"),
    ("scripts.analysis.toolbox.dump_sample_prompt", "scripts.analysis.blunder_analysis", "_collect_card_names"),
    ("scripts.analysis.toolbox.dump_sample_prompt", "scripts.analysis.blunder_analysis", "_game_overview"),
    ("scripts.analysis.toolbox.dump_sample_prompt", "scripts.analysis.blunder_analysis", "_get_oracle_texts"),
    ("scripts.backfill_annotation_snapshots", "scripts.export_decisions", "_build_decisions"),
    ("scripts.backfill_decisions", "scripts.export_decisions", "_build_decisions"),
    ("scripts.export_game", "scripts.export_card_data", "_build_card_data"),
    ("scripts.export_game", "scripts.export_card_data", "_build_card_images"),
    ("scripts.export_game", "scripts.export_decisions", "_build_decisions"),
    ("scripts.export_game", "scripts.export_errors", "_link_errors_to_decisions"),
    ("scripts.export_game", "scripts.export_errors", "_read_errors"),
    ("scripts.export_game", "scripts.export_llm_events", "_read_llm_events"),
    ("scripts.tournament_game", "puppeteer.config", "_generate_player_name"),
}

_ALLOWED_PRIVATE_REEXPORTS = {
    ("puppeteer.leaderboard", "_player_key"),
    ("puppeteer.leaderboard", "_split_key"),
    ("puppeteer.orchestrator", "_check_regular_season_block"),
    ("puppeteer.orchestrator", "_ensure_game_over_event"),
    ("puppeteer.orchestrator", "_finalize_game"),
    ("puppeteer.orchestrator", "_git"),
    ("puppeteer.orchestrator", "_missing_llm_api_keys"),
    ("puppeteer.orchestrator", "_print_game_summary"),
    ("puppeteer.orchestrator", "_save_youtube_url"),
    ("puppeteer.orchestrator", "_setup_game"),
    ("puppeteer.orchestrator", "_update_website_youtube_url"),
    ("puppeteer.orchestrator", "_wait_for_all_games"),
    ("puppeteer.orchestrator", "_wait_for_game_start"),
    ("puppeteer.orchestrator", "_wait_for_spectator_table"),
    ("puppeteer.orchestrator", "_wait_with_pilot_monitoring"),
    ("puppeteer.orchestrator", "_write_error_log"),
    ("puppeteer.orchestrator", "_write_game_meta"),
    ("puppeteer.pilot", "_build_loop_messages"),
    ("puppeteer.pilot", "_build_pilot_decision"),
    ("puppeteer.pilot", "_build_pilot_snapshot"),
    ("puppeteer.pilot", "_build_reset_message"),
    ("puppeteer.pilot", "_classify_permanent_llm_failure"),
    ("puppeteer.pilot", "_extract_last_reasoning"),
    ("puppeteer.pilot", "_extract_oracle_texts_from_board"),
    ("puppeteer.pilot", "_fetch_state_summary"),
    ("puppeteer.pilot", "_find_cache_breakpoint_idx"),
    ("puppeteer.pilot", "_find_tool_name"),
    ("puppeteer.pilot", "_handle_timeout"),
    ("puppeteer.pilot", "_handle_truncated_response"),
    ("puppeteer.pilot", "_mark_tail_cache_breakpoint"),
    ("puppeteer.pilot", "_message_text"),
    ("puppeteer.pilot", "_normalize_context_token"),
    ("puppeteer.pilot", "_parse_context_metadata"),
    ("puppeteer.pilot", "_prefetch_first_action"),
    ("puppeteer.pilot", "_record_tool_execution_failure"),
    ("puppeteer.pilot", "_recover_from_stall"),
    ("puppeteer.pilot", "_render_context"),
    ("puppeteer.pilot", "_render_for_pilot"),
    ("puppeteer.pilot", "_summarize_tool_result"),
    ("puppeteer.pilot", "_tool_execution_error_result"),
    ("puppeteer.pilot", "_with_cache_control"),
}


def _module_name_for_path(path: Path) -> str:
    if path.is_relative_to(PUPPETEER_DIR / "src"):
        rel = path.relative_to(PUPPETEER_DIR / "src")
    else:
        rel = path.relative_to(REPO_ROOT)
    return ".".join(rel.with_suffix("").parts)


@cache
def _repo_python_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in _PRIVATE_IMPORT_SCAN_ROOTS:
        for path in root.rglob("*.py"):
            modules[_module_name_for_path(path)] = path
    return modules


def _resolve_imported_module(importer_module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    importer_package = importer_module.split(".")[:-1]
    levels_up = node.level - 1
    if levels_up > len(importer_package):
        return None
    base_parts = importer_package[: len(importer_package) - levels_up]
    if node.module is None:
        return ".".join(base_parts)
    return ".".join(base_parts + node.module.split("."))


@cache
def _private_cross_module_imports() -> frozenset[tuple[str, str, str]]:
    modules = _repo_python_modules()
    private_imports: set[tuple[str, str, str]] = set()

    for importer_module, path in modules.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            exporter_module = _resolve_imported_module(importer_module, node)
            if exporter_module is None or exporter_module not in modules:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and exporter_module != importer_module:
                    private_imports.add((importer_module, exporter_module, alias.name))

    return frozenset(private_imports)


@cache
def _private_reexports() -> frozenset[tuple[str, str]]:
    reexports: set[tuple[str, str]] = set()

    for module_name, path in _repo_python_modules().items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                continue
            try:
                exported_names = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            if not isinstance(exported_names, list | tuple):
                continue
            for name in exported_names:
                if isinstance(name, str) and name.startswith("_"):
                    reexports.add((module_name, name))

    return frozenset(reexports)


class TestPrivatePythonApis:
    def test_no_new_cross_module_private_imports(self) -> None:
        unexpected = _private_cross_module_imports() - _ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS
        assert not unexpected, (
            "New cross-module imports of underscore-prefixed helpers were added.\n"
            "If another module needs the helper, rename it to a public symbol in the owner module instead.\n  "
            + "\n  ".join(f"{importer} imports {exporter}.{name}" for importer, exporter, name in sorted(unexpected))
        )

    def test_no_new_private_reexports(self) -> None:
        unexpected = _private_reexports() - _ALLOWED_PRIVATE_REEXPORTS
        assert not unexpected, (
            "New underscore-prefixed names were added to __all__.\n"
            "Private helpers should not be part of a module's public export surface.\n  "
            + "\n  ".join(f"{module}.{name}" for module, name in sorted(unexpected))
        )
