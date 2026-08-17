Tutorial 19: E/I SNNを4声のensembleとして聴く
=============================================

Tutorial 18ではOUT 0だけを音高へ変えました。この発展では1024-neuron / 32-lane / 3:1 E/I ringを保ち、
4つのphysical outputをすべてphase-continuousなtriangle voiceにします。映像、membrane state、結合、更新速度
49.152 M neuron-updates/sは変えません。

E/Iを別々に数える
------------------

``MemoryBatchedLIFBank`` は32-neuron rowを更新するとき、indexが4で割って3余る256 neuronを抑制性source、
残る768 neuronを興奮性sourceとして別々に加算します。各sampleで
``excitatory_spike_count + inhibitory_spike_count == spike_count`` をunit regressionが検査します。
細胞数が3:1なので、balance voiceは ``3 * inhibitory`` と ``excitatory`` を比較し、単純なraw count差を使いません。

4出力の役割
------------

.. list-table::
   :header-rows: 1

   * - output
     - 観測量
     - register / scale
     - control period
   * - OUT 0
     - total activity
     - C2--F3 C-minor pentatonic
     - 4096 sample / 85.33 ms
   * - OUT 1
     - excitatory population
     - C3--F4 C-minor pentatonic
     - 4096 sample / 85.33 ms
   * - OUT 2
     - inhibitory population
     - G2--C4 related pentatonic
     - 4096 sample / 85.33 ms
   * - OUT 3
     - normalized E/I balance
     - C1--Bb1 bass
     - 16384 sample / 341.33 ms

OUT 2は1/3のpopulationを表すため9/16 scale、他のvoiceは1/2 scaleです。equal-gainで監視したときに抑制性voiceが
埋もれない補正であり、実機のRMS比は1.125でした。4基のNCOは独立phaseを持ち、note changeでresetしません。

simulationとQoR
---------------

.. code-block:: bash

    cd gateware
    pdm snn_lab sonification
    pdm snn_lab sonification-report

15 tests + 3 subtestsと4-frame Verilator integrationをPASSしました。DVI checksumは単音版と同じ
``63562c9d861e7bb7`` です。3662 audio sampleのzero crossingはOUT 0--3で14 / 26 / 20 / 7、mean absoluteは
7437.0 / 7397.6 / 8276.1 / 7490.6 countsです。

.. list-table::
   :header-rows: 1

   * - item
     - result
   * - SHA-256
     - ``5ca49e5a1eddaec152d5f81085b440651cae05f545c59beb8d4107895689b61d``
   * - LUT4 / FF / DP16KD / DSP
     - 5836 / 5495 / 18 / 1
   * - physical COMB / FF / DP16KD
     - 38% / 22% / 32%
   * - sync / audio / dvi / dvi5x MHz
     - 69.57 / 69.90 / 66.07 / 350.39

全resourceとclock contractを通過しています。実験は揮発性SRAMだけを使い、SPI flashとcalibration EEPROMには触れません。

固定ES-9試験
-------------

private management repositoryで実行します。

.. code-block:: bash

    bin/tiliqua snn-ensemble-test

default commandは上のvalidated SHAと一致しなければdevice preflight前に停止します。試験はIN 0へ
``+/-0.5, +/-1, +/-2 V`` を送り、全4出力について許可音階へのperiod一致、低域energy、spectral flatness、RMS、
レジスター分離、抑制性gainを1 captureで検査します。2026-08-17の最終3 captureはすべてPASSしました。

.. list-table::
   :header-rows: 1

   * - voice
     - scale-period match range
     - RMS range
   * - total
     - 85.8--88.1%
     - 2.356--2.361 V
   * - excitatory
     - 88.5--90.4%
     - 2.320--2.321 V
   * - inhibitory
     - 87.4--89.9%
     - 2.642 V
   * - balance bass
     - 80.2--83.5%
     - 2.362--2.365 V

bass mappingの失敗
------------------

最初の4声candidateは各voiceの音階、flatness、RMSを個別にはPASSしましたが、OUT 3のfrequency medianがOUT 0と
ほぼ同じになり、register separationをFAILしました。原因はbalance tableがG2まで上がり、名前だけbassで実際の音域が
主旋律と重なっていたことです。frequency ratioを緩めず、table全体をC1--Bb1へ下げました。

次のcandidateはregisterをPASSしましたが、85.33 msで低音を切り替えると1 holdあたりの周期数が少なく、transition periodが
相対的に増えました。許可音を水増しせず、bassだけ341.33 msへ遅くしました。最終3 captureでは実音5音だけを許可したまま
80.2--83.5%一致を再現しています。

証明範囲
--------

4声はSNNのtotal/E/I/balance stateに制御されますが、音響周期そのものは4基のNCOが生成します。これはper-neuron oscillatorや
plasticityの証明ではありません。次は4出力を保ったまま、固定scale quantizationをneuron cluster、delay、weight、STDPの
状態へ段階的に置き換えます。
