"""Deterministic GDSII hashing that excludes only streamout timestamps."""

from __future__ import annotations

import hashlib
from pathlib import Path


GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM = (
    "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
)

_BGNLIB = 0x01
_BGNSTR = 0x05
_ENDLIB = 0x04
_INT2 = 0x02
_TIMESTAMP_PAYLOAD_BYTES = 24


def gds_timestamp_normalized_sha256(path: str | Path) -> str:
    """Hash an exact GDSII record stream with only creation dates normalized.

    Cadence streamout rewrites the twelve INT2 date/time values in ``BGNLIB``
    and ``BGNSTR`` on every run.  This parser preserves every record header and
    every other payload byte, but hashes zero bytes for those timestamp
    payloads.  Malformed or non-GDSII input fails closed.
    """

    data = Path(path).read_bytes()
    digest = hashlib.sha256()
    offset = 0
    record_count = 0
    timestamp_record_count = 0
    saw_endlib = False

    while offset < len(data):
        if saw_endlib:
            padding = data[offset:]
            if any(padding):
                raise ValueError("nonzero data follows the GDSII ENDLIB record")
            digest.update(padding)
            offset = len(data)
            break
        if len(data) - offset < 4:
            raise ValueError(f"truncated GDSII record header at byte {offset}")
        record_length = int.from_bytes(data[offset : offset + 2], "big")
        if record_length < 4 or record_length % 2:
            raise ValueError(
                f"invalid GDSII record length {record_length} at byte {offset}"
            )
        record_end = offset + record_length
        if record_end > len(data):
            raise ValueError(f"truncated GDSII record payload at byte {offset}")

        record_type = data[offset + 2]
        data_type = data[offset + 3]
        payload = data[offset + 4 : record_end]
        digest.update(data[offset : offset + 4])
        if record_type in {_BGNLIB, _BGNSTR}:
            if data_type != _INT2 or len(payload) != _TIMESTAMP_PAYLOAD_BYTES:
                raise ValueError(
                    "invalid BGNLIB/BGNSTR timestamp record at " f"byte {offset}"
                )
            digest.update(bytes(_TIMESTAMP_PAYLOAD_BYTES))
            timestamp_record_count += 1
        else:
            digest.update(payload)
        if record_type == _ENDLIB:
            if data_type != 0x00 or payload:
                raise ValueError("invalid GDSII ENDLIB record")
            saw_endlib = True
        record_count += 1
        offset = record_end

    if record_count == 0 or timestamp_record_count < 2 or not saw_endlib:
        raise ValueError("incomplete GDSII record stream")
    return digest.hexdigest()
