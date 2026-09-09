"""Copy two exact approved aggregate CSVs; no scientific aggregation or runtime imports."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re

E = 'reports/eucap15ghz_20260908T220300Z/'
C = E + 'formal6329_capacity_20260909_v1/'
S = E + 'acquisition_prefix16_statistics_20260909_v1/'
O = E + 'original64_terminal_statistics_20260909_v1/'
SOURCES = [
    (C+'compact_comparison_v1/run_v1/COMPARISON_5ROW.csv','35253ece9c4b83f8f2a17988e99c2a5b7ad65efd0173273feb8f2181734cd188'),
    (C+'compact_comparison_v1/run_v1/DELIVERY_RECEIPT.json','658cdfa49de317eca4038d0b28390b892c040aba2a90fbe91083d4375572e0d9'),
    (C+'independent_compact_qa_v1/run_v1/INDEPENDENT_COMPACT_QA.json','da062b9d5dbb45140fd64fa129c901fe3af330a6a7e975fc5e4ebc152357f9e6'),
    (C+'independent_tables_qa_v1/label_correction_v1/INDEPENDENT_TABLES_QA.json','243416f9a385b2ace7ec854400d0a00ebfddabdc6beeb4f40f80062fe7886595'),
    (C+'RESULTS_CN.md','3cff44d0632cd12b45c20b4f47c1806d2513db06bcbd1a1c191e3a8658151bc4'),
    (C+'tables_v1_input/CUMULATIVE_TRAINING_COST.json','c30342300fffe057936f3bfe3af7339955a5c27c9f728bc4c08098684c215272'),
    (C+'tables_v1/METHODS_AND_CAPTIONS.json','eb5b3abebffac94bcecb0204124465bf503b190f44136934127f2c11bfbe3bc2'),
    (E+'formal6329_development_20260909_v1/data_v2/DATA_RECEIPT.json','3068be3015b85aeb6e4fdc9cab00cc94fa52c6392dd1d15195dfa9aff577f532'),
    (S+'run_v2/MATCHED16_COMPARISON.csv','d60883a507d1a2cbddf3055c3bc32a212a8e9af7fbc33290ee1fe68c310b05f4'),
    (S+'run_v2/SUMMARY.json','8484cdd8e131c541f462cc86eea221663ab2141a1a62768ba2e970f622a29559'),
    (S+'independent_qa_v1/run_v1/INDEPENDENT_NUMERICAL_QA.json','2de845925d13e098f2f54606bb7e37aa6fab38450ebbfa9509edff0e493513c2'),
    (O+'run_v1/SUMMARY.json','45539223c3b1a50428bebf0d18c611ff1cea57732aba3a625a4ea39a7e6aae48'),
    (O+'RESULTS_CN.md','f3cacad736f1f1ec51990b1dca728b09a5ec1b936dcfbb716d360a7d43e53d3e'),
    (O+'INDEPENDENT_NUMERIC_QA.json','df29cbc659a888817b0087efed784c34ac9ca8e23efcab66d2e475c1592f1306'),
]
OUT = 'docs/research/eucap15_results_20260909'
NOTE = 'docs/research/EUCAP15_MILESTONE_RESULTS_20260909.md'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_text(raw):
    value = raw.decode('utf-8')
    forbidden = tuple('/' + prefix + '/' for prefix in ('Users', 'volumes', 'home'))
    if any(token in value for token in (*forbidden, 'ssh ' + '-', 'BEGIN ' + 'PRIVATE KEY')):
        raise ValueError('Private machine path or credential-shaped text in public output')
    if re.search(r'\b(?:password|api_key|access_token)\s*[:=]\s*\S+',value,re.I):
        raise ValueError('Credential-shaped public output')


def write(path, value):
    raw = (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    safe_text(raw)
    with path.open('xb') as f: f.write(raw)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--workspace-root',type=Path,required=True); parser.add_argument('--project-root',type=Path,required=True); args=parser.parse_args()
    workspace=args.workspace_root.resolve(); project=args.project_root.resolve()
    project_relative=str(project.relative_to(workspace))
    out=project/OUT
    assert Path(__file__).resolve()==out/'build_public.py'
    names=('COMPARISON_5ROW.csv','MATCHED16_COMPARISON.csv','SOURCE_OUTPUT_MANIFEST.json','PUBLIC_RECEIPT.json','SHA256SUMS')
    if any((out/name).exists() for name in names): raise ValueError('NO_CLOBBER: output already exists')
    assert out.is_dir() and not out.is_symlink()
    blobs={}; sources=[]
    for relative, expected in SOURCES:
        path=workspace/relative
        assert path.is_file() and not any(p.is_symlink() for p in (path,*path.parents))
        raw=path.read_bytes(); assert sha(raw)==expected, relative+' source SHA mismatch'
        blobs[relative]=raw
        sources.append(dict(workspace_relative_path=relative,sha256=expected,bytes=len(raw),redistributed=False))
    compact_qa=json.loads(blobs[C+'independent_compact_qa_v1/run_v1/INDEPENDENT_COMPACT_QA.json'])
    physical_qa=json.loads(blobs[S+'independent_qa_v1/run_v1/INDEPENDENT_NUMERICAL_QA.json'])
    assert compact_qa['status']=='GO' and not compact_qa['failures']
    assert physical_qa['status']=='GO_SCOPED_NUMERICAL_QA' and not physical_qa['failures']
    exports=[]
    pairs=[(C+'compact_comparison_v1/run_v1/COMPARISON_5ROW.csv','COMPARISON_5ROW.csv',5,29), (S+'run_v2/MATCHED16_COMPARISON.csv','MATCHED16_COMPARISON.csv',2,16)]
    for relative,name,nrows,ncols in pairs:
        raw=blobs[relative]; safe_text(raw)
        parser_csv=csv.DictReader(io.StringIO(raw.decode(),newline='')); rows=list(parser_csv)
        assert len(rows)==nrows and len(parser_csv.fieldnames)==ncols
        assert not any(re.search(r'path|geometry|candidate_id|request_id|password|token',key,re.I) for key in parser_csv.fieldnames)
        with (out/name).open('xb') as f:f.write(raw)
        assert (out/name).read_bytes()==raw
        exports.append(dict(source_workspace_relative_path=relative,output_project_relative_path=OUT+'/'+name,sha256=sha(raw),bytes=len(raw),copy_method='EXACT_BYTES_NO_RECOMPUTATION',rows=nrows,columns=ncols))
    note=(project/NOTE).read_bytes(); safe_text(note)
    builder=(out/'build_public.py').read_bytes(); safe_text(builder)
    def output_pin(path):
        raw=path.read_bytes(); safe_text(raw)
        return dict(project_relative_path=str(path.relative_to(project)),sha256=sha(raw),bytes=len(raw))
    manifest=dict(schema='eucap15_public_milestone_source_output.v1',created_utc=datetime.now(timezone.utc).isoformat(),source_path_base='WORKSPACE_ROOT_NOT_GITHUB_DOWNLOAD_LINKS',output_path_base='PROJECT_ROOT',project_workspace_relative_path=project_relative,sources=sources,exports=exports,public_text_and_builder=[output_pin(project/NOTE),output_pin(out/'build_public.py')],source_evidence_published=False,model_weights_or_raw_geometry_published=False,new_scientific_statistics=False)
    write(out/'SOURCE_OUTPUT_MANIFEST.json',manifest)
    for relative,expected in SOURCES: assert sha((workspace/relative).read_bytes())==expected,'Source changed during copy'
    write(out/'PUBLIC_RECEIPT.json',dict(schema='eucap15_public_milestone_delivery.v1',status='LOCAL_PUBLIC_ARTIFACTS_PREPARED_NOT_GIT_COMMITTED',created_utc=datetime.now(timezone.utc).isoformat(),manifest=output_pin(out/'SOURCE_OUTPUT_MANIFEST.json'),approved_csv_copies=exports,privacy_check='PASS_PUBLIC_FILES_NO_MACHINE_ABSOLUTE_PATHS_RAW_GEOMETRY_WEIGHTS_OR_CREDENTIALS',scientific_QA='INHERITED_EXACT_SOURCE_QA_NO_NEW_STATISTICAL_CLAIM',scientific_runs=0,figures=0,git_operations=0,existing_manifests_or_index_modified=False))
    public_files=[project/NOTE,*sorted(p for p in out.iterdir() if p.is_file())]
    lines=[]
    for path in public_files:
        identity=output_pin(path)
        relative='../'+path.name if path==project/NOTE else path.name
        lines.append(identity['sha256']+'  '+relative)
    with (out/'SHA256SUMS').open('x') as f:f.write('\n'.join(lines)+'\n')
    print(json.dumps(dict(status='PREPARED_NOT_COMMITTED',public_receipt=output_pin(out/'PUBLIC_RECEIPT.json'),manifest=output_pin(out/'SOURCE_OUTPUT_MANIFEST.json'),csv_copies=exports),ensure_ascii=False))


if __name__=='__main__':main()
