# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""SNN2 sparse adaptive spiking-network support."""

from .manifest import (
    SNN2ManifestError,
    canonical_payload_sha256,
    compile_edge_banks,
    export_manifest,
    make_default_manifest,
    validate_manifest,
)
from .reference import (
    ALIFState,
    SNN2EncoderOutput,
    SNN2EncoderReference,
    SNN2Reference,
    map_control_q8,
    step_alif_neuron,
)

__all__ = [
    "ALIFState",
    "SNN2ManifestError",
    "SNN2EncoderOutput",
    "SNN2EncoderReference",
    "SNN2Reference",
    "canonical_payload_sha256",
    "compile_edge_banks",
    "export_manifest",
    "make_default_manifest",
    "map_control_q8",
    "step_alif_neuron",
    "validate_manifest",
]
