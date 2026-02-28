"""Golden prompt test: Lightning Bolt with stack_resolved yield."""

import pytest

from tests.golden_helpers import assert_golden_export, assert_golden_prompt
from tests.golden_scenarios import get_scenario


@pytest.mark.golden
def test_stack_resolved(parallel_subprocess_results):
    """Cast Lightning Bolt, then pass_priority(until="stack_resolved") to let it resolve."""
    scenario = get_scenario("stack_resolved")
    result = parallel_subprocess_results[scenario.golden_name]
    if result.error:
        raise result.error
    assert_golden_prompt(scenario.golden_name, result.prompt, name_map=scenario.name_map)
    assert_golden_export(scenario.golden_name, result.game_dir, name_map=scenario.name_map)
