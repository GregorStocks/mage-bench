"""Golden prompt test: Savannah Lions trade in combat."""

import pytest

from tests.golden_helpers import assert_golden_export, assert_golden_prompt
from tests.golden_scenarios import get_scenario


@pytest.mark.golden
def test_savannah_lions_trade(parallel_subprocess_results):
    """Both players play Savannah Lions, P1 attacks T2, P2 blocks, both die."""
    scenario = get_scenario("savannah_lions_trade")
    result = parallel_subprocess_results[scenario.golden_name]
    if result.error:
        raise result.error
    assert_golden_prompt(scenario.golden_name, result.prompt, name_map=scenario.name_map)
    assert_golden_export(scenario.golden_name, result.game_dir, name_map=scenario.name_map)
