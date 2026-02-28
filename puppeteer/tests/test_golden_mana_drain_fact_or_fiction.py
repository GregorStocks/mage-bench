"""Golden prompt test: Mana Drain into Fact or Fiction."""

import pytest

from tests.golden_helpers import assert_golden_export, assert_golden_prompt
from tests.golden_scenarios import get_scenario


@pytest.mark.golden
def test_mana_drain_into_fact_or_fiction(parallel_subprocess_results):
    """Mana Drain counters Savannah Lions, then Fact or Fiction off the mana."""
    scenario = get_scenario("mana_drain_fact_or_fiction")
    result = parallel_subprocess_results[scenario.golden_name]
    if result.error:
        raise result.error
    assert_golden_prompt(scenario.golden_name, result.prompt, name_map=scenario.name_map)
    assert_golden_export(scenario.golden_name, result.game_dir, name_map=scenario.name_map)
