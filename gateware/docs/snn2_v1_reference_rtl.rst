SNN2 v1 implementation and validation
######################################

This tutorial validates implementation phases 1--5 of
:doc:`snn2_specification`. The existing ``snn_av`` profile remains unchanged
and is the recovery baseline. None of these commands loads an FPGA or writes
SPI flash or calibration EEPROM.

1. Implemented vertical slice
==============================

The source boundary consists of:

* ``src/tiliqua/snn2/manifest.py`` -- canonical schema, topology checks,
  payload checksum, four edge-bank ROMs, and deterministic derived files;
* ``src/tiliqua/snn2/reference.py`` -- independent integer encoder, ALIF,
  sparse network, and four-output oracle;
* ``src/tiliqua/snn2/encoder.py`` -- serial eight-band DF2T encoder and
  deterministic self-test source;
* ``src/tiliqua/snn2/rtl.py`` -- 256-neuron, 16-lane state engine and no-drop
  sparse scheduler;
* ``src/tiliqua/video/snn2_visualizer.py`` -- 16x16 E/I state view, eight band
  meters, population rates, scheduler meter, and fault pixel;
* ``src/top/snn2_av/`` -- calibrated four-input/four-output AV top and
  six-frame Verilator harness;
* ``snn2/snn2_256x16_sparse_alif_v1.json`` -- deterministic bring-up network;
* ``snn2/snn2_population_contract.json`` -- frozen low, medium, and high drive
  population-rate and audio bounds;
* ``snn2/golden/single_neuron_traces.json`` -- immutable arithmetic boundary
  fixtures; and
* ``tests/test_snn2.py`` and ``scripts/snn2_lab.py`` -- regression, import,
  AV, build, and QoR interfaces.

The encoder freezes eight Q2.14 biquads with signed 18-bit state. A shared
multiplier uses separate multiply and commit cycles, so all bands finish in
104 of the 1,250 available sync cycles. Control inputs clamp at +/-4,000 ASQ
counts and use manifest-defined shift-add mappings; no divider is inferred.

The eight outgoing edges of every source are two rounds of four bank-distinct
targets. Four edge ROMs feed 16 event sub-banks selected by
``target_id[3:0]``. A registered inhibitory-gain stage breaks the edge-BRAM to
DSP path. State and event sums commit only after every source edge from the
previous spike vector has been inspected.

2. Verify the frozen manifest
=============================

From ``gateware`` run:

.. code-block:: console

   $ pdm snn2_lab doctor

Expected identity:

.. code-block:: text

   SNN2 lab doctor: PASS
     profile            snn2_256x16_sparse_alif_v1
     payload SHA-256    c4e46d763c7f85f9f2f9c67c7248e955c2d6091bc24b49f05f9c16b7e353d52d

The validator rejects an incorrect neuron count, malformed length, zero or
``-128`` edge weight, self-edge, duplicate target, wrong target bank, Dale-law
violation, invalid encoder mapping, and stale payload checksum.

3. Run source and AV regressions
================================

.. code-block:: console

   $ pdm snn2_lab quick
   $ pdm snn2_lab stress
   $ pdm snn2_lab check

``quick`` covers schema/export, golden arithmetic, frequency ordering,
backpressure, the DVI color contract, and reference/RTL equivalence. ``stress``
covers the 4,096-sample integer fixture and all-neurons-spike scheduler case.
``check`` runs every test and the Verilator AV contract.

.. list-table:: Final simulation evidence
   :header-rows: 1

   * - Metric
     - Contract
     - Current result
   * - Unit tests
     - all pass
     - 15 tests / 23 subtests
   * - Previous spikes
     - 256
     - 256
   * - Issued events
     - 2,048
     - 2,048
   * - Scheduler cycles
     - at most 640
     - 531
   * - Input-to-commit cycles
     - less than 1,250
     - 613
   * - DVI frames / pixels
     - at least 4 / 1,500,000
     - 6 / 3,340,375
   * - Neural samples
     - active for the AV run
     - 4,887
   * - Band activity
     - all eight bands
     - mask ``0xff``
   * - Maximum E / I spikes
     - both nonzero
     - 25 / 8
   * - DVI checksum
     - deterministic
     - ``9c36c09e7b718589``
   * - Latched fault
     - 0
     - 0

The four AV output ranges are ``-3194..3014``, ``0..2088``, ``0..368``, and
``0..2248`` for the deterministic self-test fixture.

4. Enforce R5 place-and-route
=============================

.. code-block:: console

   $ pdm snn2_lab --with-build check

This builds and evaluates both the encoder-bypassed ``SNN2-AV-LAB`` profile
and physical-input ``SNN2-AV-LIVE`` profile. A timing warning or a resource
ceiling violation fails the command even if ``top.bit`` exists.

.. list-table:: R5 QoR evidence for 720x720p60r2
   :header-rows: 1

   * - Metric
     - Contract
     - Self-test
     - Live input
   * - sync
     - at least 60 MHz
     - 76.18 MHz
     - 70.93 MHz
   * - audio
     - at least 12.288 MHz
     - 65.84 MHz
     - 64.57 MHz
   * - DVI
     - at least 39.07 MHz
     - 52.82 MHz
     - 47.00 MHz
   * - DVI5x
     - at least 195.35 MHz
     - 390.17 MHz
     - 406.01 MHz
   * - LUT4
     - at most 18,216
     - 8,582
     - 9,468
   * - TRELLIS_FF
     - at most 13,358
     - 6,473
     - 7,701
   * - DP16KD
     - at most 42
     - 39
     - 39
   * - MULT18X18D
     - at most 14
     - 5
     - 10
   * - Physical COMB
     - at most 75%
     - 61%
     - 68%
   * - Physical FF
     - at most 55%
     - 26%
     - 31%

The measured local bitstream SHA-256 values were
``782bf99a7a724659c81fd958710017cafaadd98ab0f8123fbf169d921554a352``
for self-test and
``191a9f41ea504eba4e91ef1b223591b169804568edf196911465681a87fb27df``
for live input. A commit changes the embedded repository tag, so rebuild and
record fresh bitstream hashes before SRAM validation.

5. Validate and derive another manifest
=======================================

``import-manifest`` performs no hardware operation:

.. code-block:: console

   $ pdm snn2_lab import-manifest /absolute/path/to/network.json

By default it writes below ``build/snn2-import/<sha-prefix>/``: canonical
``network.json``, four edge-bank HEX files, sixteen initial-state HEX files,
one readout-weight HEX file, and ``build_manifest.json`` containing every
derived-file SHA-256. Repeat export of the same input is byte-identical.

6. Remaining gates and safety boundary
======================================

The 4,096-sample fixture proves deterministic bounded behavior in the
independent integer model; the current all-state RTL equivalence fixture is
shorter. Low, medium, and high population-rate and audio ranges are frozen by
``snn2/snn2_population_contract.json`` and checked in every full test run.
Full 4,096-sample all-state RTL comparison remains a named regression gate.

No command on this page accesses USB, ``FLASH / DEBUG``, ``DEVICE / HOST``,
SPI flash, calibration EEPROM, ES-9, or physical outputs. Place-and-route does
not establish measured audio/CV or DVI behavior. Volatile SRAM validation must
use newly recorded bitstream hashes, conservative ES-9 levels, all four
returns, a bounded zero tail, and the separate scoped management workflow.
