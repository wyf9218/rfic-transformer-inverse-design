"""Optional research-only pre-test target freeze, never a training controller."""
from pathlib import Path

from .io import read_json
from .frequency_evaluation import verify_pin


def prepare_requested_audit(study, pair, profile_path):
    """Called before any posttrain validation/test scoring for a new pair.

    A flat local request enables this user-authorized evaluation supplement.
    No scientific fields in the existing training request are modified.
    """
    study = Path(study)
    request_path = study / 'LARGE_EVALUATION_REQUEST.json'
    if not request_path.exists():
        return None
    request = read_json(request_path)
    allowed = {'schema', 'study_id', 'dataset_scope', 'sampling_seed',
               'emx_selection_seed', 'random_count', 'emx_per_panel', 'batch_size'}
    if set(request) != allowed or request['schema'] != 'frequency_pretest_audit_request.v1':
        raise ValueError('invalid pre-test audit request')
    if (study / 'posttrain/evaluation/test/EVALUATION_SUMMARY.json').exists():
        # Existing prepared targets may be reused after the later test finishes;
        # never manufacture a prospective freeze after old scoring.
        if not (study / 'large_scale_eval_v1/CONFIGURATION_FREEZE.json').exists():
            raise ValueError('prospective audit targets were not frozen before test')
    from .frequency_large_eval import prepare
    config = {k: v for k, v in request.items() if k != 'schema'}
    config.update(schema='frequency_large_eval_request.v1',
                  data_root=pair['data_root'],
                  forward_checkpoint=pair['roles']['forward']['best']['path'],
                  inverse_checkpoint=pair['roles']['inverse']['best']['path'],
                  frequency_ghz=pair['frequency_ghz'],
                  label_mode=pair['label_mode'],
                  profile_json=str(profile_path), device='cpu')
    output = study / 'large_scale_eval_v1'
    freeze = output / 'CONFIGURATION_FREEZE.json'
    if freeze.exists():
        existing = read_json(freeze)
        if existing.get('config') != config or existing.get('status') != 'FROZEN_BEFORE_NEW_SCORING':
            raise ValueError('existing prospective target configuration differs')
        for item in existing['artifacts'].values():
            verify_pin(item)
        for name in ('forward_checkpoint', 'inverse_checkpoint', 'dataset', 'data_manifest', 'splits'):
            verify_pin(existing['identity'][name])
        return existing
    return prepare(config, output)
