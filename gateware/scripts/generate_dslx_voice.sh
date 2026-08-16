#!/bin/sh

# Copyright (c) 2026 Tiliqua contributors
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

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/tiliqua-dslx-voice.XXXXXX")
trap 'rm -rf "$temporary_dir"' EXIT HUP INT TERM

source_file="$gateware_dir/dslx/voice.x"
ir_file="$temporary_dir/voice.ir"
verilog_file="$temporary_dir/tiliqua_dslx_voice.v"
output_file="$gateware_dir/dslx/generated/tiliqua_dslx_voice.v"

"$driver" dslx2ir \
    --dslx_input_file "$source_file" \
    --dslx_top voice_sample \
    --warnings_as_errors true \
    --opt true \
    > "$ir_file"

"$driver" dslx2pipeline \
    --dslx_input_file "$source_file" \
    --dslx_top voice_sample \
    --delay_model unit \
    --pipeline_stages 1 \
    --module_name tiliqua_dslx_voice \
    --flop_inputs false \
    --flop_outputs false \
    --use_system_verilog false \
    --warnings_as_errors true \
    > "$verilog_file"

awk 'NF { last = NR } { lines[NR] = $0 } END { for (i = 1; i <= last; i++) print lines[i] }' \
    "$verilog_file" > "$output_file"
echo "generated $output_file"
