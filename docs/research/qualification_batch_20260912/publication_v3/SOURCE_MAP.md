# M3 source map: qualification publication and read-only receiver

The following five source files are byte-identical to their private sources. Executed scope is specified per file; copying them neither deploys a controller nor performs another qualification.

| Delivered file | Private source binding (logical path; not distributed) | Source = delivered SHA-256 | Actual scope |
| --- | --- | --- | --- |
| history_publication.py | PRIVATE_WORK_PACKAGE/history_publication_adapter_v2/history_publication.py | 9a9f5611b6da69fb43413f4d855881aebadad7542c6874eb288c0b7bd357c36a | Native owner appended 307 qualified historical members from source rows1001–2000; readback61ad99b1841f0fdaf9a407c4941f299a6759bf12c4a35e0e86b9dd760a2eabe5. |
| consume_spec_v2.py | PRIVATE_WORK_PACKAGE/production256_research_receiver_v1/consume_spec_v2.py | 0b53d6078b8648f34ca7d46652676844118bc73466921a58237259485dc4d196 | Research received3fresh, then DOE003, then unseen004/005; original analytic cache retained. Latest deltaeddd47bbec16ebb0e6bf890019bd34286479e1e1d6a6e27649025c5635b48941. |
| production256_publication.py | PRIVATE_WORK_PACKAGE/production256_publication_adapter_v1/production256_publication.py | 8d10bc0e8fa57c272c42a3c97a7e75a58d2efc21e1aafe29e7b1f562bd472905 | Native owner actually appended one train member as649, replay0; receipt3f966b57f291ad3d806f77ccd5cc6ff4c798a0cfafac35230869303dac143de8. |
| prepare_callback.py | PRIVATE_WORK_PACKAGE/production256_publication_adapter_v1/prepare_callback.py | 79c9e93139eb968fffaee1d9387a7a2e37635b031b7f14ca9f0e9f5fd919fcc1 | Actual local byte-binding preparation; receipt11e870cc9c9b9770f9d35c68c2208cbd756d41d4a850ee7f2c09f3314ec8ffa0. |
| test_production256_publication.py | PRIVATE_WORK_PACKAGE/production256_publication_adapter_v1/test_production256_publication.py | 6e7339943479b71ef1ff0fa143cf27c7b53f58380b6539aa0eb30ac0b38aedd8 | Eight new synthetic tests passed first run; receipt65a3e3ac423baec6320f9ad270b44225f29ea8918e7b1a93fcb6a77fcbe1da04. Not rerun for publication. |

## Dependencies and boundaries

The publisher reuses the existing admission, geometry_helpers and atomic_primitives modules, plus NumPy. Existing public admission/geometry/atomic snapshots are documented in the preceding milestones; importing a convenient older reader does not authorize bypassing mixed-ledger schema checks.

The receiver needs exact seven-module private runtime pins declared in its EXPECTED_RUNTIME, immutable input spec, local source-artifact path map and actual transported files. None of those private input files, physical geometries, PDK files or source-artifact pin manifests are included here. Existing runtime paths must not be inferred from a Git checkout.

The receiver delegates to the original fresh_candidate and plan_release; it does not re-extract S4P, rerun physical QA or launch native tools. V2 allows historical metadata to declare different versions at one path; any requested read still verifies the exact SHA/length. Ambiguous path-only lookup remains a failure. The first receiver's preserved HOLD is not an EMX failure.

Success-cache identities prevent closed-source reconsumption. A new spec selects only unseen results; this source package is not an installed automatic watcher.

The historical publisher's trusted current partition is1001–2000. A subsequent source2001+ execution is NOT_INSTALLED, not authorized by relabeling an old receipt. The new-production256 callback passed compatibility review c4ddd0e5887e2a6632ff2137654aa61e47a6ea4167556726bf5b2b26363a74d2 and was used for one actual native-owner append. Its prepared evidence SHA acd45fa9de2cbb192c524b1d75c2feb31c98bed58a52bd5d7a60da3a8b314308 identifies a private document that is not published. One invocation is not an installed automatic publication scheduler.

## Limited public-safety review

All five complete modules were inspected; byte hashes match. A bounded path/credential-pattern scan found no private absolute paths or credential literals. The test's geometry and response literals are explicitly synthetic fixtures, not experimental rows. No raw experimental geometry rows, physical response files, private INPUTS or PDK content were copied. Code-level schema and implementation SHA constants remain necessary provenance, not access credentials.

This is a source review package, not a standalone native launcher. No prior test suite or physical evidence generation was repeated for publication.
