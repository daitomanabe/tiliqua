Tutorial 7: 物理CVでSNNの振幅応答を測る
==========================================

内部self-testで64ニューロン、4 DAC、DVI、timingを確認した後、Tiliqua IN 0をlive network driveへ
切り替えます。常設配線のES-9 physical OUT 1から既知の正負CVを送り、入力絶対値、ADC、
recurrent network、4 DAC、ES-9 returnを端から端まで検査します。

live profileを合成する
----------------------------------------

.. code-block:: bash

    pdm snn_av build \
      --hw r5 \
      --modeline 720x720p60r2 \
      --name SNN-AV-LIVE

生成先は ``build/snn-av-live-r5/top.bit`` です。通常はself-testとlive profileを両方検査する
次の完全gateを使います。

.. code-block:: bash

    pdm snn_lab --with-build check

2026-08-17のlive profileはLUT4 6,339、FF 3,034、DSP 1でした。Fmaxはsync 60.86 MHz、
audio 65.17 MHz、dvi 77.78 MHz、dvi5x 467.95 MHzで、全clockがcontractを通過しました。
bitstream SHA-256は
``663ff80cc9f19171d14758bb4c500aa0b57895aeb4f7138ae7f04408184f17b3`` です。

bounded bipolar stimulus
------------------------

private management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua snn-live-test

1つの12.2秒、16ch streamだけを開きます。

#. 1秒all zero
#. +0.5、+1、+2、-0.5、-1、-2 Vを各1.2秒
#. 各driveの間に0.5秒all zero
#. 最後に1秒all zero

使用するのはES-9 physical OUT 1→Tiliqua IN 0だけで、他のES-9出力はzeroです。振幅は
``+-2 V`` 以内に制限し、FPGA bitstreamは揮発性SRAMだけへloadします。

returnから区間を検出する
-------------------------

host output patternのsample番号へ固定offsetを足しません。AudioToolboxとAVFoundationの同時利用では
timestamp間隔が欠落する場合があるためです。

captureを10 ms blockへ分け、OUT 2 burst gateの20 percentileをzero-drive baselineとします。
baselineより0.25 V高い状態が0.5秒以上続くrunを抽出し、時系列の6 runを入力系列へ対応させます。
各runの端100 msを捨て、次を測ります。

``spike RMS``
    OUT 0のblock内標準偏差。入力絶対値とともに単調増加すること。

``activity``
    OUT 1平均。正負それぞれ0.5→1→2 Vで単調増加すること。

``burst``
    OUT 2平均。activityと同様に単調増加すること。

``membrane``
    OUT 3平均。発火resetが増えるため単調減少すること。

実測結果
--------

2026-08-17のfresh captureは全contractを通りました。

.. list-table::
   :header-rows: 1

   * - 入力
     - spike RMS
     - activity
     - burst
     - membrane
   * - +0.5 V
     - 0.757 V
     - -0.071 V
     - 0.603 V
     - 3.763 V
   * - +1.0 V
     - 1.148 V
     - +0.006 V
     - 1.671 V
     - 3.302 V
   * - +2.0 V
     - 1.511 V
     - +0.145 V
     - 3.453 V
     - 2.740 V
   * - -0.5 V
     - 0.876 V
     - -0.049 V
     - 0.901 V
     - 3.643 V
   * - -1.0 V
     - 1.253 V
     - +0.035 V
     - 2.066 V
     - 3.057 V
   * - -2.0 V
     - 1.554 V
     - +0.163 V
     - 3.635 V
     - 2.708 V

zero-drive baselineはactivity -0.112 V、burst +0.193 V、membrane +3.637 V、spike RMS
0.525 Vです。6 runを全て検出し、正負それぞれの単調性がPASSしました。

``abs(input)`` を検査する
----------------------------------------

network driveはRTLで入力の絶対値を取ります。ただし物理 ``+x V`` と ``-x V`` を全振幅で完全一致
させるcontractにはしません。ADCの物理0 V offsetが絶対値演算より前に加わるため、0.5 V付近では
正負差が相対的に大きくなります。

offsetの影響が小さい ``+-2 V`` で対称性を確認します。実測差はactivity 0.019 V、burst
0.181 V、membrane 0.032 V、spike RMS 0.043 Vで、全てlimit内でした。低振幅差をRTLの
非対称と誤診せず、必要なら物理ADC zeroを別に測定してから補正します。

このテストが証明する範囲
--------------------------

PASSは次を証明します。

* ES-9 OUT 1からTiliqua IN 0までのDC-coupled input path
* 正負両極性をexcitation magnitudeとして扱う絶対値入力
* 入力強度に対する2つの単調なpopulation応答曲線
* 高振幅での正負対称性
* 各drive後のzero-drive stateへの復帰
* live profileのR5 timing/resource closure

連続周波数依存、温度drift、入力3系統の使用、学習、PSRAM結合行列、物理DVI captureは未検査です。
次はIN 1..3をleak、recurrent gain、threshold modulationへ割り当て、パラメータ空間を安全な範囲で
自動scanします。
