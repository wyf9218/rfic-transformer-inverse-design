# Research code evidence

`eucap15_controlled_prepare_20260910.py` is a byte-preserved review copy of the
thin adapter actually used for the new64-proposal bounded15GHz comparison.
SHA-256: `ceadaabee8da5958a36839604ae2ee9f3803ff72d8786f79865d61d890c9c734`.

It reuses the existing repository's MLP loading, symmetric Q selection, fixed
bin allocation, geometry LHS, grid and analytical constraints. It does not
train a model, invoke a native simulator, install a scheduler, or compute
unobserved physical results. Native budget enforcement is NOT_INSTALLED for
this new frame and belongs to the original native execution owner.

This is not a command to repeat the completed preparation. The original private
manifest pins the original absolute code/input/output paths. A later authorized
study must freeze its own identity and no-clobber output; do not edit an old
manifest to make it appear to describe a new run. Private models, dataset
members, geometries, foundry files and actual candidate outputs are excluded
from this public code archive.
