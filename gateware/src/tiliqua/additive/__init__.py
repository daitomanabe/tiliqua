# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Tiliqua 1,000-sine additive instrument: reference model and shared tables."""

from . import tables
from .reference import (
    AdditiveBlockStatistics,
    AdditiveControlState,
    AdditiveGroupParameters,
    AdditiveReference,
    AdditiveStateError,
    DEFAULT_CONTROL_STATE,
    normalize_cv,
)

__all__ = [
    "AdditiveBlockStatistics",
    "AdditiveControlState",
    "AdditiveGroupParameters",
    "AdditiveReference",
    "AdditiveStateError",
    "DEFAULT_CONTROL_STATE",
    "normalize_cv",
    "tables",
]
