"""One additional DOE006 fixture isolates width from adjacency validation."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from lineage_cross_binding_v1 import bind_cross_centers
from replay_lineage_v1 import pin

p=argparse.ArgumentParser();p.add_argument('--trace',type=Path,required=True)
p.add_argument('--capture',type=Path,required=True);p.add_argument('--repo',type=Path,required=True)
p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(a.repo.resolve()))
import gdstk
from rfic_transformer_inverse_design.layout import port_endpoint_construction as endpoint
assert pin(endpoint.__file__)['sha256']=='df712c44abb2113d6cced55653d8da169ecae74a7061b8dec756a4f73f09c151'
assert pin(a.trace)['sha256']=='2eeb762d3f212856fc5fbd7e46302de115670acc94e81aeb9f0c7091541114f8'
trace=json.loads(a.trace.read_bytes());capture=json.loads(a.capture.read_bytes())
after=deepcopy(trace['after'])
for idx in (13,14):after[2]['points'][idx][1]-=.005
c=gdstk.Cell('WIDTH_ONLY_SYNTHETIC_MUTATION')
for item in after:c.add(gdstk.Polygon(item['points'],layer=item['layer'],datatype=item['datatype']))
for x in capture['labels']:c.add(gdstk.Label(x['text'],x['origin'],layer=x['layer'],texttype=x['texttype']))
try:bind_cross_centers(cell=c,ports=capture['ports'],before=trace['before'],after=after,endpoint=endpoint)
except ValueError as e:error=str(e)
else:raise AssertionError('expected width guard did not reject')
assert error=='canonicalized face not exact intended width',error
report=dict(status='EXPECTED_REJECTION',fixture='ONE_GRID_WIDTH_CHANGE_WITH_AXIAL_ADJACENCY_RETAINED',
    error=error,synthetic_mutation_not_new_candidate=True,old_fixtures_rerun=0,
    gds_written=0,cadence_runs=0,calibre_runs=0,emx_runs=0,
    sources=[pin(__file__),pin(Path(__file__).with_name('lineage_cross_binding_v1.py')),pin(endpoint.__file__)],
    inputs=[pin(a.trace),pin(a.capture)])
with (a.out/'RECEIPT.json').open('x') as f:json.dump(report,f,indent=2)
print(json.dumps(pin(a.out/'RECEIPT.json')))
