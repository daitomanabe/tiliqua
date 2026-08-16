#!/bin/sh

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
gateware_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
driver=${XLSYNTH_DRIVER:-xlsynth-driver}
expected_version="xlsynth-driver 0.65.0"

if ! command -v "$driver" >/dev/null 2>&1; then
    echo "error: xlsynth-driver was not found; set XLSYNTH_DRIVER to its absolute path" >&2
    exit 1
fi

actual_version=$($driver --version)
if [ "$actual_version" != "$expected_version" ]; then
    echo "error: expected '$expected_version', got '$actual_version'" >&2
    exit 1
fi

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/tiliqua-dslx-voice-test.XXXXXX")
trap 'rm -rf "$temporary_dir"' EXIT HUP INT TERM
actual_file="$temporary_dir/voice_test.actual"

"$driver" dslx-fn-eval \
    --dslx_input_file "$gateware_dir/dslx/voice.x" \
    --dslx_top voice_sample \
    --warnings_as_errors true \
    --input_ir_path "$gateware_dir/dslx/voice_test.irvals" \
    > "$actual_file"

diff -u "$gateware_dir/dslx/voice_test.expected" "$actual_file"
echo "DSLX voice vectors passed"
