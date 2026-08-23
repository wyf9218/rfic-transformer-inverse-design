from tests.rfic_transformer_inverse_design.shared import *

import importlib.util
import sys


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "stage_physical_feature_acquisition_mix_contract.py"
    spec = importlib.util.spec_from_file_location("stage_acquisition_mix_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _recommendation(path: Path) -> None:
    counts = {
        "coarse_4d": 35704,
        "rare_marginal": 35712,
        "pairwise_gap": 24584,
        "random_exploration": 12000,
        "geometry_diversity": 12000,
    }
    path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "outcome_status": "PROPOSAL_ONLY_NOT_DEPLOYED",
                "queue_count": 120000,
                "recommended_mix": {
                    "counts": counts,
                    "fractions": {key: value / 120000 for key, value in counts.items()},
                },
                "production_mapping": {"automatic_command_authorized": False},
                "uniformity_summary": {"sha256": "real-label-audit"},
            }
        ),
        encoding="utf-8",
    )


def test_staged_contract_cannot_launch_automatically(tmp_path):
    module = _load_module()
    recommendation = tmp_path / "recommendation.json"
    output = tmp_path / "contract.json"
    _recommendation(recommendation)

    assert module.main(["--recommendation-summary", str(recommendation), "--output", str(output)]) == 0

    payload = json.loads(output.read_text())
    assert payload["overall_status"] == "PASS"
    assert payload["automatic_command_authorized"] is False
    assert payload["authorization_status"] == "STAGED_AWAITING_RESOURCE_RELEASE_AND_FINAL_PREFLIGHT"
    assert sum(payload["production_acquisition_mix"]["counts"].values()) == 120000


def test_authorization_requires_preflight_and_explicit_post_tapeout_release(tmp_path):
    module = _load_module()
    recommendation = tmp_path / "recommendation.json"
    output = tmp_path / "contract.json"
    preflight = tmp_path / "preflight.json"
    release = tmp_path / "release.json"
    _recommendation(recommendation)
    preflight.write_text(json.dumps({"overall_status": "PASS", "queue_count": 120000, "jobs": 48}))
    release.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "tapeout_resource_window_released": True,
                "approved_by": "resource-owner",
                "approved_utc": "2026-07-16T12:00:00Z",
            }
        )
    )

    assert module.main(
        [
            "--recommendation-summary", str(recommendation),
            "--output", str(output),
            "--controller-preflight-summary", str(preflight),
            "--resource-release-json", str(release),
            "--authorize",
        ]
    ) == 0
    payload = json.loads(output.read_text())
    assert payload["automatic_command_authorized"] is True
    assert payload["authorization_status"] == "AUTHORIZED_FOR_CONTROLLER_PREFLIGHT"

    release_data = json.loads(release.read_text())
    release_data["approved_utc"] = "2026-07-15T23:59:59Z"
    release.write_text(json.dumps(release_data))
    assert module.main(
        [
            "--recommendation-summary", str(recommendation),
            "--output", str(output),
            "--controller-preflight-summary", str(preflight),
            "--resource-release-json", str(release),
            "--authorize", "--no-fail-exit",
        ]
    ) == 0
    payload = json.loads(output.read_text())
    assert payload["overall_status"] == "FAIL"
    assert payload["automatic_command_authorized"] is False
    assert payload["authorization_checks"]["resource_release_not_before_july_16"] is False
