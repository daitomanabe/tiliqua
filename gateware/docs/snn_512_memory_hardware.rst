Tutorial 13: 512 logical neuronの状態をDP16KDへ移す
=====================================================

Tutorial 12の256x32版はR5で動作しましたが、256個の16-bit膜電位をregisterに保持するため、batch選択MUXと
広いfanoutが残りました。このTutorialでは論理ニューロン数を512へ倍増し、32 physical laneを16 batchで
再利用しながら、膜電位本体8,192 bitをECP5の ``DP16KD`` block RAMへ移します。

512-bit rowで32状態を同時に読む
----------------------------------

``MemoryBatchedLIFBank`` はdepth 16、width 512の同期memoryを定義します。1 wordは32個の16-bit膜電位を
連結した1 batchです。Yosys/ECP5 mappingは幅方向へ15個の ``DP16KD`` を並べます。各audio sampleでは次を
実行します。

#. input driveと3 CV modeをregisterする
#. batch addressを同期read portへ発行する
#. read dataを512-bit row registerへ取り込む
#. 次cycleで32 candidateを並列計算してregisterする
#. threshold比較、reset選択を行い、同じaddressへ1 rowを書き戻す
#. 1--5を16 batch繰り返す
#. 512-bitの新spike vectorを一度にcommitする
#. group reduction、4 DAC monitor、64x8 DVI snapshotを更新する

全batchの隣接spikeは更新前の512-bit vectorから読みます。このdouble-buffer相当の規則により、先に処理した
batchのspikeが同じsample内の後続batchへ漏れません。膜電位上位4 bitだけはDVI snapshotとmonitor集計用の
2,048-bit shadow registerへ書きます。16-bit本体はblock RAMにだけ保持します。

60 MHzでは48 kHz sampleあたり1,250 cycleあります。16 read + 16 row capture + 16 candidate + 16 writeと
集計/outputの約68 cycleはsample budgetの6%未満です。論理処理量は ``512 * 48000 = 24.576 M neuron-updates/s`` 、
物理同時幅は32です。

完全並列モデルとの同値証明
----------------------------

unit testは同じ初期膜電位、drive、leak、recurrence、thresholdで完全並列512版とmemory版を16 sample動かし、
各sampleの4 DAC値、512 spike bit、spike count、512個の膜電位上位4 bitを比較します。全項目がbit単位で一致
しなければAV simulationやsynthesisへ進みません。

``gateware`` directoryで一括実行します。

.. code-block:: bash

    pdm snn_lab memory

このcommandはunit/equivalence、512x32 AV simulation、R5 self-test build、resource/timing contract、bitstream
SHAを順に検査します。既存bitstreamだけを再評価する場合は次を使います。

.. code-block:: bash

    pdm snn_lab memory-report

AV simulationは4 frame、2,534,824 active pixel、DVI checksum
``30f0961041edfc63`` を2回連続で生成しました。4 audio monitorは3,662 sampleを持ち、spike audioは
-29,494..+29,490、activityは0..29,490、burstは0..17,998、membraneは0..10,626でした。

R5 synthesis contract
---------------------

.. list-table::
   :header-rows: 1

   * - 項目
     - 結果
   * - unit
     - 8 PASS、3つの256 lane-count subtest PASS、512 memory equivalence PASS
   * - LUT4 / FF / DSP
     - 12,002 / 9,534 / 1
   * - DP16KD
     - 15 / 56 = 26%
   * - physical COMB / FF
     - 15,734 / 24,288 = 64%、9,527 / 24,288 = 39%
   * - sync / audio / dvi / dvi5x
     - 64.33 / 61.13 / 55.56 / 276.70 MHz
   * - bitstream SHA-256
     - ``761b41dda0ebde90338e0abe70383e264ce648b5e8207a178f2490c568b4b5b2``

最終版のplacement予測はsync 52.38 MHzでしたが、routing完了後は64.33 MHzへ改善しました。容量が合法で
routingが進行中の場合、placement値は診断値であり最終contractではありません。routed critical pathは32 lane
candidateではなく、512個の膜電位上位nibbleをgroup reductionして ``membrane_mean`` へ入れる15.54 ns経路でした。
次の大規模化ではmonitor reductionをもう1段registerする余地があります。

``DP16KD=15`` を必須の上限契約へ含めます。これが0になった場合はmemoryがFF/LUTへ展開された退行であり、
bitstreamが生成されても大規模化の目的を満たしません。physical ``TRELLIS_COMB`` と ``TRELLIS_FF`` 、4 clockも
別々に制限します。

実機SRAM自己診断
----------------

private management repositoryの常設ES-9 fixtureで次を実行します。

.. code-block:: bash

    bin/tiliqua snn-test --neurons 512

2026-08-17のfresh captureはSHA
``761b41dda0ebde90338e0abe70383e264ce648b5e8207a178f2490c568b4b5b2`` を揮発性SRAMへloadし、
次を測定しました。

.. list-table::
   :header-rows: 1

   * - monitor
     - 実測
   * - spike audio
     - p01 -5.983 V、p99 +6.136 V、RMS 2.767 V
   * - population activity
     - low/high +1.442 / +3.668 V、差2.226 V
   * - burst gate
     - low/high +4.615 / +4.933 V、差0.317 V
   * - mean membrane
     - low/high activity +2.158 / +1.745 V、差-0.413 V
   * - collective correlation
     - activity to gate +0.78471、activity to membrane -0.99726

512版ではspike RMSがhigh-activity時に増えるという64--256用仮定が、出力飽和により逆転します。burstも約5 Vへ
強く飽和します。2回のdiagnostic captureでactivity差2.232/2.245 V、burst差0.312/0.322 V、gate相関
0.786/0.792、membrane差-0.413/-0.417 Vを再現してから、512専用contractを定義しました。synthetic
512-saturated波形は512 profileでPASSし、256 profileではFAILします。

失敗しやすい点
--------------

* 初版は同期BRAM出力からleak/candidate/thresholdを通ってBRAM入力までを1 cycleに置きました。15 DP16KD、
  physical COMB 64%、FF 55%には収まりましたが、placement後のsync予測は46.12 MHzでFAILしました。この
  buildはrouting中に停止し、``top.bit`` を生成せず、SRAMにもloadしていません。
* Amaranthの同期read dataはaddress/enを発行した次cycleに有効です。read発行、row capture、candidate/writeを
  3 stateへ分けます。512-bit registerが増えてもsample budgetには十分な余裕があり、BRAM delayと18-bit
  candidate pathを同じcycleから分離できます。
* row capture追加版はsyncを50.79 MHzまで改善しましたが、表示payloadのbitごとの2段CDCによりFF 57%となり、
  placement後dvi5xも136.15 MHzへ低下しました。このP&Rもbitstream生成前に停止しました。512 spike bitと
  2,048 membrane display bitを個別同期せず、source sampleが安定してからsample-toggleでまとめてcaptureします。
* bundled CDC版はFFを8,957、physical 36%へ減らし、dvi5xを351.99 MHzへ回復しましたが、syncは50.92 MHz
  でした。candidate adderとthreshold/BRAM writeをさらに2 cycleへ分け、candidateを32x18 bit registerします。
* batch処理中に ``spike_vector`` を書き換えると逐次更新モデルになり、完全並列版と結果が変わります。最終batchで
  一括commitします。
* 全512個の16-bit状態をDVI clockへ ``FFSynchronizer`` するとBRAM化の利点を失います。表示用は上位4 bitだけに
  narrowし、さらにsample-toggleだけを2段同期します。SNN payloadは48 kHz sample間で安定するため、toggleが
  DVI側へ届いた後にbundled dataを1回captureできます。frame開始後の最初のsampleだけを取り込み、tearingを
  防ぎます。
* ``ram_style=block`` だけを信用せず、Yosys ``DP16KD`` 数とnextpnr physical utilizationを検査します。
* PDM project rootは ``tiliqua-dslx/gateware`` です。repository rootから実行して新しい ``pyproject.toml`` を作らないで
  ください。

証明範囲
--------

この段階は512 logical state、32 arithmetic lane、16 synchronous memory batch、完全並列モデルとのbit同値、
64x8 DVIと4ch audioのsimulation、R5 resource/timing closure、bitstream identity、揮発性SRAM load、常設ES-9
4ch returnを証明します。SPI flashとcalibration EEPROMは変更していません。
学習則、可変重み行列、512 live CV control、外部PSRAM、物理DVI captureは未検証です。
