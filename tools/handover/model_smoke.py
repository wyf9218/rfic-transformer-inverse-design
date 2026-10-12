#!/usr/bin/env python3
"""Load a named frozen bundle and infer ONE target. Not a scientific evaluation."""
import argparse
import json
from pathlib import Path
import numpy as np
from rfic_transformer_inverse_design.synthesis.model_adapter import load_model, describe_model


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets', type=Path, required=True)
    p.add_argument('--model', choices=('original100k', 'real10k', 'symmetric100k'), default='original100k')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    root = a.assets.resolve(strict=True)
    if a.out.exists(): raise FileExistsError('Use a NEW output JSON')
    profiles = {'original100k':'deployed100k', 'real10k':'real10k', 'symmetric100k':'custom'}
    model = load_model(root / 'models' / a.model, profile=profiles[a.model])
    target = np.array([[0.833, 0.620, 10.0, 0.487]])
    pred = model.predict(target)
    if pred.geometry.shape != (1,10) or pred.proxy_features.shape != (1,4):
        raise ValueError('Unexpected output shape')
    if not np.isfinite(pred.geometry).all() or not np.isfinite(pred.proxy_features).all():
        raise ValueError('Nonfinite prediction')
    result = {'status':'PASS_MODEL_LOAD_AND_ONE_INFERENCE', 'model':describe_model(model),
              'target_order':['Lp_nH','Ls_nH','Q','K_abs'], 'target':target[0].tolist(),
              'geometry_fields':model.contract['geometry_columns'], 'geometry_um':pred.geometry[0].tolist(),
              'proxy_features':pred.proxy_features[0].tolist(), 'evidence_class':'SELF_PROXY',
              'fresh_EMX':'NOT_RUN', 'DRC':'NOT_RUN', 'new_training':False,
              'boundary':'One installation smoke target; not accuracy, Q scanning, or native-tool validation.'}
    with a.out.open('x') as f: json.dump(result,f,indent=2,allow_nan=False); f.write('\n')
    print(json.dumps({'status':result['status'],'model_id':model.model_id,'out':str(a.out.resolve())}))


if __name__ == '__main__': main()
