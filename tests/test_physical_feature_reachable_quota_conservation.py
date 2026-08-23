import argparse
import importlib.util
import sys
from pathlib import Path


def _load_selector():
    path = Path(__file__).resolve().parents[1] / "scripts" / "select_physical_feature_targeted_candidate_geometries.py"
    spec = importlib.util.spec_from_file_location("reachable_quota_selector", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _targets(count: int = 3, request: int = 4) -> list[dict[str, str]]:
    return [
        {
            "rank": str(index + 1),
            "bin_key": f"bin-{index}",
            "recommended_new_samples": str(request),
        }
        for index in range(count)
    ]


def _args(*, max_total: int, max_per_target: int | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        redistribute_reachable_quota=True,
        reachable_targets_only=True,
        allow_outside_bin=False,
        max_total=max_total,
        max_per_target=max_per_target,
    )


def test_reachable_redistribution_conserves_full_requested_budget():
    module = _load_selector()
    targets = _targets()
    capacity = {module._target_key(target): 4 for target in targets}

    quotas = module._quota_by_target(targets, capacity, _args(max_total=12), 12)

    assert quotas == {("1", "bin-0"): 4, ("2", "bin-1"): 4, ("3", "bin-2"): 4}
    assert sum(quotas.values()) == 12


def test_reachable_redistribution_round_robins_integer_remainder_once():
    module = _load_selector()
    targets = _targets(request=10)
    capacity = {module._target_key(target): 10 for target in targets}

    quotas = module._quota_by_target(targets, capacity, _args(max_total=5), 5)

    assert quotas == {("1", "bin-0"): 2, ("2", "bin-1"): 2, ("3", "bin-2"): 1}
    assert sum(quotas.values()) == 5


def test_reachable_redistribution_respects_inside_candidate_capacity():
    module = _load_selector()
    targets = _targets(request=10)
    capacities = {
        module._target_key(targets[0]): 1,
        module._target_key(targets[1]): 2,
        module._target_key(targets[2]): 8,
    }

    quotas = module._quota_by_target(targets, capacities, _args(max_total=20), 20)

    assert quotas == capacities
    assert sum(quotas.values()) == 11
