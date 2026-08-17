SNN direct MIDI and front-panel control
#######################################

The live 1024-neuron, 32-lane E/I CV profile keeps calibrated analogue IN 0--3
as its default controls and adds a local absolute-override layer. This layer is
specific to the live profile; the deterministic self-test remains isolated.

Control priority
================

With no override, all four ready/valid samples pass through bit-for-bit. A TRS
MIDI event or encoder step enables an override for one parameter:

.. code-block:: text

   local TRS MIDI or knob override > calibrated analogue IN 0--3

CC 20 controls drive from 0 to +2 V. CC 21, 22, and 23 control leak,
recurrence, and threshold from -1 to +1 V. Program Change 0--7 recalls the
same eight four-parameter presets used by the macOS controller. CC 24 below 64
releases the selected parameter; CC 24 at or above 64 releases all parameters.

The compact MIDI decoder accepts Control Change and Program Change, preserves
running status, and ignores realtime bytes without disturbing a partial
message. Unsupported channel and system statuses clear running status. A
four-byte receive FIFO is sufficient because the decoder never backpressures
the UART in normal operation.

Front panel
===========

A short encoder-button press cycles drive, leak, recurrence, and threshold.
Turning the encoder copies the current analogue value when necessary, applies a
0.03125 V step, clamps to the parameter's safe range, and enables its override.
A one-second hold releases all overrides. The existing three-second hold still
returns to the bootloader through the RebootProvider component.

Four meters above the neural grid display the effective controls. The selected
meter has a white border and active overrides use a bright fill. The meters are
frame-rate status feedback rather than measurement outputs.

Regression and R5 result
========================

Run:

.. code-block:: console

   $ pdm run pytest -q tests/test_snn_control.py tests/test_snn.py
   $ pdm snn_lab cv

The focused simulation covers analogue transparency, bounded CC extremes,
running status, realtime interleaving, preset recall, encoder adjustment,
short-button selection, long-button release, and representative meter pixels.

The R5 live build closes every clock and stays inside the established CV
profile contracts: 6,001 LUT4, 5,520 FF, 18 DP16KD, one DSP, 39% physical
COMB, and 73.13 MHz sync for a required 60 MHz. The hardware-validated
volatile-SRAM bitstream SHA-256 is:

.. code-block:: text

   bc39ea61944664c2b32412058240eb8d522de87719a3858d79aaa089b733244c

Three fixed ES-9 fixture runs passed the unchanged clock, pitch, gate, and
modulation contracts. Physical encoder rotation and TRS cable injection still
require a hands-on check because the fixture has no actuator or MIDI patch.
