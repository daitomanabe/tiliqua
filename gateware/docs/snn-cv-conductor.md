# SNN modular-CV conductor

The `snn_av` top can map the 1024-neuron, 32-lane, 3:1 E/I ring to four
unipolar modular-control outputs:

| Channel | Signal |
|---:|---|
| 0 | 8 Hz master clock, 10 ms pulse |
| 1 | C minor pentatonic 1 V/oct pitch |
| 2 | 16-step SNN-density gate |
| 3 | smoothed population-activity modulation |

Run the complete simulation and R5 synthesis gate from `gateware/`:

```sh
pdm snn_lab cv
```

The live profile uses the 60 MHz sync clock for its clock and gate scheduler.
It therefore continues in wall time even if the SNN ready/valid stream is
backpressured. Compact self-test simulations keep the transfer-count scheduler.

The hardware build is:

```sh
pdm run python src/top/snn_av/top.py build \
  --hw r5 --modeline 720x720p60r2 \
  --neurons 1024 --physical-lanes 32 --ei-ring --cv-output \
  --name SNN-AV-1024X32-EI-CV-LIVE
```

The 2026-08-17 gate passed with 5,685 LUT4, 5,340 FF, 18 DP16KD, one DSP,
and 74.16 MHz sync-domain Fmax against a 60 MHz requirement. The resulting
`top.bit` SHA256 was
`0b6a7cf2e8cd213f699a0b2b3aac2ddb21eeef59de6049dece32b11426eca842`.

This top does not contain a SoC, so audio-board calibration constants stored in
EEPROM are not loaded automatically. Keep generic simulation values in the
gateware; apply device-specific root tuning or pitch conditioning in the
hardware integration layer.
