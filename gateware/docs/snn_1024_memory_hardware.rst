Tutorial 15: 1024 logical neuronを32 laneとdual-clock表示RAMで動かす
=====================================================================

Tutorial 13--14の512 logical neuron版を、演算器32 laneのまま32 batchへ拡張します。16-bit膜電位本体は
512-bit x 32 rowの ``DP16KD`` memoryへ置き、48 kHzで ``1024 * 48000 = 49.152 M`` neuron-updates/sを
実行します。1 sampleの更新は約132 sync cycleで、60 MHzにおける1,250 cycle budgetの約11%です。

等価性と表示精度を分離する
----------------------------

``MemoryBatchedLIFBank`` の神経状態、1024 spike bit、spike count、4 DAC出力は完全並列1024モデルと8 sample
比較してbit一致します。音声用のmean membraneは各32-lane row更新時に上位4 bitを厳密加算するため、表示を
圧縮しても値は変わりません。表示だけは各neuronをspike 1 bitとmembrane上位2 bitへ量子化し、64x16 gridで
描画します。

1024表示状態をsync FFからDVI FFへ直接渡すと、容量より配線本数が先に限界になります。最終版は32 neuronを
96-bit rowへ詰め、depth 32のdual-clock ``DP16KD`` へsync側から書き、DVI側から現在セルのrowを読みます。
セル境界の2 pixelが同期readの1 cycle latencyを隠します。表示は近接するaudio sampleを含み得ますが、神経計算と
4 audio outputはsample単位でatomicです。

一括回帰
--------

``gateware`` directoryで実行します。

.. code-block:: bash

    pdm snn_lab kiloneuron
    pdm snn_lab kiloneuron-report

前者は9 unit test、1024完全並列等価、4-frame AV simulation、self/live R5 buildを実行します。後者は既存buildの
resource、physical utilization、4 clockを再評価します。AV checksumは ``6f6f5e25f3a042d5``、active pixelは
2,534,824です。audio 0--3 rangeはそれぞれ -29,494..+29,490、0..29,490、0..17,998、0..11,842でした。

R5 QoR
-------

.. list-table::
   :header-rows: 1

   * - profile
     - LUT4 / FF / DP16KD / DSP
     - physical COMB / FF / DP16KD
     - sync / audio / dvi / dvi5x MHz
     - SHA-256
   * - self-test
     - 4,770 / 5,228 / 18 / 1
     - 31% / 21% / 32%
     - 61.61 / 71.47 / 69.05 / 446.83
     - ``7cb994aa79e09a21b6453cf149e81f9234777a270eef34ec9ac424b3d3d4f602``
   * - live
     - 5,781 / 5,346 / 18 / 1
     - 36% / 21% / 32%
     - 60.55 / 73.02 / 65.42 / 477.55
     - ``08e071142abb7cdf550f78cfba91a90b292f77bcf943a07b37aa84a5fab812ae``

実機自己診断とlive control
--------------------------

private management repositoryの固定ES-9 patchで次を実行します。どちらも揮発性SRAMだけを更新します。

.. code-block:: bash

    bin/tiliqua snn-test --neurons 1024
    bin/tiliqua snn-live-test --neurons 1024
    bin/tiliqua snn-control-test --neurons 1024

self-testはspike p01/p99 -6.927/+7.046 V、activity low/high 2.241/7.041 V、burst 3.724/4.898 V、
membrane 2.139/1.673 V、activity-to-gate correlation 0.95140、activity-to-membrane correlation -0.99740で
PASSしました。live amplitudeは6/6、control scanは9/9 segmentを検出し、leak、recurrence、thresholdとneutral
repeatabilityをすべてPASSしました。

失敗から得た境界
----------------

* 最初の4-bit表示shadow版はLUT4 20,097、FF 16,150となり、合法配置を得られませんでした。神経state RAMは
  15 DP16KDへ正しく入っていても、観測用FFとdynamic selectorだけで設計は失敗します。
* 2-bit shadowへ減らした版はLUT4 7,467、FF 10,392、physical COMB/FF 42%に収まりましたが、3,072本の
  bundled CDC配線が混雑し、routerは213,000 iteration後も約700 arcを残しました。13分以上進展しない所有buildを
  bitstream生成前に中断しました。
* dual-clock表示RAM版はLUT4 4,770、FF 5,228、physical 31%/21%となり、約1分でrouteしました。大規模FPGAでは
  register数だけでなく、clock domainを横断する配線本数もcontract対象です。
* 1024 controlの初回detectorは、最大の状態clusterをbaselineとみなしたため、自然変動するzero-driveではなく
  安定したstrong-leak無発火状態を選び2/9になりました。刺激で保証した先頭0.5秒zero区間をbaselineにすると、同じ
  rawから9/9を検出しました。
* placement時sync予測はself/liveで52.71/51.82 MHzでしたが、最終routing後は61.61/60.55 MHzです。合法配置と
  router進展がある限り、最終timing reportをcontract判定に使います。

証明範囲
--------

1024 logical state、32 arithmetic lane、32 memory batch、完全並列モデルとの計算等価、2-bit 64x16表示、R5
resource/timing closure、自己診断、bipolar drive、3 CV controlを証明しました。SPI flashとcalibration EEPROMは
変更していません。表示のframe-atomic snapshot、学習則、signed E/I weight、外部PSRAMは未検証です。
