# M4 source map — byte-identical inspection copies

No source in this directory was executed by the publication step. Original private inputs,
polygons, model data, PDK files, physical artifacts and ledger bodies are not published.
Every delivered source is byte-identical to its listed source version; no path sanitization
or unmarked refactor was performed.

| Delivered file | Original source role/version | SHA-256 | Actual status at preparation |
|---|---|---|---|
| history_publication.py | history_publication_adapter_v3 | 885f794ada737faad7deed9c4cef3bc56ac92d7e7a631fd98c5381eb3b5a2811 | 8 new tests passed once; exact native release appended first293, 650→943 |
| failure_receive.py | production256_failure_receiver_v1 | d6599ae889f8b406603a7ce15853a63c8a119d7672de0f0d1a85abf1786bffd6 | Executed delta reception of two then one original failed objects; no physical QA |
| lineage_cross_binding_v1.py | production256_doe006_endpoint_diagnosis_v1 | 01c0ae7c2eac2fca55baf0de58824c034625d84dcaceb9a8bb8772c6d49aa2b1 | Isolated one-case in-memory replay only; NOT_INSTALLED |
| replay_lineage_v1.py | same isolated diagnosis version | 2baa53cd03ddd466f06d27a43f54dc644969d0dc90f20183d0914500e65ffe2d | Actual one-case draft replay and four negative fixtures |
| targeted_width_guard_v1.py | same isolated diagnosis version | 8399450535da1712bcf66586d57512dc7286fd46fdd154fca1658fd92b2536c5 | One extra fixture isolates exact-width rejection; earlier four not rerun |

The original runtime endpoint constructor SHA is
df712c44abb2113d6cced55653d8da169ecae74a7061b8dec756a4f73f09c151.
The local construction receipt is 221833c2ac9bd607383c564fa2d5406278dde9fdd74df3f55ae47aa5517a9ed3.
The fifth width-guard receipt is c41511a5c4d797dbead4251c10dd6058838e29f36bef5f1a9359b39815ed5f73.
The diagnosis seal is df545d83b8886c5ebedec6e7f43f5614ca1302b428602cd891d01dbf19bf71c0.
These private receipts are referenced, not copied into Git.
The actual history publication release18dbe316cc47e51e843ce2180c0fc29915e48b3f609419fdc279e125d752372f
pins the identical delivered885f history source. First293 native readback:
9d1022d87928ea2e2502562d1b15b235be61caa28d6abc28bfbf4b5697cc7614.

## Dependencies and invocation boundary

History publication reuses the prior public admission.py / geometry_helpers.py /
atomic_primitives.py snapshots; its unchanged writer retains WRITE.lock, dynamic ledger
readback and exclusive next sequence. The caller must supply the original private
source/audit/runtime bindings. It has no standalone CLI and this copy is not an installer.
The eight-test source has private hardcoded environment bindings and is intentionally
not copied; original SHA e3779ec161b68bcf326d588e1ea25bbb73c86d510b082f6e634ef685efa58e7a.

Failure reception requires the exact sibling production256_research_receiver_v1 layout
and consume_spec_v2.py SHA0b53d6078b8648f34ca7d46652676844118bc73466921a58237259485dc4d196.
That existing source is already public in publication_v3; do not edit the copied source
to silently reinterpret paths. At its original configured location the interface is:

`python -B failure_receive.py --spec PRIVATE_SPEC --sha256 EXACT_SPEC_SHA --out NEW_OUTPUT`

The actual local diagnostic replay interface, with its already-recorded private trace:

`python -B replay_lineage_v1.py --trace-dir PRIVATE_TRACE_DIR --repo PINNED_REPO --out NEW_OUTPUT`

These show required arguments, not a request to rerun completed work. A missing private
trace or source pin is a hard failure. The draft must not replace an active frozen256
runtime, and local construction PASS must not be reported as DRC/EMX/physical PASS.
