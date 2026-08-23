import rfic_transformer_inverse_design
import rfic_transformer_inverse_design.api as public_api


def test_canonical_root_imports_resolve_to_public_api():
    assert rfic_transformer_inverse_design.TransformerOptimizer is public_api.TransformerOptimizer
    assert rfic_transformer_inverse_design.TransformerEmxEvaluator is public_api.TransformerEmxEvaluator
    assert rfic_transformer_inverse_design.default_run_config is public_api.default_run_config
    assert rfic_transformer_inverse_design.load_run_config is public_api.load_run_config


def test_canonical_root_import_surface_stays_small():
    assert not hasattr(rfic_transformer_inverse_design, "score_transformer_result")
    assert not hasattr(rfic_transformer_inverse_design, "BridgeSectionConfig")
