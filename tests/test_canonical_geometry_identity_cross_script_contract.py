import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _geometry(delta: float = 0.0):
    fields = (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "line_width_um",
        "primary_terminal_y_span_um",
        "secondary_terminal_y_span_um",
        "offset_um",
        "primary_feed_extension_um",
        "secondary_feed_extension_um",
    )
    return {field: 20.0 + index + delta for index, field in enumerate(fields)}


def test_entry_merge_and_final_audit_share_exact_canonical_geometry_identity():
    materializer = _load("materializer_geometry_contract", "materialize_physical_feature_targeted_s4p_queue.py")
    merger = _load("merger_geometry_contract", "merge_physical_feature_accepted_pool.py")
    final_audit = _load("final_audit_geometry_contract", "audit_accepted_1m_campaign_completion.py")

    assert materializer.GEOMETRY_FINGERPRINT_SCHEMA == merger.GEOMETRY_FINGERPRINT_SCHEMA
    assert materializer.GEOMETRY_FINGERPRINT_SCHEMA == final_audit.GEOMETRY_FINGERPRINT_SCHEMA
    assert materializer.DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM == merger.GEOMETRY_FINGERPRINT_QUANTIZATION_UM
    assert materializer.DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM == final_audit.GEOMETRY_FINGERPRINT_QUANTIZATION_UM
    assert materializer.CANONICAL_GEOMETRY_FIELDS == merger.CANONICAL_GEOMETRY_FIELDS
    assert materializer.CANONICAL_GEOMETRY_FIELDS == final_audit.CANONICAL_GEOMETRY_FIELDS

    unprefixed = _geometry()
    prefixed = {f"geom__{key}": value for key, value in unprefixed.items()}
    materialized_sha = materializer._geometry_fingerprint(
        unprefixed,
        materializer.DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
    )
    assert materialized_sha == merger._canonical_geometry_fingerprint(prefixed)
    assert materialized_sha == final_audit._canonical_geometry_fingerprint(prefixed)


def test_all_three_stages_treat_sub_quantum_near_duplicate_identically():
    materializer = _load("materializer_near_duplicate_contract", "materialize_physical_feature_targeted_s4p_queue.py")
    merger = _load("merger_near_duplicate_contract", "merge_physical_feature_accepted_pool.py")
    final_audit = _load("final_audit_near_duplicate_contract", "audit_accepted_1m_campaign_completion.py")

    fingerprints = []
    for delta in (0.0, 0.4e-6):
        unprefixed = _geometry(delta)
        prefixed = {f"geom__{key}": value for key, value in unprefixed.items()}
        fingerprints.append(
            (
                materializer._geometry_fingerprint(
                    unprefixed,
                    materializer.DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
                ),
                merger._canonical_geometry_fingerprint(prefixed),
                final_audit._canonical_geometry_fingerprint(prefixed),
            )
        )
    assert len(set(fingerprints[0])) == 1
    assert len(set(fingerprints[1])) == 1
    assert fingerprints[0][0] == fingerprints[1][0]
