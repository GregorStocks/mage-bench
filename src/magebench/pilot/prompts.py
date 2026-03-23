"""Prompt-loading helpers for pilot-facing configuration."""

from pathlib import Path

from magebench.common.json5_utils import loads_json5


def _load_json_file(name: str, config_file: Path | None) -> dict[str, str]:
    """Load a JSON/JSON5 object by name from the config dir or repo defaults."""
    candidates: list[Path] = []
    if config_file is not None:
        candidates.append(config_file.parent / name)
    candidates.append(Path("puppeteer") / name)

    for candidate in candidates:
        if candidate.exists():
            data = loads_json5(candidate.read_text())
            assert isinstance(data, dict), f"{candidate}: expected JSON object"
            typed_prompts: dict[str, str] = {}
            for key, value in data.items():
                assert isinstance(key, str), f"{candidate}: prompt key must be a string, got {key!r}"
                assert isinstance(value, str), f"{candidate}: prompt {key!r} must be a string, got {value!r}"
                typed_prompts[key] = value
            return typed_prompts
    return {}


def load_prompts(config_file: Path | None) -> dict[str, str]:
    """Load prompt definitions from prompts/ directories plus prompts.json."""
    result: dict[str, str] = {}

    prompt_dirs: list[Path] = []
    if config_file is not None:
        prompt_dirs.append(config_file.parent / "prompts")
    prompt_dirs.append(Path("puppeteer") / "prompts")

    for prompt_dir in prompt_dirs:
        if prompt_dir.is_dir():
            for md_file in sorted(prompt_dir.glob("*.md")):
                key = md_file.stem
                if key not in result:
                    result[key] = md_file.read_text().strip()

    result.update(_load_json_file("prompts.json", config_file))
    return result
