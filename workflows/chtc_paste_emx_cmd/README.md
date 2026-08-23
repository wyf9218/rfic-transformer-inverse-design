# CHTC Pasted EMX Command Template

This is a minimal HTCondor workflow for rerunning an EMX command on CHTC when
you already have:

- an EMX work zip
- a process `.proc` file you are allowed to use on that system
- the exact EMX command line copied from your own local run script or log

The repository does not include foundry process files, license settings, or
design data. Stage those files separately according to your organization and
tool license rules.

## Files

- `run_chtc_paste_emx.sh`: runner that unzips the work bundle, rewrites the
  pasted command onto local job paths, and executes EMX
- `emx_job_paste.sh`: template job configuration to edit per run
- `chtc_paste_emx.sub`: HTCondor submit file
- `submit_chtc_paste_emx.sh`: convenience wrapper around `condor_submit`

## Edit The Job

In `emx_job_paste.sh`, set:

- `ZIP_FILE`
- `PROC_FILE`
- `ORIGINAL_WORK_DIR`
- `ORIGINAL_PROC_FILE`
- `EMX_CMD_RAW`

`EMX_CMD_RAW` should be the exact command you want to run. The runner replaces
`ORIGINAL_WORK_DIR` and `ORIGINAL_PROC_FILE` with the paths staged into the
CHTC job directory.

If you change `ZIP_FILE` or `PROC_FILE`, update `transfer_input_files` in
`chtc_paste_emx.sub` too. HTCondor transfers only the files listed there.

## Environment

The runner expects `emx` to be available on `PATH`. You can provide these
optional environment variables before submission if your CHTC pool needs them:

- `CADENCE_LICENSE_FILE`
- `CADENCE_CDSLMD_LICENSE_FILE`
- `CADENCE_BIN_DIR`
- `CADENCE_INSTALL_ROOT`

## Submit

Copy the workflow files, your EMX work zip, and your process file to the CHTC
submit host. Then run:

```bash
chmod +x run_chtc_paste_emx.sh submit_chtc_paste_emx.sh
./submit_chtc_paste_emx.sh
```

The job writes returned logs and rewritten command details under
`emx_paste_cmd/`.
