SNN2 sparse adaptive spiking network specification
##################################################

Status
======

This document defines the ``SNN2`` profile for Tiliqua R5. ``SNN2`` is the
canonical name; it is a new design and does not replace or silently change the
existing ``snn_av`` profiles. The implementation status below records the
source, simulation, and place-and-route boundary without changing the
normative requirements.

The first implementation is called ``SNN2 v1``. It is a deterministic,
sample-synchronous, sparse adaptive leaky-integrate-and-fire network. The
integer reference model is normative: if a floating-point training model,
Python integer model, RTL simulation, or FPGA result disagree, the exported
integer manifest and the independent integer reference model define the
expected digital behavior.

Implementation status -- 2026-08-18
===================================

The reference-freeze, neuron-engine, sparse-scheduler, AV, and R5-closure
phases now have an independent Python integer model, separate Amaranth RTL,
Verilator integration harness, and enforced place-and-route contracts. The
checked-in deterministic bring-up manifest is
``snn2_256x16_sparse_alif_v1`` with payload SHA-256
``c4e46d763c7f85f9f2f9c67c7248e955c2d6091bc24b49f05f9c16b7e353d52d``.

The implemented regression boundary is:

* canonical manifest validation, Dale-law and topology rejection, stable
  SHA-256, and byte-identical BRAM export;
* signed 18-bit membrane, separate 16-bit E/I currents, 16-bit adaptation,
  refractory state, widened arithmetic, and saturating commit;
* 256 logical neurons on 16 state lanes;
* 2,048 sparse recurrent edges issued through four normative target banks and
  16 physical event sub-banks;
* exact event counts and a latched scheduler/deadline fault;
* immutable single-neuron golden traces and a 4,096-sample deterministic
  integer-model regression;
* reference/RTL comparison of every neuron state, spike, output, and scheduler
  counter across the current short multi-sample RTL fixture;
* an eight-band DF2T Q2.14 encoder with signed 18-bit filter state, exact
  center-frequency ordering, bounded shift-add CV mappings, and 104-cycle
  serial execution;
* four calibrated outputs, a dual-clock BRAM-backed 16x16 state display, band
  meters, E/I meters, scheduler status, and a latched fault indicator;
* a six-frame Verilator AV regression with 4,887 neural samples; and
* separate self-test and live-input R5 builds that enforce clocks and resource
  ceilings rather than accepting the existence of ``top.bit`` as a pass.

The all-neurons-spike RTL fixture issues all 2,048 events in 531 scheduler
cycles and commits one sample in 613 sync cycles, within the 640- and
1,250-cycle limits. These are simulation results.

The current phrase-stability music/CV profile is commit ``6942aa2``. Pitch and
gate density now approach their mapped targets by one musical scale step or
one Euclidean pulse per 125 ms control interval. Registering those targets
also breaks the threshold/compare path with one intentional control interval
of latency. The live-input build closes sync at 64.91 MHz with 71% physical
COMB, 7,716 FFs, 39/42 enforced ``DP16KD`` blocks, and 9/14 enforced DSPs. The
self-test build closes sync at 74.36 MHz with 61% physical COMB, 6,453 FFs,
39/42 ``DP16KD`` blocks, and 4/14 DSPs. Both also pass audio, DVI, and DVI5x
timing.

An intermediate unpipelined live candidate was stopped after 15 minutes 36
seconds without route convergence at conflict 182. Registering the performance
targets removed that combinational path; the replacement live build converged
and passed the 60 MHz sync contract at the 64.91 MHz result above.

The SHA-pinned bitstreams were loaded through ``FLASH / DEBUG`` to volatile
SRAM and measured through the fixed ES-9 fixture. The self-test returned
dynamic stereo audio, ``+0.836..+1.251 V`` pitch CV, a ``0/5 V`` gate, and a
stereo correlation of ``+0.099``. The live test changed pitch by ``+0.827 V``
and gate density by ``+0.252`` between the frozen low/high drive profiles. The
live bitstream with SHA-256
``3151fa54632c1205c828c17e2f3135e8395ac95f614210ae4f88ea1e6ef4fc52``
was then written only to the explicitly authorized slot 7. The scoped helper
made a full pre-write slot backup and passed device readback verification. The
trained manifest remains a named gate.
The full 4,096-sample all-state/event RTL comparison and frozen low, medium,
and high population-rate ranges run locally. See :doc:`snn2_v1_reference_rtl`
for commands and the exact validation scope.

Goals and non-goals
===================

SNN2 v1 shall provide:

* separate excitatory and inhibitory synaptic state;
* sparse, per-edge, signed weights;
* Dale's law, so a source neuron's outgoing edges have one sign;
* adaptive thresholds and an explicit refractory period;
* a deterministic audio-to-spike encoder and population readout;
* a worst-case schedule that cannot drop synaptic events;
* offline-trained, quantized weight import;
* bit-exact comparison between an independent model and RTL; and
* a separate R5 bitstream that can be tested in volatile SRAM.

SNN2 v1 does not attempt to implement Hodgkin--Huxley or conductance-based
biophysical membrane equations, a dense weight matrix, stochastic Poisson
spikes, runtime topology editing, per-edge transmission delay, or online STDP.
Those features must not be implied by the ``SNN2`` name. Per-edge delay and
bounded plasticity are later phases and require their own specification and
regression gates.

Why the first target is 256 neurons
===================================

The established 1024-neuron E/I live profile occupies 9,036/24,288 physical
COMB cells, 5,358/24,288 FFs, 18/56 ``DP16KD`` blocks, and one DSP. Its final
60 MHz sync result is 62.01 MHz. Memory and DSP headroom remain, but sync
timing has little margin for several new state memories, random synaptic
updates, and an event scheduler.

SNN2 therefore trades neuron count for richer state and connectivity. The v1
target is 256 logical neurons and 16 arithmetic lanes. Scaling to 512 neurons
is allowed only after the 256-neuron reference, worst-case scheduler, AV
simulation, and R5 QoR contracts all pass without weakening their limits.

Architecture
============

.. list-table:: SNN2 v1 fixed architecture
   :header-rows: 1

   * - Property
     - Requirement
   * - Logical neurons
     - 256
   * - Physical neuron lanes
     - 16
   * - Population
     - 192 excitatory and 64 inhibitory
   * - E/I assignment
     - neuron index ``3 mod 4`` is inhibitory; all others are excitatory
   * - Fan-out
     - exactly 8 stored edges per source neuron
   * - Total recurrent edges
     - 2,048
   * - Weight format
     - signed 8-bit two's complement, excluding ``-128``
   * - Recurrent delay
     - exactly one 48 kHz neural sample
   * - Synapse scheduler
     - four target banks, no event drops
   * - Neural sample rate
     - 48 kHz
   * - Sync clock
     - 60 MHz
   * - Available sync cycles
     - 1,250 per neural sample

The top-level signal flow is:

.. code-block:: text

   calibrated IN 0
       -> 8-band analysis bank
       -> deterministic rate encoders
       -> 8 excitatory input populations
       -> sparse signed recurrent ALIF network
       -> signed audio readout + E/I monitors
       -> calibrated OUT 0--3 and 16x16 DVI state view

   calibrated IN 1--3
       -> bounded encoder gain, inhibitory gain, and adaptation controls

All logical neurons observe one atomic previous-sample spike vector. A batch
written earlier in the physical schedule must never affect a later batch in
the same neural sample.

Neuron state and numeric rules
==============================

Each neuron stores the following state:

.. list-table:: Per-neuron state
   :header-rows: 1

   * - Name
     - Format
     - Meaning
   * - ``V``
     - signed 18-bit
     - membrane potential
   * - ``I_e``
     - unsigned 16-bit
     - excitatory synaptic current
   * - ``I_i``
     - unsigned 16-bit
     - inhibitory synaptic current magnitude
   * - ``A``
     - unsigned 16-bit
     - adaptive threshold increment
   * - ``R``
     - unsigned 4-bit
     - remaining refractory samples
   * - ``S``
     - one bit
     - committed spike for the current sample

All additions and subtractions shall use widened intermediates. State is
clamped once at the specified commit boundary; accidental wraparound is a
test failure. Right shifts are arithmetic for signed values and logical for
unsigned values. Decay parameters are integer shifts in the inclusive range
2--10.

For each neural sample, the implementation shall evaluate the following
integer recurrence in this order. ``sat_s18`` and ``sat_u16`` clamp to their
destination ranges.

.. code-block:: text

   Ie_next = sat_u16(Ie - (Ie >> tau_e) + excitatory_events)
   Ii_next = sat_u16(Ii - (Ii >> tau_i) + inhibitory_events)
   A_decay = sat_u16(A  - (A  >> tau_a))

   V_leak = V - (V >>> tau_m)
   V_candidate = sat_s18(
       V_leak + Ie_next - Ii_next + external_drive + bias
   )

   adaptive_threshold = threshold_base + A_decay

   if R != 0:
       S_next = 0
       V_next = reset_level
       R_next = R - 1
       A_next = A_decay
   elif V_candidate >= adaptive_threshold:
       S_next = 1
       V_next = reset_level
       R_next = refractory_samples
       A_next = sat_u16(A_decay + adaptation_step)
   else:
       S_next = 0
       V_next = V_candidate
       R_next = 0
       A_next = A_decay

Synaptic currents and adaptation continue to decay while a neuron is
refractory. The membrane is held at ``reset_level``. A spike committed for
sample ``t`` creates recurrent events consumed by sample ``t + 1``; no edge
has zero-sample feedback. Initial state, biases, thresholds, decay shifts,
adaptation step, refractory length, and reset level shall be present in the
network manifest and covered by its checksum.

Connectivity and Dale's law
===========================

Each edge is a 16-bit record:

.. code-block:: text

   bits  7:0   target neuron ID
   bits 15:8   signed weight

An excitatory source may contain weights ``+1..+127``. An inhibitory source
may contain weights ``-127..-1``. Zero, ``-128``, self-edges, duplicate
targets from one source, and a sign that violates the source population are
invalid. The topology compiler must reject them before simulation or build.

The sign belongs to the outgoing edge. An inhibitory neuron still emits a
binary ``0`` or ``1`` spike; it does not emit a negative spike value. Unlike
the original E/I ring, inhibitory spikes must not also enter an untyped,
positive global-recurrence term.

The 256 target neurons are divided into four banks using ``target_id[1:0]``.
Every source has two targets in each bank. Its eight edges are arranged as two
rounds of four bank-distinct targets. This fixed layout lets four scheduler
lanes issue one edge to every bank without an intra-round memory collision.

Events targeting the same neuron from different sources shall be summed in a
widened accumulator. Excitatory and inhibitory magnitudes are accumulated
separately. The final sum is clamped once per target at the neural-sample
boundary, making the result independent of source processing order. A bypass
or forwarding path must preserve consecutive updates to the same target.

Scheduler and deadline contract
===============================

When all 256 neurons spike, the scheduler must inspect all 2,048 edges. Two
four-edge rounds per source require 512 issue cycles. Pipeline fill, flush,
and same-target forwarding may add latency but may not drop, overwrite, or
merge events incorrectly.

The initial schedule budget is:

.. list-table:: 60 MHz / 48 kHz cycle budget
   :header-rows: 1

   * - Stage
     - Maximum cycles
   * - Sparse synapse issue, forwarding, and flush
     - 640
   * - Sixteen-lane neuron state update and commit
     - 256
   * - Encoder, readout, display shadow, and bookkeeping
     - 128
   * - Required deadline margin
     - 226
   * - Total
     - 1,250

The all-neurons-spike case is a required regression, not an assumed-impossible
state. ``event_count`` must equal ``popcount(previous_spikes) * 8`` and the
core must finish before the next 48 kHz sample. Event overflow, a missed
deadline, or accepting a new sample before atomic commit is a hard failure.

Audio and CV input contract
===========================

``IN 0`` is the signal input. It is split into eight analysis bands with
target center frequencies 80, 160, 320, 640, 1,280, 2,560, 5,120, and 10,240
Hz at 48 kHz. Exact filter coefficients and fixed-point widths must be frozen
in the manifest and verified against an independent impulse/frequency-response
fixture before the first RTL build. SNN2 v1 uses transposed direct-form II
Q2.14 coefficients and signed 18-bit filter state. One shared multiplier is
pipelined into multiply and commit cycles; all eight bands complete in 104
sync cycles.

Each band drives a deterministic phase-accumulator rate encoder. The absolute
band envelope, after bounded gain, advances its accumulator; overflow emits
one input spike. No random-number generator or Poisson approximation is used.
The eight encoders each inject into one group of 24 excitatory neurons, covering
all 192 excitatory neurons. Inhibitory neurons receive no direct encoder edge
in v1 and are recruited through the recurrent network.

The remaining calibrated inputs are bounded global controls:

.. list-table:: Live control inputs
   :header-rows: 1

   * - Input
     - Control
     - Normalized range
   * - ``IN 1``
     - encoder/external-drive gain
     - 0.5x--2.0x
   * - ``IN 2``
     - inhibitory edge magnitude multiplier
     - 0.5x--2.0x
   * - ``IN 3``
     - adaptation-step multiplier
     - 0.0x--2.0x

The mapping from calibrated ASQ units to these ranges shall clamp outside
``-1..+1 V`` and shall be bit-exact in the reference model. Self-test bypasses
the physical encoder and supplies a deterministic spike fixture while keeping
the same neuron, synapse, and readout paths.

Output and DVI contract
=======================

The four calibrated outputs have fixed meanings:

.. list-table:: SNN2 outputs
   :header-rows: 1

   * - Output
     - Meaning
   * - ``OUT 0``
     - signed learned audio readout, DC-blocked and saturated to the safe ASQ range
   * - ``OUT 1``
     - low-pass excitatory population activity, unipolar 0--5 V
   * - ``OUT 2``
     - low-pass inhibitory population activity, unipolar 0--5 V
   * - ``OUT 3``
     - population-size-normalized E/I balance, bipolar -5--+5 V

The ``OUT 0`` readout has one signed 8-bit coefficient per neuron. Multiplying
a one-bit spike by a coefficient is a conditional add, not a general
multiplier. The widened sum is filtered and saturated only at the documented
boundary. It must not alternate sign solely from sample parity as the original
diagnostic spike output did.

The DVI view is a 16x16 neuron grid. Excitatory and inhibitory base colors are
distinct, brightness represents membrane level, and a spike has a distinct
highlight for each population. The display may use a nearby state snapshot,
but it must not feed back into neural computation. A visible diagnostic area
shall include input-band activity, E/I rates, scheduler utilization, and a
latched red fault indication for an event-count or deadline invariant failure.

Music and modular-CV performance profile
----------------------------------------

``--performance`` preserves the SNN2 core and its compact DVI diagnostics but
replaces the 16x16 population grid with an additive-synthesis view and replaces
the four calibrated output roles with a playable profile:

.. list-table:: SNN2 music/CV outputs
   :header-rows: 1

   * - Output
     - Meaning
   * - ``OUT 0``
     - stereo music left: excitatory melody plus shared bass
   * - ``OUT 1``
     - stereo music right: inhibitory counter-voice plus shared bass
   * - ``OUT 2``
     - C-minor-pentatonic 1 V/oct pitch CV for the melody voice
   * - ``OUT 3``
     - bounded 0/5 V Euclidean activity-density gate

Additive-synthesis HDMI view
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The performance view visualizes the standalone Mac additive demo as its exact
structural product: five vertical chord-tone lanes, ten harmonic rows per lane,
and twenty micro-sine particles per cell. The complete field therefore contains
``5 * 10 * 20 = 1000`` visible oscillator particles. A brighter particle means
that the corresponding measured encoder band is active. Color identifies its
tone lane and harmonic row.

The top eight bars are the actual 8-band energy values derived from ``IN 0``.
Harmonic rows one through eight use the matching encoder band; the two highest
rows share the highest band because the analogue link transports eight bands,
not ten independent harmonic controls. The low horizontal bar below the field
shows the measured lowest-band foundation. The three lower meters retain
excitatory rate, inhibitory rate, and scheduler utilization, and the fault area
still turns red for a scheduler invariant failure.

Individual oscillator frequency, amplitude, and phase values are not sent from
the Mac over the mono analogue link. Particle phase motion is consequently a
deterministic structural animation made from frame, lane, harmonic, and
micro-sine indices. It shall not be described as a measurement of the Mac's
individual oscillator phases. Band brightness and the eight spectrum bars are
measured; the particle phase choreography is illustrative. Neither path feeds
back into neural or audio computation.

For the demo patch, the stereo master remains on ``Loopback Audio 2`` while a
separately switched, hard-limited ``+/-2 V`` mono copy leaves ES-9 physical
``OUT 1`` and enters Tiliqua ``IN 0``. All other ES-9 output channels remain
zero. Stopping the feed returns ``OUT 1`` to zero before closing its stream.

The three voices are phase-continuous triangle oscillators. E activity selects
the melody, I activity selects the counter-voice, and population-normalized E/I
balance selects the bass. Musical control advances at exactly 8 Hz from the
sync-domain wall clock; audio phase advances only on accepted stream transfers,
so backpressure cannot skip state or run an oscillator ahead of the stream.

Live input uses the ``sparse`` activity profile. It observes every 31st neural
sample across each 125 ms interval, accumulates E/I activity, and maps fractional
average firing rates into seven pitch and density bands. The prime stride avoids
locking the observation phase to the 16-lane scheduler. The frozen sparse range
starts below one spike per sample because physical characterization measured a
high-drive E-rate change of approximately 0.28 spikes/sample.

Self-test uses the ``instantaneous`` profile. Its deterministic source reaches
a much denser steady state, so averaging a complete interval would collapse the
pitch to one constant note. Sampling the current E/I counts at each 8 Hz boundary
retains a dynamic CV regression while leaving the live mapping unchanged.

The performance mapper latches pitch and density targets at each 8 Hz boundary.
The active pitch index then moves toward its target by at most one adjacent
C-minor-pentatonic scale step per 125 ms interval. The active Euclidean density
moves by at most one pulse per interval. This bounded slew suppresses abrupt
multi-note and multi-pulse control jumps while retaining deterministic mapping.
The extra target register is an intentional one-interval pipeline stage. The
melody starts at scale index zero, so pitch CV starts at the root ``0 V`` until
the first mapped movement.

The normal SNN2 output contract above remains the default when ``--performance``
is absent. Performance mode is therefore an explicit build profile rather than
a silent change to existing SNN2 bitstreams.

Successor: hardware additive instrument
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The mono analogue link and the illustrative particle animation above are an
interim demo. The optimized four-input/four-output instrument -- a framed
digital control link from the Mac UI, four analogue macro CVs on ``IN 0-3``,
a real time-multiplexed 1,000-oscillator engine on the FPGA, additive audio on
all of ``OUT 0-3``, and an HDMI view fed by committed bank state -- is
specified separately in :doc:`additive_specification`. It is a new
``ADDITIVE`` profile and does not change the SNN2 contracts above.

Training and manifest format
============================

Training is performed offline. A floating-point or surrogate-gradient model
may be used to discover parameters, but the result must be quantized and
replayed through the independent integer reference model before export. FPGA
RTL never consumes a framework-specific checkpoint directly.

The canonical JSON manifest contains at least:

.. code-block:: json

   {
     "schema_version": 1,
     "profile": "snn2_256x16_sparse_alif_v1",
     "sample_rate_hz": 48000,
     "neuron_count": 256,
     "lane_count": 16,
     "excitatory_count": 192,
     "inhibitory_count": 64,
     "fanout": 8,
     "numeric": {},
     "dynamics": {},
     "neurons": [],
     "edges": [],
     "encoder": {},
     "readout_weights": [],
     "training_provenance": {},
     "payload_sha256": "..."
   }

The exporter shall canonicalize ordering, reject non-integer or out-of-range
values, verify all population/connectivity invariants, and write BRAM
initialization files plus a derived build manifest. Re-running the exporter on
the same canonical JSON must produce byte-identical outputs. The SHA-256 covers
all parameters that can change neural behavior.

Runtime topology or weight writes are outside v1. Adding them requires an
authenticated or physically scoped transport, atomic bank switching, checksum
verification, and a recovery path; it must not mutate the compile-time profile
silently.

Reference model and regression gates
====================================

The Python integer reference must be structurally independent from the
Amaranth RTL. It shall not import the RTL component or copy intermediate HDL
signals as its oracle. The following gates are required:

1. Single-neuron golden traces cover positive and negative membrane state,
   all decay extremes, threshold equality, reset, refractory hold, adaptation,
   and every saturation boundary.
2. The topology compiler covers Dale violations, duplicates, self-edges,
   target-bank balance, malformed lengths, range errors, and deterministic
   export.
3. The scheduler covers zero spikes, one spike, same-target hazards, E/I
   cancellation, and all 256 neurons spiking without event loss.
4. Reference and RTL match for at least 4,096 consecutive neural samples in
   spike vector, all neuron states, E/I event sums, output samples, and
   scheduler counters.
5. Backpressure holds all committed state and output payloads stable and never
   repeats or skips a neural sample.
6. Low, medium, and high drive fixtures detect complete silence, sustained
   near-total firing, dead E or I populations, and output saturation. Numeric
   pass ranges must be frozen before hardware capture rather than adjusted to
   fit one observed run.
7. AV integration covers four complete DVI frames, all four output contracts,
   encoder band ordering, DVI population colors, and diagnostic fault pixels.
8. Place-and-route must satisfy every clock and the provisional resource
   contract. Producing ``top.bit`` alone is not a pass.

The provisional R5 resource ceilings are 75% physical COMB, 55% FF, 42/56
``DP16KD`` blocks, and 14/28 DSP blocks. Required clocks are sync 60 MHz, audio
12.288 MHz, DVI 39.07 MHz, and DVI5x 195.35 MHz for the established
``720x720p60r2`` profile. The first characterized build may tighten these
ceilings; a failing build is not grounds to loosen them without an architectural
review.

Command interface
=================

Before any command below is advertised as available, it must be implemented,
tested, and included in ``pyproject.toml`` or the management CLI. From
``gateware`` the intended development interface is:

.. code-block:: console

   $ pdm snn2_lab doctor
   $ pdm snn2_lab quick
   $ pdm snn2_lab stress
   $ pdm snn2_lab check
   $ pdm snn2_lab --with-build check
   $ pdm snn2_lab performance
   $ pdm snn2_lab performance-live
   $ pdm snn2_lab --with-build performance
   $ pdm snn2_lab --with-build performance-live
   $ pdm snn2_lab import-manifest /absolute/path/to/network.json
   $ pdm snn2_lab report

All listed commands are implemented. ``check`` runs the complete source and AV
simulation contract. ``--with-build check`` additionally builds and evaluates
both ``SNN2-AV-LAB`` and ``SNN2-AV-LIVE``. The self-test build fixes nextpnr
seed 2 because the default seed is reproducibly slow to route; the live build
uses the default seed.

``quick`` runs unit and bit-equivalence tests. ``stress`` runs the worst-case
scheduler and saturation fixtures. ``check`` adds AV integration. The
``--with-build`` form adds synthesis, place-and-route, and QoR enforcement.
``performance`` and ``performance-live`` apply the music/CV AV contracts to the
self-test and calibrated live-input profiles respectively. Their
``--with-build`` forms additionally enforce the R5 QoR ceilings.
``import-manifest`` validates and derives artifacts but never programs hardware.

Management-repository commands remain separate from the existing SNN commands:

.. code-block:: console

   $ bin/tiliqua snn2-test --bitstream /absolute/path/to/top.bit
   $ bin/tiliqua snn2-performance-test --bitstream /absolute/path/to/top.bit
   $ bin/tiliqua snn2-performance-live-test --bitstream /absolute/path/to/top.bit

They must require an explicit candidate bitstream until a specific SHA has
passed self-test, live response, control response, and output-safety gates in
one revalidation transaction.

Implementation phases
=====================

1. **Reference freeze:** implement the manifest validator and independent
   integer ALIF model, then commit immutable golden traces.
2. **Neuron engine:** implement 256 neurons, 16 lanes, E/I currents,
   adaptation, refractory behavior, and atomic sample commit without recurrent
   edges.
3. **Sparse scheduler:** add the banked 2,048-edge table, Dale enforcement,
   forwarding, exact counters, and worst-case deadline regression.
4. **AV path:** add the eight-band deterministic encoder, four readouts, DVI
   view, and four-frame Verilator contract.
5. **R5 closure:** synthesize, enforce resource/timing limits, and document
   every confirmed failure and recovery. Do not load hardware before this gate
   passes.
6. **Volatile hardware validation:** use the fixed ES-9 fixture, conservative
   levels, all four returns, a final bounded zero tail, and FPGA SRAM first.
7. **Bounded persistence checkpoint:** only after the exact bitstream SHA passes
   simulation, QoR, and volatile hardware validation, use an explicitly
   authorized flash slot, make a complete pre-write slot backup, write only
   that slot, and require device readback verification.
8. **Offline training:** import one quantized trained manifest and repeat every
   equivalence, QoR, and hardware gate under a new bitstream SHA.
9. **Scale study:** evaluate 512 neurons or per-edge delay only as a separate
   bounded experiment. Online STDP remains a later specification.

Hardware safety boundary
========================

SNN2 development does not by itself authorize an SPI flash write and never
authorizes a calibration EEPROM write. Simulation and QoR come first. A
hardware experiment shall use ``FLASH / DEBUG`` for the checked SRAM load or a
separately authorized bounded slot write, and ``DEVICE / HOST`` for runtime USB
audio/MIDI where required. It shall use the scoped management helper, match
exactly one configured R5 identity, record the bitstream SHA-256 and destination,
and leave all four physical outputs at zero on normal completion or failure.

An SPI flash candidate may be persisted only after the same SHA passes
simulation, QoR, and SRAM hardware validation and the user has explicitly
configured one allowed slot. The helper must back up the complete slot before
every write, reject every other offset, bind the operation to the selected USB
identity, and verify the written bytes by reading them back from the device.
The backup checksum and rollback command are part of the validation record.

The existing SNN remains the recovery and comparison baseline throughout SNN2
development. SNN2 must not replace its default hardware identity until the new
profile has independent simulation, synthesis, and measured evidence.
