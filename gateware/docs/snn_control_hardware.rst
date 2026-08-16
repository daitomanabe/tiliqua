Tutorial 8: 4本のCVでSNNの集団状態を変調する
===============================================

常設しているES-9→Tiliquaの4本を全て使い、network driveに加えてleak、recurrent gain、
thresholdを物理CVで切り替えます。各controlをいきなり連続可変にせず、最初は3 modeに量子化し、
simulationと実機で方向・再現性・安全な無発火状態を検査します。

4入力mapping
------------

.. list-table::
   :header-rows: 1

   * - Tiliqua入力
     - CV mode
     - network内の作用
   * - IN 0
     - bipolar continuous
     - ``abs(input) >> 5`` を全ニューロンへ加算
   * - IN 1
     - negative / neutral / positive
     - leakを ``v>>6 / v>>5 / v>>4`` に切替
   * - IN 2
     - negative / neutral / positive
     - previous spike countを ``<<1 / <<2 / <<3`` にscale
   * - IN 3
     - negative / neutral / positive
     - 全thresholdを ``-800 / 0 / +800`` count移動

controlはcalibrated ASQ countが ``-1000`` 未満、``-1000..+1000``、``+1000`` 超の3領域です。
理想換算は約 ``+-0.25 V`` ですが、物理ADC offsetを含むため境界電圧を校正値なしで厳密値とは
みなしません。実機scanは十分離れた ``-1/0/+1 V`` を使います。

入力を先にregisterする
-----------------------

live profileではcalibrated ADCから64個のcandidate演算までが同じtransactionに属します。3 controlの
decodeを各candidate pathへ直接足すとtiming marginが不安定になるため、入力振幅と3 modeをstage 0で
registerします。続くneuron update、population reduction、DAC mappingと合わせ4段です。

また、16-bit signed最小値 ``-32768`` の単純な符号反転は同じ幅では表現できません。入力絶対値は
この値だけ明示的にunsigned ``32768`` へ写像します。これは通常の ``-1..+1 V`` scanでは出ませんが、
full-scale digital regressionで見落とさない境界です。

monitor adder treeの余裕を増やす
----------------------------------------

旧live profileのsync Fmaxは60.86 MHzで、critical pathは64個の上位8-bit膜電位を集計する経路でした。
OUT 3は観測用なので内部16-bit状態を変えず、各膜電位の上位4-bitを加算し、64-neuron population和を
``<<6`` して出力します。平均を先に整数化しないため、64個の分数的な集合変化は保持されます。

4 control追加後もlive profileはLUT4 7,334、FF 3,128、DSP 1、sync Fmax 64.21 MHzです。
self-test profileは定数controlが最適化され、LUT4 5,359、FF 3,014、sync 68.95 MHzです。

自動実機scan
------------

private management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua snn-control-test

各active区間はIN 0へ+1 Vを入れ、対象controlだけを ``-1/0/+1 V`` にします。leak、recurrence、
thresholdの順で9区間、各1.2秒です。間は4入力全てを0.5秒zeroにし、全17.3秒を1つの16ch
CoreAudio streamとして送ります。SPI flashとcalibration EEPROMは変更しません。

単一markerが消える失敗
----------------------

最初のanalyzerはOUT 2 burst gateだけでactive runを検出しました。しかしstrong leakではnetworkが
完全に無発火になり、OUT 0 RMSもOUT 2もzeroになります。その区間を「刺激が無かった」と誤認し、
9区間中8区間しか見つけられませんでした。

修正版は10 ms blockごとに次の4指標を使います。

* OUT 1 activity平均
* OUT 2 burst平均
* OUT 3 membrane平均
* OUT 0 spike RMS

4次元状態を量子化し、最頻クラスタを繰り返されるzero-drive baselineとします。どれか1指標がbaselineを
離れればactive候補です。これにより「発火が無いが膜電位状態が変わった」strong leakも検出します。
global medianはactive時間が過半数のfixtureではbaselineにならないため使いません。

fresh実測
---------

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
     - 1.451 V
     - +0.113 V
     - 3.072 V
     - 1.728 V
   * - leak
     - 0 V
     - 1.141 V
     - +0.006 V
     - 1.674 V
     - 2.401 V
   * - leak
     - +1 V
     - 0.000 V
     - -0.146 V
     - 0.007 V
     - 2.024 V
   * - recurrence
     - -1 V
     - 1.090 V
     - +0.003 V
     - 1.638 V
     - 2.415 V
   * - recurrence
     - 0 V
     - 1.130 V
     - +0.005 V
     - 1.667 V
     - 2.401 V
   * - recurrence
     - +1 V
     - 1.178 V
     - +0.014 V
     - 1.790 V
     - 2.306 V
   * - threshold
     - -1 V
     - 1.320 V
     - +0.053 V
     - 2.323 V
     - 1.892 V
   * - threshold
     - 0 V
     - 1.121 V
     - +0.006 V
     - 1.668 V
     - 2.401 V
   * - threshold
     - +1 V
     - 0.986 V
     - -0.032 V
     - 1.114 V
     - 2.907 V

3回のneutral stateの最大差はactivity 0.0004 V、burst 0.0065 V、membrane 0.0002 V、
spike RMS 0.0203 Vで全てrepeatability contract内です。

一回のcaptureへ過適合しない
---------------------------

recurrenceのstrong/weak spike RMS差は、複数のfresh captureで0.026..0.088 Vに変動しました。
activity、burst、membraneは全captureで期待方向と最低差を満たしました。RMSは有限長区間の発火位相に
敏感です。

recurrence contractは方向が独立に一致するactivity、burst、membraneの3指標を必須とし、RMSは
記録だけにします。閾値を直近結果に合わせ続けず、何を物理的に証明したいかでstable metricを
選びます。

証明範囲と次の段階
------------------

このPASSで4 ADC、3 mode decoder、64個のleak/threshold path、population recurrence、4 DAC、
neutral再現性を確認しました。continuous control、複数control同時変調、hysteresis、温度drift、
学習則は未検査です。

次はこのcontrol planeを保ったままニューロン数を増やし、R5のLUT/timing上限を測ります。128個を
単純複製する前に、表示mapping、population reduction段、routing fanoutを別contractとして扱います。
