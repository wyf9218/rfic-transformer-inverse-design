import hashlib,json,math
from typing import Any,Mapping,Sequence
from decimal import Decimal,ROUND_HALF_UP
GEOMETRY_FIELDS = ('primary_outer_width_um', 'primary_outer_height_um', 'secondary_outer_width_um', 'secondary_outer_height_um', 'line_width_um', 'primary_terminal_y_span_um', 'secondary_terminal_y_span_um', 'offset_um', 'primary_feed_extension_um', 'secondary_feed_extension_um')

def canonical_json_bytes(payload: Any) -> bytes:
    """Return stable UTF-8 bytes for hashing a JSON-compatible value."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

def canonical_geometry_sha256(
    values: Mapping[str, Any],
    *,
    fields: Sequence[str] = GEOMETRY_FIELDS,
    decimal_places: int = 9,
) -> str:
    """Create the campaign's geometry identity from ordered, quantized values."""

    ordered: list[tuple[str, str]] = []
    for field in fields:
        if field not in values:
            raise KeyError(f"missing geometry field: {field}")
        value = float(values[field])
        if not math.isfinite(value):
            raise ValueError(f"non-finite geometry field {field}: {value}")
        ordered.append((field, f"{value:.{int(decimal_places)}f}"))
    return sha256_bytes(canonical_json_bytes(ordered))

GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)

PRODUCTION_GEOMETRY_FINGERPRINT_SCHEMA = "mars56_grounded_s4p_geometry_v1"

PRODUCTION_GEOMETRY_FINGERPRINT_QUANTIZATION_UM = 1.0e-6

def _format_number(value: float) -> str:
    numeric = 0.0 if float(value) == 0.0 else float(value)
    return format(numeric, ".17g")

def _raw_geometry_identity(values: Sequence[float]) -> str:
    return hashlib.sha256(
        "|".join(_format_number(value) for value in values).encode("ascii")
    ).hexdigest()

def _production_geometry_fingerprint(values: Sequence[float]) -> str:
    quantum = Decimal(str(PRODUCTION_GEOMETRY_FINGERPRINT_QUANTIZATION_UM))
    quantized = [
        int(
            (Decimal(str(float(value))) / quantum).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        for value in values
    ]
    payload = {
        "schema": PRODUCTION_GEOMETRY_FINGERPRINT_SCHEMA,
        "quantization_um": format(quantum, "f"),
        "fields": [column.removeprefix("geom__") for column in GEOMETRY_COLUMNS],
        "quantized_values": quantized,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
