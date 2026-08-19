Tiliqua 1,000-sine additive instrument specification
#####################################################

Status
======

This document defines the ``ADDITIVE`` profile for Tiliqua R5: a hardware
additive-synthesis instrument with 1,000 conceptual sine oscillators, four
analogue modulation inputs, four additive audio outputs, a framed host
control link from the Mac UI, and an HDMI view driven by the real synthesis
state. It is the FPGA counterpart of the standalone Mac additive demo in the
``teliqua-management`` repository and supersedes the SNN2 ``--performance``
analogue link for that demo.

The integer reference model in ``src/tiliqua/additive/reference.py`` and the
frozen tables in ``src/tiliqua/additive/tables.py`` are normative. If RTL
simulation, Verilator integration, or hardware measurements disagree with the
reference, the reference defines the expected digital behaviour.

Implementation status -- 2026-08-19
===================================

Phase 1 (specification and deterministic reference) is implemented:
``tests/test_additive.py`` freezes the table contents, the default chord, the
1,000-oscillator structure, bounded outputs, exact mute, stem separation,
stereo coherence, independent CV effects, block-atomic frame commit, slow
phase-continuous harmony morph, and determinism.

Phase 2 (framed host control protocol) is implemented:
``tests/test_additive_control.py`` covers the CRC check value, encode/decode
round trips, start/version/length/CRC/bounds/stale rejection, resync after
garbage and truncation, the RTL decoder against the Python model byte for
byte, rejection counters and reasons, the inter-byte timeout, the link
watchdog, and the 115200 8N1 receiver decoding real serial bits.

Phase 3 (time-multiplexed oscillator core) is implemented:
``src/tiliqua/additive/engine.py`` (``AdditiveOscillatorBank``) holds the
1,000 phase accumulators in block RAM, updates one oscillator per sync cycle
through a five-stage pipeline with running increment and spread sums, forms
exact 20-voice group sums, multiplies each finished group by its four bus
gains in DSP tiles, accumulates four 41-bit buses, and runs the serial
saturate / soft-limiter / master / ceiling output stage. Parameters live in a
double-buffered block RAM swapped only at a sample start.
``tests/test_additive_engine.py`` proves sample-for-sample equality with the
reference over several control blocks for the default chord, a worst-case
all-harmonics zero-spread state under output backpressure, a muted state, and
a transposed/tilted GLASS state driven by changing CV, with every sample
finishing in exactly 1,027 sync cycles (limit 1,250) and no overrun fault.

Phase 4 (four additive outputs and safety) is implemented on the same RTL:
``OUT 0/1`` master stereo, ``OUT 2`` low/sub stem, and ``OUT 3`` air stem
are produced by the four bus gains of the parameter bank, and
``test_rtl_outputs_are_bounded_mute_exactly_and_keep_low_band_mono`` asserts
directly on RTL outputs that the worst-case all-harmonics zero-spread state
stays inside ``+/-8000 ASQ`` with every bus active, that ``stereo_width = 0``
gives bit-identical ``OUT 0`` and ``OUT 1``, that the air stem is bipolar,
and that a zero master with the same worst-case gains yields exact all-zero
output on all four channels. Long-window DC behaviour and the sub-stem
spectrum are reference gates that the sample-exact RTL inherits. No build or
hardware result exists yet for this profile.

Goals
=====

The instrument shall provide, all at once:

1. Mac UI editing of harmony, chord morph, ten harmonic levels, spectrum
   preset, phase spread, detune, LFO/evolution, stereo width, sub level, and
   master level through a framed digital control link.
2. Four independent live analogue modulations on Tiliqua ``IN 0-3``.
3. Exactly 1,000 sine oscillators -- 5 chord tones x 10 harmonics x 20
   micro-detuned voices -- defaulting to C minor 9 add 13 over a C1 sub.
4. Additive audio on all four Tiliqua ``OUT 0-3``.
5. A stereo return to ``Loopback Audio 2`` and Ableton through ES-9 ``IN 1-4``.
6. HDMI that visualises the committed oscillator-bank state and visibly
   responds to slow harmonic, phase, amplitude, and CV changes.
7. Levels bounded to ``+/-2.0 V`` on every output under every setting.

Architecture
============

.. code-block:: text

   Mac UI
     | framed control state (UART over the FLASH/DEBUG CDC, 115200 8N1)
     v
   control frame decoder -> pending UI base state
                                  |
   ES-9 OUT 1-4 -> Tiliqua IN 0-3 | block-rate control engine
     four analogue macro CVs ---->| (smoothing, evolution, CV modulation,
                                  |  normalization) -- commits once per
                                  v  128-sample block
   time-multiplexed 1,000-oscillator engine
     OUT 0 = stereo master L
     OUT 1 = stereo master R
     OUT 2 = low / sub stem        -> ES-9 IN 1-4 -> Loopback Audio 2 ch 1-4
     OUT 3 = upper-harmonic air stem
   committed bank state + statistics -> HDMI visualiser

Detailed control, analogue modulation, and audio return never share a lane.
The UI owns the base state; the four CVs modulate a smoothed copy of it and
never overwrite stored UI values.

Control transport
-----------------

The framed control link uses the RP2040 ``FLASH / DEBUG`` CDC-ACM serial port
bridged to the ECP5 ``uart`` resource (``rx`` = m2 pin 19 = ball ``A4``,
``tx`` = m2 pin 17 = ball ``B4``). The bridge supports 115200 8N1 only. This
path is electrically and logically independent from JTAG: SRAM loading uses
the ECP5 dedicated JTAG balls through the dirtyJtag interface of the same
composite device, and no gateware in this repository claims the ``uart`` pins
outside the optional ILA. The same composite device enumerates on macOS as
``/dev/cu.usbmodem*`` with USB serial ``E465343353094529`` on the configured
R5. Two guard rails apply:

* The FPGA never transmits on ``tx`` in v1. The RP2040 firmware inspects
  FPGA-to-host traffic for ``BITSTREAM<n>`` keywords and would reconfigure
  the FPGA; a silent ``tx`` makes that impossible.
* The host never opens the port at 1200 baud, which resets the RP2040 into
  its UF2 bootloader.

USB MIDI on ``DEVICE / HOST`` is not used: the repository only contains a USB
MIDI *host*, so a Mac cannot attach to it without new device-class gateware.

Control frame protocol
----------------------

``src/tiliqua/additive/protocol.py`` is the normative encoder/decoder and
``src/tiliqua/additive/control.py`` the RTL decoder (``ControlFrameDecoder``)
plus the 115200 8N1 receiver (``AdditiveControlLink``). One 45-byte frame
carries the complete control state:

.. code-block:: text

   offset  size  field
   0       1     start byte 0xA5
   1       1     start byte 0x5A
   2       1     protocol version = 1
   3       1     sequence number, wraps modulo 256
   4       1     payload length = 38
   5       38    payload (below)
   43      2     CRC-16/CCITT-FALSE over bytes 2..42, big-endian

   payload (little-endian)
   0  u8   root_midi            1  u8   harmony
   2  u8   flags: bit0 sub_octave, bit1 output_enabled
   3  u8   morph_seconds        4  u8   evolution_rate_mhz    5  u8 reserved
   6  u16  master               8  u16[10] harmonic_levels
   28 u16  detune_millicents    30 u16  phase_spread
   32 u16  evolution_amount     34 u16  stereo_width          36 u16 sub_focus

Acceptance rules, applied in this order by both the Python model and the RTL:

1. start bytes, version, and length must match; any other byte while
   hunting is ignored and ``0xA5`` restarts the start sequence;
2. the CRC (poly ``0x1021``, init ``0xFFFF``, check value ``0x29B1`` for
   ``"123456789"``) must match;
3. every field must satisfy the control-state bounds (reserved bits are
   ignored);
4. the sequence must be fresh: the first frame after reset is always fresh,
   afterwards ``(sequence - last) mod 256`` must lie in ``1..127``, so a
   duplicate retry or an older frame is rejected as stale;
5. an inter-byte gap longer than 20 ms inside a frame aborts it, so a
   truncated frame can never swallow the next one.

A rejected frame leaves the last accepted payload untouched. The RTL keeps
16-bit accepted/rejected counters, the last reject reason (``start``,
``version``, ``length``, ``crc``, ``bounds``, ``stale``, ``timeout``), and a
``link_alive`` flag that drops 2 s after the last accepted frame. After reset
the decoder presents the specification default state. The FPGA never
transmits, so the host only needs heartbeat re-sends (with a new sequence
number) to keep ``link_alive`` high; an unchanged heartbeat payload is
accepted and merely re-arms the watchdog.

Musical model
=============

.. list-table:: Oscillator structure
   :header-rows: 1

   * - Property
     - Requirement
   * - Chord tones
     - 5, intervals from the harmony table below
   * - Harmonics per tone
     - 10, integer multiples 1..10 of the tone frequency
   * - Voices per harmonic
     - 20, placed at ``(2k - 19) / 19`` of the detune half-span, ``k = 0..19``
   * - Oscillators
     - exactly 1,000 independent 32-bit phase accumulators
   * - Default root
     - MIDI 36 (C2); tone 0 drops one octave to C1 = 32.703 Hz
   * - Default harmony
     - ``MINOR 9 + 13`` = intervals ``0, 3, 10, 14, 21``
   * - Default spectrum
     - ``WARM`` = ``1.00, 0.52, 0.34, 0.24, 0.18, 0.14, 0.11, 0.085, 0.068, 0.052``
   * - Tone weights
     - ``1.00, 0.84, 0.74, 0.64, 0.56``

.. list-table:: Harmony table (semitones above the root, tone 0..4)
   :header-rows: 1

   * - Index
     - Name
     - Intervals
   * - 0
     - OPEN FIFTH
     - 0, 7, 12, 19, 24
   * - 1
     - MINOR 9 + 13
     - 0, 3, 10, 14, 21
   * - 2
     - MINOR 9
     - 0, 3, 7, 10, 14
   * - 3
     - MAJOR 9
     - 0, 4, 7, 11, 14
   * - 4
     - SUSPENDED
     - 0, 2, 7, 9, 14
   * - 5
     - QUARTAL
     - 0, 5, 10, 15, 20
   * - 6
     - LYDIAN AIR
     - 0, 4, 7, 11, 18

Resource reduction must not change this declared 1,000-oscillator model. A
mathematically compressed bank is only permitted after a direct-sum
equivalence test against this reference exists.

Control state
=============

One host frame carries the complete UI base state. All unit-range values use
unsigned Q1.15 (``0..32767``).

.. list-table:: Control state fields
   :header-rows: 1

   * - Field
     - Range
     - Default
   * - ``root_midi``
     - 24..60
     - 36
   * - ``harmony``
     - 0..6
     - 1
   * - ``sub_octave``
     - 0/1
     - 1
   * - ``output_enabled``
     - 0/1
     - 1
   * - ``master``
     - Q1.15
     - 0.42
   * - ``harmonic_levels[10]``
     - Q1.15 each
     - WARM
   * - ``detune_millicents``
     - 0..18000
     - 5000
   * - ``phase_spread``
     - Q1.15
     - 0.58
   * - ``evolution_amount``
     - Q1.15
     - 0.62
   * - ``evolution_rate_mhz``
     - 5..80
     - 20
   * - ``stereo_width``
     - Q1.15
     - 0.82
   * - ``morph_seconds``
     - 2..120
     - 18
   * - ``sub_focus``
     - Q1.15
     - 0.36

A frame outside these ranges is rejected whole. An accepted frame becomes the
current base state at the next control-block start, never mid-block. After
power-up and before any frame, the FPGA plays the default state above; the
instrument is therefore usable standalone with CV only.

Analogue modulation contract
============================

Calibrated inputs are sampled every audio sample, averaged over each
128-sample block, normalized with ``4000 ASQ = 1 V``, then smoothed with a
1/8 one-pole per block (about 21 ms).

.. list-table:: IN 0-3 roles
   :header-rows: 1

   * - Input
     - Voltage
     - Normalized
     - Effect on the smoothed UI base state
   * - ``IN 0``
     - 0 to +2 V
     - ``cv0 = clamp(asq * 8389 >> 11, 0, 32767)``
     - ``master += cv0 / 2``; ``sub_focus += cv0 / 2`` (both clamped to 1.0)
   * - ``IN 1``
     - -1 to +1 V
     - ``cv1 = clamp(asq * 8389 >> 10, +/-32767)``
     - all oscillators transposed by ``cv1 * 2`` semitones (``+/-1 V`` = ``+/-2 st``)
   * - ``IN 2``
     - -1 to +1 V
     - ``cv2`` as above
     - harmonic ``h`` (0-based) scaled by ``2 ** (cv2 * 2 * h / 9)`` octaves
   * - ``IN 3``
     - -1 to +1 V
     - ``cv3`` as above
     - ``phase_spread``, ``evolution_amount``, ``stereo_width`` each ``+= cv3 / 2``

CV offsets are added to the *smoothed* UI values and clamped; the stored UI
state is untouched. While ``output_enabled`` is zero, ``IN 0`` cannot reopen
the output.

Output contract
===============

.. list-table:: OUT 0-3 roles
   :header-rows: 1

   * - Output
     - Meaning
   * - ``OUT 0``
     - stereo master left
   * - ``OUT 1``
     - stereo master right
   * - ``OUT 2``
     - low / sub stem: every group whose smoothed base frequency is at or
       below 120 Hz, plus the sub-focus term, centred mono
   * - ``OUT 3``
     - air stem: every group whose smoothed base frequency is at or above
       600 Hz, lifted +12 dB before limiting because its voices are
       decorrelated

Every output is bounded to ``+/-8000 ASQ`` (``+/-2.0 V``) by construction and
additionally clamped at that ceiling. Low groups are panned centre, so their
contribution is bit-identical on ``OUT 0`` and ``OUT 1``. ``stereo_width = 0``
makes ``OUT 0`` and ``OUT 1`` bit-identical.

Numeric rules
=============

.. list-table:: Fixed-point contract
   :header-rows: 1

   * - Quantity
     - Format
   * - Phase accumulator
     - unsigned 32-bit, wraps; ``increment = round(f * 2**32 / 48000)``
   * - Sine table
     - 4,096 x signed Q1.15, indexed by the top 12 phase bits, no interpolation
   * - Unit parameters
     - unsigned Q1.15; smoothed copies carry 8 extra fraction bits
   * - Smoothing coefficients
     - Q0.24 per 128-sample block: 0.45 s parameters, 0.40 s master,
       morph table indexed by ``morph_seconds`` (95 % in that many seconds)
   * - Mute
     - linear ramp of ``2**15`` Q8 units per block to an exact zero master
   * - Group sum
     - 20 voices summed exactly (21-bit), then ``>> 3`` (18-bit)
   * - Per-group bus gains
     - unsigned 17-bit, ``GAIN_UNIT = 52430`` represents unit normalized weight
   * - Bus accumulator
     - ``sum(group_sum18 * gain)``, ``>> 19`` (``>> 17`` for the air stem)
   * - Normalized mix ``y``
     - signed Q3.13 saturated at ``+/-32767``
   * - Limiter
     - linear below 0.70, exponential soft knee to 1.0 from a 1,024-entry table
   * - Master
     - ``out = limiter(y) * master_asq >> 15``, ``master_asq = master * 8000 >> 15``
   * - ``exp2``
     - 1,024-entry fractional-octave table, integer octave as shift, Q16.16
   * - Pan
     - 256-entry equal-power ``sqrt`` table, index ``(32767 -/+ pan) >> 8``
   * - Stems
     - membership by increment thresholds 120 Hz and 600 Hz on the
       morph-smoothed base increment

Control block algorithm
-----------------------

Once per 128 samples, in this order:

1. Apply a pending frame, if any.
2. Average the four CV accumulators, normalize, one-pole smooth (``>> 3``).
3. Smooth ``detune``, ``phase_spread``, ``evolution_amount``,
   ``stereo_width``, ``sub_focus`` (0.45 s) and ``master`` (0.40 s, or the
   linear mute ramp when the output is disabled).
4. Derive effective values by adding the CV offsets; compute the pitch ratio
   ``exp2(cv1 * 171 / 1024 octaves)`` and the sub-mix scales from
   ``sub_focus``.
5. Advance the timbre, pitch (x0.73), and stereo (x0.51) LFO phases by
   ``evolution_rate_mhz`` x the per-block increment table.
6. For each of the 50 groups: morph-smooth the base increment toward
   ``NOTE[note] * harmonic``; classify low/air; add the pitch drift
   (``2.2 cents x evolution x sin(pitch LFO + group offset)``); multiply by
   the pitch ratio; derive the detune half-spacing and the phase-spread
   half-offset; compute the weight-motion target and smooth the weight;
   apply the harmonic tilt; compute the pan (0 for low groups).
7. Sum the squared left, right, low, and air weights; ``isqrt`` each with a
   floor of 256; ``norm = GAIN_UNIT << 16 / root``. The stereo pair shares
   the louder side's root.
8. Commit, atomically, per group: midpoint increment, spacing half increment,
   spread half phase, and the four bus gains (group 0 also carries the
   sub-focus gain on left, right, and low).

Per-sample engine
-----------------

For every oscillator ``o = 20 g + k``:

.. code-block:: text

   increment_o = midpoint_g + (2k - 19) * spacing_half_g      (mod 2**32)
   phase_o    += increment_o                                   (mod 2**32)
   sine_o      = SINE[(phase_o + (2k - 19) * spread_half_g) >> 20]

   group_sum_g = sum_k sine_o                     (exact)
   bus_c       = sum_g (group_sum_g >> 3) * gain_c,g
   y_c         = saturate13(bus_c >> shift_c)
   out_c       = clamp(limiter(y_c) * master_asq >> 15, +/-8000)

The phase-spread offset is applied at read time, so changing spread or
detune never discontinues a phase. Phases keep advancing while muted.

Safety and mute
===============

* ``output_enabled = 0`` ramps the master to exactly zero within 256 blocks
  (0.68 s from full scale) and all four outputs read exactly ``0``
  thereafter; CV cannot reopen the output.
* The limiter output never exceeds ``+/-32767`` and the master never exceeds
  ``8000 ASQ``, so ``+/-2.0 V`` holds for every harmonic, phase, detune, CV,
  and chord setting, including all levels at 1.0 with zero spread and
  detune and all CVs at their extremes.
* The engine is deterministic and DC-free apart from the sub fundamental.

Reference model and regression gates
====================================

``tests/test_additive.py`` is the phase-1 gate and must keep passing:

1. tables: 1,000 oscillators, odd-symmetric full-scale sine, monotonic
   ``exp2``/pan/limiter tables with anchored values, 48 kHz increments,
   CV normalization anchors;
2. state: default C minor 9 add 13 over C1, range validation;
3. frequencies: every tone/harmonic centre within 0.3 %, 20 distinct voices
   spanning 10 cents at 5 cents detune;
4. bounds: every output inside ``+/-8000``, worst-case settings with and
   without extreme CV, gains inside 17 bits;
5. mute: exact zero tail, phases keep running, resume works, CV cannot
   reopen;
6. stems: ``OUT 2`` at least 98 % of its energy below 150 Hz, ``OUT 3`` at
   least 98 % above 550 Hz, low groups ``0, 1, 2, 10, 20`` for the default
   chord;
7. stereo: low band bit-identical gains, ``stereo_width = 0`` is exact mono;
8. each CV independently: ``IN 0`` doubles the master and raises the sub,
   ``IN 1`` transposes every oscillator by ``2 ** (+/-2/12)``, ``IN 2``
   changes the 10th/1st harmonic ratio by x4 / x0.25, ``IN 3`` widens the
   phase spread, evolution, and stereo width; the stored UI state is never
   modified;
9. frames commit only at block boundaries;
10. harmony morph is monotonic, reaches 94 % within ``morph_seconds``, and
    phases follow ``phase + increment`` exactly;
11. master smoothing is monotonic without overshoot;
12. two instances produce identical output for the same CV stream.

Later phases add, as separate gated commits: the CV layer; the HDMI state view with pixel-level tests; the Mac transport and
ES-9 return bridge; full simulation and R5 QoR; SRAM-only hardware
validation; and demo packaging.

Provisional R5 contracts
========================

The profile shall meet sync 60 MHz, audio 12.288 MHz, DVI 39.07 MHz, and
DVI5x 195.35 MHz for ``720x720p60r2``, and provisionally at most 75 %
physical COMB, 55 % FF, 42 ``DP16KD``, and 14 ``MULT18X18D``. Every audio
sample must complete its 1,000 oscillator updates, bus flush, limiting, and
output handoff within 1,250 sync cycles, asserted in simulation. A generated
``top.bit`` without a final timing PASS is a failure.

Hardware safety boundary
========================

The SNN2 hardware boundary applies unchanged: simulation and QoR first,
volatile SRAM through the scoped session helper bound to the single
configured R5 identity, recorded bitstream SHA-256, no SPI flash or
calibration EEPROM writes for development, and all ES-9 stimulus paths
finishing with an all-zero tail.
