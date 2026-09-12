"""Narrow, no-clobber edit of the existing five-page development manuscript."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import zipfile
from lxml import etree

ROOT = Path("/Users/wyf/Documents/模拟变压器AI反向建模/reports/eucap15ghz_20260908T220300Z")
WORK = ROOT / "batch_qualification_resume_20260912_v1"
SOURCE = ROOT / "paper_controlled32_increment_20260911_v1/document_v3/EuCAP_15GHz_Development_Results_Draft.docx"
COST = WORK / "closed_batch3_cost_m29_v1/COST.json"
COST_CODE = WORK / "closed_batch3_cost_m29_v1/SOURCE.rb"
OUTPUT = Path(__file__).parent / "EuCAP_15GHz_Development_Results_Cost_Update_v2.docx"
PINS = {
    SOURCE: "4fecf1a3009e3925cc55219ea01812ca0371be7dd1f29c4d479033b8dd544242",
    COST: "ceea270dcb6a8ba6d15b6205b09bf5f83a0406c8e1bea560bd73153089e223ff",
    COST_CODE: "b84ceac897cfec3bca90be91a2eb829921c8c599ab0e2551b7e76b8af23e737f",
}
for path, digest in PINS.items():
    assert sha256(path.read_bytes()).hexdigest() == digest, str(path)
assert not OUTPUT.exists(), "No-clobber output already exists"
cost = json.loads(COST.read_text())
assert cost["counts"] == {"terminal": 256, "fresh": 234, "analytic_fail": 22, "formal": 116}
assert cost["formal_closure_elapsed_seconds"] == 3391.446753
assert cost["storage"]["allocated_bytes"] == 2162520064

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{%s}" % NS["w"]
with zipfile.ZipFile(SOURCE) as z:
    original = {entry.filename: z.read(entry.filename) for entry in z.infolist()}
    entries = z.infolist()
xml = etree.fromstring(original["word/document.xml"])
body = xml.find("w:body", NS)
paragraphs = body.findall("w:p", NS)
assert len(paragraphs) == 77

def text(p):
    return "".join(p.xpath(".//w:t/text()", namespaces=NS))

before = [text(p) for p in paragraphs]
needle = "the full 6700-member owner ledger"
assert before[24].count(needle) == 1
for node in paragraphs[24].findall(".//w:t", NS):
    if needle in (node.text or ""):
        node.text = node.text.replace(needle, "the full owner ledger")
        break
else:
    raise AssertionError("Expected ledger phrase must remain in one source text run")

qualification = ("Numerical strict/range eligibility does not establish historical process, port, GDS or DRC identity.")
scope_sentence = "Conclusions remain specific to the recorded topology, process and simulation settings."
assert before[71].startswith(scope_sentence)
for node in paragraphs[71].findall(".//w:t", NS):
    if scope_sentence in (node.text or ""):
        node.text = node.text.replace(scope_sentence, qualification, 1)
        break
else:
    raise AssertionError("Expected scope sentence must remain in one source text run")
cost_text = (
    "Separate production batch 3 closed 256 candidates: 234 fresh EMX, 22 analytical failures and "
    "116 formally admitted unique geometries. Admission-to-final-submission time was 3391.45 s. "
    "Near-terminal allocated storage was 2,162,520,064 bytes (2.014 GiB), including failed artifacts "
    "but excluding shared resources and the external ledger. This excludes pre-admission generation "
    "time and establishes neither peak/incremental storage, stable throughput, full-campaign cost "
    "nor the controlled-64 trial's cost."
)
newp = etree.Element(W + "p")
ppr = paragraphs[72].find("w:pPr", NS)
if ppr is not None:
    newp.append(deepcopy(ppr))
run = etree.SubElement(newp, W + "r")
source_run = paragraphs[72].find("w:r", NS)
rpr = source_run.find("w:rPr", NS)
if rpr is not None:
    run.append(deepcopy(rpr))
etree.SubElement(run, W + "t").text = cost_text
assert before[73] == "References"
body.insert(list(body).index(paragraphs[73]), newp)

after_old = [text(p) for p in paragraphs]
assert [i for i, (a, b) in enumerate(zip(before, after_old)) if a != b] == [24, 71]
assert after_old[72] == before[72]
assert after_old[74:] == before[74:]
assert len(xml.findall(".//w:sectPr", NS)) == 4
assert len(xml.findall(".//w:tbl", NS)) == 1
for retained in ("40/128", "40/48", "1253/1259", "NOT_REACHED"):
    assert retained in " ".join(text(p) for p in body.findall("w:p", NS))
newxml = etree.tostring(xml, encoding="UTF-8", xml_declaration=True, standalone=True)
with zipfile.ZipFile(OUTPUT, "x") as out:
    for entry in entries:
        out.writestr(entry, newxml if entry.filename == "word/document.xml" else original[entry.filename])
with zipfile.ZipFile(OUTPUT) as out:
    changed_parts = [name for name, data in original.items() if out.read(name) != data]
assert changed_parts == ["word/document.xml"]
assert sha256(SOURCE.read_bytes()).hexdigest() == PINS[SOURCE]
print(json.dumps({
    "output": str(OUTPUT), "sha256": sha256(OUTPUT.read_bytes()).hexdigest(),
    "bytes": OUTPUT.stat().st_size, "changed_ooxml_parts": changed_parts,
    "changed_original_paragraph_indices": [24, 71],
    "inserted_cost_paragraph_before_original_index": 73,
    "qualification_addition": qualification.strip(), "cost_paragraph": cost_text,
    "retained_sections": 4, "retained_tables": 1,
    "original_unchanged": True, "references_unchanged": True,
    "render_status": "REQUIRED"
}, indent=2))

