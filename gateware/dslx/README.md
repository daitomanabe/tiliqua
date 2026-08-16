# DSLX audio/video tutorial cores

This directory contains Tiliqua audio and video logic authored in Google DSLX.
The generated Verilog is checked in so users can build FPGA images without
installing XLS.

- `reactor.x` follows audio amplitude and emits an envelope and gate.
- `adsr.x` advances a gate-driven attack/decay/sustain/release envelope.
- `voice.x` selects an oscillator waveform and applies saturating drive.
- `visualizer.x` maps those control values and pixel coordinates to RGB.

## Signal mapping

| Jack | Function |
| --- | --- |
| IN 0 | Audio or CV to analyse |
| IN 1 | Positive or negative threshold CV; values near 0 use the 1.024 V default |
| IN 2-3 | Reserved |
| OUT 0 | IN 0 passthrough |
| OUT 1 | Attack/release envelope |
| OUT 2 | 5 V gate with hysteresis |
| OUT 3 | Full-wave rectified instantaneous magnitude |

The envelope uses a 1/8 attack step and a 1/128 release step. The gate turns
on at the selected threshold and turns off at 7/8 of that threshold.

## Regenerating Verilog

The generator is pinned to `xlsynth-driver 0.65.0`, which embeds the Google XLS
`v0.54.6` DSO on macOS arm64. Install the driver, then run:

```bash
cd gateware
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/generate_dslx_reactor.sh
```

The script type-checks and optimizes `reactor.x`, then emits portable Verilog
to `generated/tiliqua_dslx_reactor.v`.

Run the fixed boundary and hysteresis vectors with:

```bash
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/test_dslx_reactor.sh
```

Generate and test the visualizer in the same way:

```bash
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/generate_dslx_visualizer.sh
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/test_dslx_visualizer.sh
```

Generate and test the resource-aware oscillator voice with:

```bash
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/generate_dslx_voice.sh
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/test_dslx_voice.sh
```

Generate and test the state-explicit ADSR step function with:

```bash
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/generate_dslx_adsr.sh
XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
  ./scripts/test_dslx_adsr.sh
```

The ADSR function itself is pure: the Amaranth `DSLXADSR` wrapper stores its
previous gate, phase, and level only when a stream transaction is accepted.
