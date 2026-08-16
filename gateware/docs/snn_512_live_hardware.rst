Tutorial 14: 512 memory SNNを4本のlive CVで操作する
======================================================

Tutorial 13では512 logical neuronのself-testを15個の ``DP16KD`` と32 arithmetic laneで実装しました。
このTutorialでは内部sourceを外し、常設ES-9の4出力をnetwork drive、leak、recurrence、thresholdへ接続した
live bitstreamを合成・実測します。

live bitstream contract
-----------------------

``gateware`` directoryで次を実行します。

.. code-block:: bash

    pdm snn_lab memory

``memory`` はunit/equivalence、512 self-test simulation、self-test buildに加え、512 live buildも実行します。
既存buildのresource/timing/SHAだけを再評価する場合は次です。

.. code-block:: bash

    pdm snn_lab memory-report

live profileはCV mode選択がconstant-foldされないためself-testより1,036 LUT4多くなりますが、以前45分以上完了
しなかった256 register-state live P&Rと異なり、block-memory版は約2分で完了しました。

.. list-table::
   :header-rows: 1

   * - 項目
     - live結果
   * - LUT4 / FF / DSP / DP16KD
     - 13,038 / 9,652 / 1 / 15
   * - physical COMB / FF / DP16KD
     - 69% / 39% / 26%
   * - sync / audio / dvi / dvi5x
     - 64.60 / 60.93 / 56.44 / 407.50 MHz
   * - bitstream SHA-256
     - ``f2a5b4763931642255296013e835b008858e1f8a2257d6315a290fa3247a040a``

bipolar drive応答
-----------------

private management repositoryから次を実行します。

.. code-block:: bash

    bin/tiliqua snn-live-test --neurons 512

ES-9 OUT1からIN0へ ``+/-0.5, +/-1, +/-2 V`` を順に入れ、ほかの入力を0 Vにします。fresh runは6区間を
すべて検出しました。

.. list-table::
   :header-rows: 1

   * - input
     - spike RMS
     - activity
     - burst
     - membrane
   * - +0.5 V
     - 2.818 V
     - 0.679 V
     - 4.401 V
     - 2.726 V
   * - +1.0 V
     - 2.997 V
     - 1.507 V
     - 4.179 V
     - 2.078 V
   * - +2.0 V
     - 2.176 V
     - 2.756 V
     - 4.967 V
     - 1.803 V
   * - -0.5 V
     - 2.913 V
     - 0.952 V
     - 4.469 V
     - 2.470 V
   * - -1.0 V
     - 3.058 V
     - 1.467 V
     - 3.822 V
     - 2.044 V
   * - -2.0 V
     - 2.171 V
     - 2.929 V
     - 4.967 V
     - 1.784 V

activityは両極性で各段0.4 V以上増え、membraneは各段0.15 V以上低下します。512ではspikeとburstが飽和して
単調増加しないため、各active区間でspike RMS 2.0 V以上、burst 3.5 V以上を要求します。 ``+/-2 V`` の差は
activity 0.172 V、burst 0.0003 V、membrane 0.019 V、spike RMS 0.005 Vでした。

4入力parameter scan
--------------------

.. code-block:: bash

    bin/tiliqua snn-control-test --neurons 512

IN0を1 Vに保ち、IN1--3の各parameterを ``-1, 0, +1 V`` へ変えます。fresh runは9区間を検出し、3 responseと
neutral repeatabilityをPASSしました。

.. list-table::
   :header-rows: 1

   * - parameter
     - -1 V activity / membrane
     - 0 V activity / membrane
     - +1 V activity / membrane
   * - leak
     - 2.429 / 1.680 V
     - 1.509 / 2.077 V
     - -0.146 / 2.024 V
   * - recurrence
     - 1.280 / 2.183 V
     - 1.473 / 2.088 V
     - 1.666 / 2.021 V
   * - threshold
     - 1.657 / 1.762 V
     - 1.500 / 2.079 V
     - 1.137 / 2.503 V

leak +1 Vはnetworkをほぼ停止し、recurrence -1から+1 Vではactivityが0.386 V増えてmembraneが0.162 V
下がり、threshold -1から+1 Vではactivityが0.520 V下がってmembraneが0.741 V上がりました。3回のneutral
measurement rangeはactivity 0.036 V、burst 0.104 V、membrane 0.011 V、spike RMS 0.170 Vです。

segment detectorの失敗と修正
-----------------------------

初回control解析は7/9 runsとなりましたが、rawを0.25秒ごとに集計すると9状態はすべて存在しました。512の
zero-drive modeはspike RMSが約2.35 Vあり、自然変動をmarkerへ含めると0.5秒zero gapがactiveになって隣接runを
結合します。512 profileはactivity、burst、membraneだけでsegmentを検出し、spike RMSは応答判定にだけ使います。
64/128 detectorは変更しません。synthetic 512 saturated scanは512でPASSし、128ではFAILします。

証明範囲
--------

このPASSは512 logical / 32 physical laneのlive R5 timing/resource closure、4 calibrated CV input、bipolar drive
response、leak/recurrence/threshold response、4 DAC returnを証明します。load先は揮発性SRAMだけで、SPI flashと
calibration EEPROMは変更していません。物理DVI capture、学習則、可変重み、外部PSRAMは未検証です。
