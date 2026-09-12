# Batch4 DOE021: Exact EMX-Stage Rejection

- Scope: only `eucap15_continuous_doe_neighborhood_20260912_batch000004-GEOMETRY_DOE-021`. No retry, solver call, source mutation or process signal.
- Original failed result pin is retained in `../increment_20260912T132310639271Z/OBSERVATION.json`: SHA256 `17d6a1eaa3657c446fb5ace16ffbde23f2a794a3a3238f2d6fed134b18b15d71`,7598B. No physical response is assigned to this failure.
- `emx_PROCESS.json`: SHA `befbc5c327e33e7a7ef1613983087a4ddfe4d7e14111086e7537ecaf498e5d2f`,2463B; matches original result stage pin. Returncode1 at13:22:21.691770 UTC, completion=null.
- `emx_20260912T132220288828Z.log`: SHA `a013516e9412473ff10bc765f0a039bf14a789a9204a0b1169741fcdd410a64a`,2465B; matches PROCESS.log pin.
- `EMX_REQUEST.json`: SHA `03820a864fc4513e64c83aba404562b237b0e99be6c943227ddbe31e788dbcea`,16197B; matches PREFLIGHT.request pin. Exact request/release `dd9b0808...` and plan `ae56c7da...` are preserved in its controlled_execution object.
- `PREFLIGHT.json`: SHA `71a66ea0313497b895a080ab1ec21fcf12a91751c4e92f65faae362e16e116cb`,30117B. This is a prepared command/GDS binding, not successful execution evidence. Four raw files copied into this private directory, no PDK body or actual GDS copied for this diagnosis.

## Command and Failure Location

- Full Python-stage argv is in PROCESS.intent.command: approved Python, `-B -m research.broadband56_nn.frequency_research_emx run`, this exact EMX_REQUEST path, this candidate's emx_selected output, inherited-global-lease-fd. The intent deliberately represents the descriptor as `<inherited-fd>`; its numeric value is not retained here and is not fabricated.
- Full prepared native argv is in PREFLIGHT.command: the pinned EMX wrapper, this candidate's `cadence_only/.../evaluations/e6fbef8198226c9b/streamout/transformer_layout_cadpins.gds`, TRANSFORMER, exact typical proc, impedance50, standard accuracy, parallel2, pins51, four P001..P004 signal/ground pairs, 5..60GHz step1GHz. Prepared GDS SHA `982cc923159cb87e1cf1e00d723629178699aaffdb601302ab333e58e8ee3a3d`,20480B. Calibre summary pin is `4b722630fd16da725e519afc012ddadb040a97b11f9c61f853cdc0fc464c3431`,6278B. These downstream source pins were not reaudited in this read-only diagnosis.
- Actual traceback: solve -> prepare_native_dispatch -> verify_emx_permit -> `m.require(not own_peers(...), 'FOREIGN_NATIVE_CHAIN')`. Fixed48 runtime SHA23eddf702afb6c3f5ee64ac9c415c56376199857dd6fe915f67856a456f8b99f, lines278/31; EMX endpoint SHAe72f8f392305ffb6d412eef2e5321ea1f5b79fc88ed0a80ed7f2a6b32585db4a, solve lines495/500/510.
- This exception occurs before creation of solve/RUNNING.json, native command execution and S4P extraction on this attempt. It is an execution-isolation rejection, not a Q/L/K/DRC physical failure.
- The original exception did not serialize the returned foreign-peer list or ancestry rejection reason. Exactly which PID triggered it, genuine foreign ownership versus an exited-process race, and any reproducible shared-code defect are UNKNOWN. Subsequent successful candidates do not prove this particular rejection was erroneous.
- No confirmed common deterministic defect to repair from this evidence. Healthy batch4 continues; this single failure stays in the denominator and is not substituted or retried.

Next supported-boundary diagnostic is PLANNED, not deployed: preserve exact owner/release/resource identity, peer PID/start/argv, each observed ancestry hop, and explicit rejection reason in a no-clobber private receipt before raising the same error. Do not weaken isolation, accept vanished processes by default, mutate the active runtime, or backfill the missing historical payload.
