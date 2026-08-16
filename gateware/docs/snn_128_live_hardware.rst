Tutorial 10: 128ニューロンSNNを4本のCVで演奏する
====================================================

128-neuron self-testの次に、4つのcalibrated ADCをlive networkへ戻します。IN 0はbipolar drive、
IN 1はleak、IN 2はrecurrence、IN 3はthresholdです。128個を48 kHzごとに全並列更新する
6.144 M neuron-updates/sを保ったまま、正負振幅応答と9区間control scanを実機で検査します。

live profile固有のtiming failure
----------------------------------

self-testではcontrolが全て定数0なので、synthesisが多くのMuxを消します。同じ128-neuron coreを
live化した初版はlogic LUT4 14,892、FF 5,359、DSP 1でdeviceに収まりましたが、sync Fmaxは
56.35 MHzで60 MHz contractに失敗しました。``top.tim`` の最長経路は次です。

.. code-block:: text

    spike_count
      -> recurrent mode scale
      -> 128 neuron candidate add
      -> threshold compare
      -> membrane register

前sampleのpopulation countと選択済みrecurrence scaleをinput受付stageで1つの共有registerへ移すと、
60.50 MHzまで改善しました。ただし0.50 MHzの余裕は小変更で失われやすいため、そこで止めません。

controlから導出する値を先にregisterする
-------------------------------------------

最終版では、input transactionを受けるstageで次を固定します。

* ``abs(IN 0) >> 5`` のdrive
* previous spike countとIN 2 modeから作る共有recurrent drive
* 各16-bit膜電位とIN 1 modeから作る128個の12-bit leak量
* 16種類だけ存在するbase thresholdとIN 3 modeから作る16個のdynamic threshold

次cycleのneuron updateには、mode decoderや可変shiftを残しません。thresholdは128個作らず、index
modulo 16が同じ8ニューロンで共有します。これによりLUT4は12,956へ減り、sync Fmaxは76.48 MHzへ
上がりました。FFは6,906で128 contract上限7,000まで94個です。次の機能をregister追加だけで
実装し続けないことも、この結果から分かります。

完全gate
--------

``gateware`` directoryで次を実行します。

.. code-block:: bash

    pdm snn_lab scale

このcommandは4 unit test、128 self-test simulation、self-test R5 build、live R5 buildを順に実行し、
両bitstreamへ同じclock/resource contractを適用します。最終結果は次です。

.. list-table::
   :header-rows: 1

   * - profile
     - LUT4
     - FF
     - DSP
     - sync Fmax
     - SHA-256
   * - 128 self-test
     - 9,264
     - 6,662
     - 1
     - 77.99 MHz
     - ``1a3f4995dcb1b98b53c5daad19da1764a8b339ba45dbeac03e0fb26c1aad8114``
   * - 128 live
     - 12,956
     - 6,906
     - 1
     - 76.48 MHz
     - ``78da738c63b0e42cbb758fb49ab785df4df4e75adcd360e58ec8f32a8d457eef``

live profileのaudio/dvi/dvi5x Fmaxは61.01/72.66/422.83 MHzで全てPASSです。

正負振幅response
----------------

private management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua snn-live-test --neurons 128

12.2秒の1 streamでIN 0へ ``+0.5,+1,+2,-0.5,-1,-2 V`` を送り、間をzeroへ戻します。
6区間は全て検出され、正負それぞれでactivity、burst、spike RMSが単調増加し、膜電位が単調減少
しました。

.. list-table::
   :header-rows: 1

   * - input
     - spike RMS
     - activity
     - burst
     - membrane
   * - +0.5 V
     - 1.232 V
     - +0.012 V
     - 1.697 V
     - 2.774 V
   * - +1.0 V
     - 1.742 V
     - +0.174 V
     - 3.503 V
     - 2.322 V
   * - +2.0 V
     - 2.171 V
     - +0.453 V
     - 4.776 V
     - 1.861 V
   * - -0.5 V
     - 1.388 V
     - +0.053 V
     - 2.238 V
     - 2.769 V
   * - -1.0 V
     - 1.960 V
     - +0.230 V
     - 3.880 V
     - 2.133 V
   * - -2.0 V
     - 2.258 V
     - +0.490 V
     - 4.812 V
     - 1.837 V

``+-2 V`` の差はactivity 0.038 V、burst 0.036 V、membrane 0.024 V、spike RMS 0.087 Vで、
全てsymmetry contract内です。

populationごとにzero-drive envelopeを分ける
--------------------------------------------

最初の128 captureは単調性と対称性を通りましたが、64版のzero-drive上限だけでFAILしました。
128版のbaselineは2回連続でactivity -0.077 V、burst 0.526..0.534 V、membrane 2.665 V、RMS
0.805..0.806 Vでした。64版のburst ``<=0.45 V``、RMS ``<=0.8 V`` をそのまま使うと、正常な
128個の自励発火を故障と誤認します。

128版は独立した広いenvelopeとしてactivity ``<=0 V``、burst ``<=0.8 V``、membrane
``2.3..3.1 V``、RMS ``<=1.1 V`` を使います。synthetic negative testは同じcapture envelopeを
64版として解析するとFAILすることも確認します。population数を波形振幅から推測せず、``--neurons``、
bitstream SHA、結果JSONのnetwork metadataを一致させます。

4-input control scan
--------------------

.. code-block:: bash

    bin/tiliqua snn-control-test --neurons 128

9 active区間を全て検出し、3回のneutral stateの最大差はactivity 0.0003 V、burst 0.0075 V、
membrane 0.0043 V、RMS 0.0448 Vでした。

.. list-table::
   :header-rows: 1

   * - parameter
     - CV
     - spike RMS
     - activity
     - burst
     - membrane
   * - leak
     - -1 V
     - 2.305 V
     - +0.386 V
     - 4.582 V
     - 1.722 V
   * - leak
     - 0 V
     - 1.771 V
     - +0.174 V
     - 3.523 V
     - 2.322 V
   * - leak
     - +1 V
     - 0.000 V
     - -0.145 V
     - 0.007 V
     - 2.025 V
   * - recurrence
     - -1 V
     - 1.651 V
     - +0.157 V
     - 3.398 V
     - 2.400 V
   * - recurrence
     - 0 V
     - 1.768 V
     - +0.174 V
     - 3.516 V
     - 2.320 V
   * - recurrence
     - +1 V
     - 2.024 V
     - +0.211 V
     - 3.528 V
     - 2.184 V
   * - threshold
     - -1 V
     - 1.992 V
     - +0.267 V
     - 4.033 V
     - 1.867 V
   * - threshold
     - 0 V
     - 1.727 V
     - +0.174 V
     - 3.523 V
     - 2.324 V
   * - threshold
     - +1 V
     - 1.545 V
     - +0.088 V
     - 2.645 V
     - 2.863 V

飽和monitorを必須判定にしない
------------------------------

128 recurrence scanを2回行うと、weak/strongのactivity差は0.053..0.054 V、膜電位差は
0.214..0.216 Vで安定しました。一方burstは約3.5 Vへ飽和し、差が0.083..0.129 Vに変動して
64版の0.12 V limitを一度下回りました。

128 recurrence contractは飽和しないactivityを0.03 V以上増加、膜電位を0.15 V以上減少させることを
必須にし、burstとRMSは記録します。64版はburstに十分なheadroomがあるため従来の3指標contractを
維持します。規模が変わったときに閾値だけを広げず、各monitorがまだ情報を持つかを先に確認します。

64版の回帰
----------

同じ最終RTLで ``pdm snn_lab --with-build check``、64 self-test、6振幅、9 controlを再実行し、全て
PASSしました。64 liveはsync 80.65 MHz、LUT4 7,144、FF 3,995、DSP 1です。事前計算registerは
64/128の状態更新則を変えず、timingだけを改善しています。

次の境界
--------

128 liveでFF contractは98.7%です。256個を同じ方式でlive化する前に、per-neuron leak registerを
RAM/時分割へ移すか、controlをpopulation単位へまとめる必要があります。次は256 self-testの
simulationとsynthesisだけを安全に試し、R5での全並列上限を測ります。timing/resource contractを
通らないbitstreamはSRAMへloadしません。
