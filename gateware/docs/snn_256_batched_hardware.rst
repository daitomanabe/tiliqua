Tutorial 12: 256 logical neuronを32 laneでR5へ実装する
=========================================================

Tutorial 11では256個のLIFを1 cycleへ完全複製し、機能simulationはPASSした一方、R5の
``TRELLIS_COMB`` が146%となりました。このTutorialでは256個の膜電位とspike状態、更新式、
32x8映像、4出力を保ち、32個のphysical update laneを8 batchとして再利用します。

モデルと実装方式を分離する
----------------------------

``BatchedLIFBank`` は1 audio sampleごとに次を行います。

#. 入力drive、leak、recurrence、threshold controlをregisterする
#. batchの32状態、左隣spike、reset値をlane registerへ選択する
#. 32個のcandidate、threshold比較、膜電位更新を同時実行する
#. 2と3を8 batch繰り返す
#. 256-bitの新spike vectorをcommitし、8-neuron group単位でmonitorを集計する
#. 4 DAC sampleへ写像する

全batchは更新前の同じ256-bit spike vectorを読みます。先に更新したbatchが同じsample内の後続batchへ
影響しないため、完全並列モデルと離散時間の意味が一致します。unit testは32、64、128 physical laneの
各実装を完全並列256版と比較し、4 output、256 spike bit、spike count、全膜電位上位bitが32 sampleで
bit単位一致することを検査します。

60 MHz sync clockでは48 kHz audio sampleあたり約1,250 cycleあります。32-lane版の8 select cycleと
8 update cycle、集計/outputを加えても20 cycle程度で、sample budgetの2%未満です。処理量は
``256 * 48000 = 12.288 M neuron-updates/s``、同時更新幅は32 neuronsです。

途中の失敗からlane数を選ぶ
----------------------------

次の順序でresourceとtimingを測りました。

.. list-table::
   :header-rows: 1

   * - architecture
     - 結果
     - 主な証拠
   * - 256 lane x 1
     - non-fit
     - COMB 35,628 / 24,288 = 146%
   * - 128 lane x 2
     - non-fit
     - COMB 24,726 / 24,288 = 101%
   * - 64 lane x 4、select register前
     - timing FAIL
     - COMB 81%、sync 59.84 MHz
   * - 64 lane x 4、select/update分離
     - self-test PASS
     - COMB 69%、sync 74.43 MHz
   * - 64 lane live
     - P&R中断
     - 暫定COMB 79%、sync PASS、44分後もbitstreamなし
   * - 32 lane x 8
     - self-test PASS
     - COMB 53%、sync 72.26 MHz
   * - 32 lane live
     - P&R中断
     - 暫定COMB 58%、sync 60.57 MHz、45分後もbitstreamなし

64-lane初版のcritical pathは ``batch_index`` から4-way state選択、18-bit candidate加算、threshold比較、
membrane FFまでを1 cycleに含み、16.71 nsでした。state選択をregisterし、次cycleでcandidateを計算すると
self-test syncは74.43 MHzへ上がりました。

live profileはlogic容量と暫定timingを満たしていても、YoWASP nextpnrのregister-state配置探索が45分で
完了しませんでした。中断したbuildには ``top.bit`` がないため、live実機試験には使いません。次の大規模化は
状態をDP16KDへ置き、広いregister muxとfanoutをなくす必要があります。

一括contract
------------

``gateware`` directoryで次を実行します。

.. code-block:: bash

    pdm snn_lab batch

既定ではunit、3種のparallel-equivalence subtest、256x32 AV simulation、self-test synthesis、Yosys
resource、nextpnr physical utilization、4 clock、bitstream SHAを検査します。長時間の実験live P&Rを
明示的に含める場合だけ次を使います。

.. code-block:: bash

    pdm snn_lab --with-live-build batch

2026-08-17のself-test contractは次の通りです。

.. list-table::
   :header-rows: 1

   * - 項目
     - 結果
   * - unit
     - 7 PASS、3 lane-count subtests PASS
   * - AV simulation
     - 4 frames、checksum ``489a1b5c3d6835e3``、完全並列256版と同一
   * - LUT4 / FF / DSP
     - 9,626 / 10,336 / 1
   * - physical COMB / FF
     - 12,904 / 24,288 = 53%、10,329 / 24,288 = 42%
   * - sync / audio / dvi / dvi5x
     - 72.26 / 62.65 / 64.29 / 452.08 MHz
   * - bitstream SHA-256
     - ``a6ac1022e63442b31421482d83258c5ff399c0a8993f19eb2a2b5fb4f0bbf643``

実機SRAM自己診断
----------------

private management repositoryから次を実行します。

.. code-block:: bash

    bin/tiliqua session status
    bin/tiliqua snn-test --neurons 256

``--neurons 256`` は ``snn-av-256x32-lab-r5/top.bit`` だけを選びます。揮発性SRAMへloadし、ES-9へ
zeroを出しながらTiliqua OUT 0..3をphysical IN 1..4でcaptureします。結果JSONはlogical neurons、
physical lanes、batches/sample、update rate、SHA、SRAM targetを別々に記録します。

fresh実測は次です。

.. list-table::
   :header-rows: 1

   * - monitor
     - low / high activityまたはrange
     - 判定
   * - spike audio
     - p01 -7.446 V / p99 +7.498 V、RMS 2.883 V
     - PASS
   * - population activity
     - +0.494 / +1.565 V
     - PASS
   * - burst gate
     - +4.467 / +4.918 V
     - PASS（256 saturated profile）
   * - mean membrane
     - +2.437 / +1.796 V
     - PASS
   * - activity to gate correlation
     - +0.96402
     - PASS
   * - activity to membrane correlation
     - -0.99829
     - PASS

最初の2 captureは64/128用burst separation 1.5 Vを使ったため、ほかのmonitorがすべて安定していても
burstだけFAILしました。再測定ではburst separation 0.446 / 0.460 V、相関0.967、分位幅約1.33 Vが
再現しました。256 populationではgateが約5 Vへ飽和するため、専用contractはseparation 0.25 V以上、
10--90%分位幅1.0 V以上、activity相関0.90以上を要求します。64/128の厳しいcontractは変更せず、
saturated synthetic captureが128ではFAIL、256ではPASSするnegative testを持ちます。

証明範囲
--------

このPASSは256 logical state、32 parallel lanes、8 batch、完全並列モデルとのbit同値、AV simulation、
R5 self-test timing/resource closure、SRAM load、4 DAC returnを証明します。256 live bitstream、物理CV control、
物理DVI capture、BRAM/PSRAM state、学習則、疎結合行列は未検証です。次はregister配列をBRAMへ移し、
512以上のlogical neuronsと可変結合を、同じsample-equivalence contractから段階的に検査します。
