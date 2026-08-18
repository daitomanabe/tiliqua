# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Canonical manifest validation and deterministic SNN2 artifact export."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


PROFILE = "snn2_256x16_sparse_alif_v1"
NEURON_COUNT = 256
LANE_COUNT = 16
EXCITATORY_COUNT = 192
INHIBITORY_COUNT = 64
FANOUT = 8
EDGE_COUNT = NEURON_COUNT * FANOUT
TARGET_BANK_COUNT = 4
CENTER_FREQUENCIES_HZ = (80, 160, 320, 640, 1280, 2560, 5120, 10240)
FILTER_COEFFICIENTS_Q2_14 = (
    (120, 0, -120, -32_525, 16_143),
    (239, 0, -239, -32_283, 15_906),
    (471, 0, -471, -31_798, 15_442),
    (915, 0, -915, -30_829, 14_553),
    (1_728, 0, -1_728, -28_901, 12_927),
    (3_091, 0, -3_091, -25_107, 10_202),
    (5_000, 0, -5_000, -17_843, 6_384),
    (6_680, 0, -6_680, -4_432, 3_023),
)


class SNN2ManifestError(ValueError):
    """Raised when a network manifest violates the frozen SNN2 v1 schema."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int(mapping: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = mapping.get(key)
    if not _is_int(value) or not minimum <= value <= maximum:
        raise SNN2ManifestError(
            f"{key} must be an integer in the range {minimum}..{maximum}"
        )
    return value


def canonical_payload_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the checksum payload with stable key and whitespace ordering."""

    payload = deepcopy(manifest)
    payload.pop("payload_sha256", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_payload_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(manifest)).hexdigest()


def _validate_header(manifest: dict[str, Any]) -> None:
    expected = {
        "schema_version": 1,
        "profile": PROFILE,
        "sample_rate_hz": 48_000,
        "neuron_count": NEURON_COUNT,
        "lane_count": LANE_COUNT,
        "excitatory_count": EXCITATORY_COUNT,
        "inhibitory_count": INHIBITORY_COUNT,
        "fanout": FANOUT,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SNN2ManifestError(f"{key} must equal {value!r}")


def _validate_numeric(manifest: dict[str, Any]) -> None:
    numeric = manifest.get("numeric")
    if not isinstance(numeric, dict):
        raise SNN2ManifestError("numeric must be an object")
    expected = {
        "membrane_bits": 18,
        "current_bits": 16,
        "adaptation_bits": 16,
        "refractory_bits": 4,
        "weight_bits": 8,
    }
    if numeric != expected:
        raise SNN2ManifestError(f"numeric must equal {expected!r}")


def _validate_dynamics(manifest: dict[str, Any]) -> None:
    dynamics = manifest.get("dynamics")
    if not isinstance(dynamics, dict):
        raise SNN2ManifestError("dynamics must be an object")
    for key in ("tau_e", "tau_i", "tau_a", "tau_m"):
        _require_int(dynamics, key, 2, 10)
    _require_int(dynamics, "threshold_base", 0, (1 << 17) - 1)
    _require_int(dynamics, "reset_level", -(1 << 17), (1 << 17) - 1)
    _require_int(dynamics, "adaptation_step", 0, (1 << 16) - 1)
    _require_int(dynamics, "refractory_samples", 0, 15)


def _validate_neurons(manifest: dict[str, Any]) -> None:
    neurons = manifest.get("neurons")
    if not isinstance(neurons, list) or len(neurons) != NEURON_COUNT:
        raise SNN2ManifestError(f"neurons must contain {NEURON_COUNT} records")
    for index, neuron in enumerate(neurons):
        if not isinstance(neuron, dict):
            raise SNN2ManifestError(f"neurons[{index}] must be an object")
        if neuron.get("id") != index:
            raise SNN2ManifestError(f"neurons[{index}].id must equal {index}")
        expected_population = "I" if index % 4 == 3 else "E"
        if neuron.get("population") != expected_population:
            raise SNN2ManifestError(
                f"neurons[{index}].population must equal {expected_population}"
            )
        _require_int(neuron, "bias", -(1 << 17), (1 << 17) - 1)
        _require_int(neuron, "initial_v", -(1 << 17), (1 << 17) - 1)
        _require_int(neuron, "initial_ie", 0, (1 << 16) - 1)
        _require_int(neuron, "initial_ii", 0, (1 << 16) - 1)
        _require_int(neuron, "initial_a", 0, (1 << 16) - 1)
        _require_int(neuron, "initial_r", 0, 15)


def _validate_edges(manifest: dict[str, Any]) -> None:
    edges = manifest.get("edges")
    if not isinstance(edges, list) or len(edges) != EDGE_COUNT:
        raise SNN2ManifestError(f"edges must contain {EDGE_COUNT} records")

    seen: dict[int, set[int]] = defaultdict(set)
    bank_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for position, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise SNN2ManifestError(f"edges[{position}] must be an object")
        source = _require_int(edge, "source", 0, NEURON_COUNT - 1)
        target = _require_int(edge, "target", 0, NEURON_COUNT - 1)
        weight = _require_int(edge, "weight", -127, 127)
        expected_source = position // FANOUT
        expected_bank = position % 4
        if source != expected_source:
            raise SNN2ManifestError(
                f"edges[{position}].source must equal {expected_source}"
            )
        if target % TARGET_BANK_COUNT != expected_bank:
            raise SNN2ManifestError(
                f"edges[{position}] must target bank {expected_bank}"
            )
        if target == source:
            raise SNN2ManifestError(f"source {source} contains a self-edge")
        if target in seen[source]:
            raise SNN2ManifestError(
                f"source {source} contains duplicate target {target}"
            )
        if weight in (0, -128):
            raise SNN2ManifestError(f"edges[{position}] has an invalid weight")
        if source % 4 == 3 and weight >= 0:
            raise SNN2ManifestError(
                f"inhibitory source {source} has non-negative weight {weight}"
            )
        if source % 4 != 3 and weight <= 0:
            raise SNN2ManifestError(
                f"excitatory source {source} has non-positive weight {weight}"
            )
        seen[source].add(target)
        bank_counts[source][target % TARGET_BANK_COUNT] += 1

    for source in range(NEURON_COUNT):
        if bank_counts[source] != [2, 2, 2, 2]:
            raise SNN2ManifestError(
                f"source {source} bank counts must equal [2, 2, 2, 2]"
            )


def _validate_encoder(manifest: dict[str, Any]) -> None:
    encoder = manifest.get("encoder")
    if not isinstance(encoder, dict):
        raise SNN2ManifestError("encoder must be an object")
    if encoder.get("kind") != "deterministic_phase_accumulator":
        raise SNN2ManifestError("encoder.kind is not the v1 deterministic encoder")
    if encoder.get("center_frequencies_hz") != list(CENTER_FREQUENCIES_HZ):
        raise SNN2ManifestError("encoder center frequencies do not match v1")
    fixed = {
        "filter_structure": "df2t_q2_14",
        "filter_state_bits": 18,
        "phase_bits": 24,
        "phase_increment_shift": 4,
        "drive_base": 6_144,
        "control_clamp_asq": 4_000,
    }
    for key, value in fixed.items():
        if encoder.get(key) != value:
            raise SNN2ManifestError(f"encoder.{key} must equal {value!r}")
    expected_mapping = {
        "gain": {
            "low_q8": 128,
            "center_q8": 320,
            "high_q8": 512,
            "slope_numerator": 3,
            "slope_shift": 6,
        },
        "adaptation": {
            "low_q8": 0,
            "center_q8": 256,
            "high_q8": 512,
            "slope_numerator": 1,
            "slope_shift": 4,
        },
    }
    if encoder.get("control_mapping") != expected_mapping:
        raise SNN2ManifestError(
            f"encoder.control_mapping must equal {expected_mapping!r}"
        )
    groups = encoder.get("excitatory_groups")
    excitatory_ids = [index for index in range(256) if index % 4 != 3]
    expected_groups = [
        {"neuron_ids": excitatory_ids[start:start + 24]}
        for start in range(0, 192, 24)
    ]
    if groups != expected_groups:
        raise SNN2ManifestError(
            "encoder groups must cover all 192 excitatory neuron IDs"
        )
    coefficients = encoder.get("filter_coefficients_q2_14")
    if not isinstance(coefficients, list) or len(coefficients) != 8:
        raise SNN2ManifestError("encoder must contain eight coefficient records")
    for index, record in enumerate(coefficients):
        if not isinstance(record, dict):
            raise SNN2ManifestError(
                f"encoder.filter_coefficients_q2_14[{index}] must be an object"
            )
        for key in ("b0", "b1", "b2", "a1", "a2"):
            _require_int(record, key, -(1 << 15), (1 << 15) - 1)


def _validate_readout(manifest: dict[str, Any]) -> None:
    weights = manifest.get("readout_weights")
    if not isinstance(weights, list) or len(weights) != NEURON_COUNT:
        raise SNN2ManifestError(
            f"readout_weights must contain {NEURON_COUNT} integers"
        )
    for index, weight in enumerate(weights):
        if not _is_int(weight) or not -127 <= weight <= 127 or weight == -128:
            raise SNN2ManifestError(
                f"readout_weights[{index}] must be a signed 8-bit coefficient"
            )


def validate_manifest(
    manifest: dict[str, Any], *, verify_checksum: bool = True
) -> dict[str, Any]:
    """Validate and return a defensive copy of one canonical SNN2 manifest."""

    if not isinstance(manifest, dict):
        raise SNN2ManifestError("manifest must be a JSON object")
    required = {
        "schema_version", "profile", "sample_rate_hz", "neuron_count",
        "lane_count", "excitatory_count", "inhibitory_count", "fanout",
        "numeric", "dynamics", "neurons", "edges", "encoder",
        "readout_weights", "training_provenance", "payload_sha256",
    }
    missing = sorted(required - manifest.keys())
    extra = sorted(manifest.keys() - required)
    if missing:
        raise SNN2ManifestError(f"missing manifest keys: {', '.join(missing)}")
    if extra:
        raise SNN2ManifestError(f"unknown manifest keys: {', '.join(extra)}")

    _validate_header(manifest)
    _validate_numeric(manifest)
    _validate_dynamics(manifest)
    _validate_neurons(manifest)
    _validate_edges(manifest)
    _validate_encoder(manifest)
    _validate_readout(manifest)
    if not isinstance(manifest.get("training_provenance"), dict):
        raise SNN2ManifestError("training_provenance must be an object")

    checksum = manifest.get("payload_sha256")
    expected_checksum = canonical_payload_sha256(manifest)
    if verify_checksum and checksum != expected_checksum:
        raise SNN2ManifestError(
            f"payload_sha256 mismatch: expected {expected_checksum}, got {checksum}"
        )
    return deepcopy(manifest)


def _default_edges() -> list[dict[str, int]]:
    edges: list[dict[str, int]] = []
    for source in range(NEURON_COUNT):
        used: set[int] = set()
        sign = -1 if source % 4 == 3 else 1
        for round_index in range(2):
            for bank in range(4):
                high = (source * 13 + round_index * 17 + bank * 7 + 5) % 64
                target = high * 4 + bank
                while target == source or target in used:
                    high = (high + 1) % 64
                    target = high * 4 + bank
                used.add(target)
                magnitude = 8 + ((source * 5 + round_index * 11 + bank * 3) % 40)
                edges.append({
                    "source": source,
                    "target": target,
                    "weight": sign * magnitude,
                })
    return edges


def make_default_manifest() -> dict[str, Any]:
    """Create the deterministic, non-trained bring-up network for SNN2 v1."""

    coefficients = [
        dict(zip(("b0", "b1", "b2", "a1", "a2"), values))
        for values in FILTER_COEFFICIENTS_Q2_14
    ]
    readout_weights = []
    for index in range(NEURON_COUNT):
        if index % 4 == 3:
            readout_weights.append(-64)
        else:
            excitatory_rank = index - ((index + 1) // 4)
            readout_weights.append(
                127 if (excitatory_rank // 24) % 2 else -127
            )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "profile": PROFILE,
        "sample_rate_hz": 48_000,
        "neuron_count": NEURON_COUNT,
        "lane_count": LANE_COUNT,
        "excitatory_count": EXCITATORY_COUNT,
        "inhibitory_count": INHIBITORY_COUNT,
        "fanout": FANOUT,
        "numeric": {
            "membrane_bits": 18,
            "current_bits": 16,
            "adaptation_bits": 16,
            "refractory_bits": 4,
            "weight_bits": 8,
        },
        "dynamics": {
            "tau_e": 4,
            "tau_i": 4,
            "tau_a": 7,
            "tau_m": 5,
            "threshold_base": 12_000,
            "reset_level": 0,
            "adaptation_step": 512,
            "refractory_samples": 2,
        },
        "neurons": [
            {
                "id": index,
                "population": "I" if index % 4 == 3 else "E",
                "bias": 96 + (index % 16) * 8,
                "initial_v": ((index * 997 + 313) % 4_000) - 2_000,
                "initial_ie": 0,
                "initial_ii": 0,
                "initial_a": 0,
                "initial_r": 0,
            }
            for index in range(NEURON_COUNT)
        ],
        "edges": _default_edges(),
        "encoder": {
            "kind": "deterministic_phase_accumulator",
            "center_frequencies_hz": list(CENTER_FREQUENCIES_HZ),
            "filter_structure": "df2t_q2_14",
            "filter_state_bits": 18,
            "phase_bits": 24,
            "phase_increment_shift": 4,
            "drive_base": 6_144,
            "control_clamp_asq": 4_000,
            "control_mapping": {
                "gain": {
                    "low_q8": 128,
                    "center_q8": 320,
                    "high_q8": 512,
                    "slope_numerator": 3,
                    "slope_shift": 6,
                },
                "adaptation": {
                    "low_q8": 0,
                    "center_q8": 256,
                    "high_q8": 512,
                    "slope_numerator": 1,
                    "slope_shift": 4,
                },
            },
            "excitatory_groups": [
                {"neuron_ids": [
                    index
                    for index in range(256)
                    if index % 4 != 3
                ][start:start + 24]}
                for start in range(0, 192, 24)
            ],
            "filter_coefficients_q2_14": coefficients,
        },
        "readout_weights": readout_weights,
        "training_provenance": {
            "kind": "deterministic_bringup_fixture",
            "trained": False,
            "generator": "tiliqua.snn2.manifest.make_default_manifest",
        },
        "payload_sha256": "",
    }
    manifest["payload_sha256"] = canonical_payload_sha256(manifest)
    return validate_manifest(manifest)


def compile_edge_banks(manifest: dict[str, Any]) -> tuple[tuple[int, ...], ...]:
    """Compile four 512-word target-bank ROMs from the canonical edge list."""

    manifest = validate_manifest(manifest)
    banks = [[0] * (NEURON_COUNT * 2) for _ in range(TARGET_BANK_COUNT)]
    for position, edge in enumerate(manifest["edges"]):
        source = edge["source"]
        round_index = (position % FANOUT) // TARGET_BANK_COUNT
        bank = edge["target"] % TARGET_BANK_COUNT
        address = source * 2 + round_index
        banks[bank][address] = (
            ((edge["weight"] & 0xFF) << 8) | edge["target"]
        )
    return tuple(tuple(bank) for bank in banks)


def _pack_state(neuron: dict[str, Any]) -> int:
    fields = (
        (neuron["initial_v"] & ((1 << 18) - 1), 18),
        (neuron["initial_ie"], 16),
        (neuron["initial_ii"], 16),
        (neuron["initial_a"], 16),
        (neuron["initial_r"], 4),
    )
    packed = 0
    shift = 0
    for value, width in fields:
        packed |= value << shift
        shift += width
    return packed


def export_manifest(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Write byte-deterministic canonical JSON and BRAM initialization files."""

    manifest = validate_manifest(manifest)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    canonical_path = output_dir / "network.json"
    canonical_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    for bank_index, words in enumerate(compile_edge_banks(manifest)):
        path = output_dir / f"edge_bank{bank_index}.hex"
        path.write_text("".join(f"{word:04x}\n" for word in words), encoding="ascii")

    for lane in range(LANE_COUNT):
        path = output_dir / f"state_lane{lane:02d}.hex"
        words = [
            _pack_state(manifest["neurons"][batch * LANE_COUNT + lane])
            for batch in range(NEURON_COUNT // LANE_COUNT)
        ]
        path.write_text("".join(f"{word:018x}\n" for word in words), encoding="ascii")

    readout_path = output_dir / "readout_weights.hex"
    readout_path.write_text(
        "".join(f"{weight & 0xFF:02x}\n" for weight in manifest["readout_weights"]),
        encoding="ascii",
    )

    for path in sorted(output_dir.iterdir()):
        if path.name == "build_manifest.json" or not path.is_file():
            continue
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    derived = {
        "schema_version": 1,
        "profile": manifest["profile"],
        "payload_sha256": manifest["payload_sha256"],
        "files": files,
    }
    (output_dir / "build_manifest.json").write_text(
        json.dumps(derived, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return derived
