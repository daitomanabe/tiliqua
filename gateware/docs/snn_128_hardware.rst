Tutorial 9: 128ニューロンSNNを全並列で動かす
================================================

64ニューロン版のcontrol planeと4出力を保ったまま、LIF populationを128個へ倍増します。
各audio sampleで128個を同時更新するため、48 kHzでは
``128 * 48000 = 6.144 M`` neuron update/sです。この段階の目的は、音を複雑にする前に
R5のlogic、routing、集計pipeline、DVI mapping、4 DAC returnがどこまで拡張できるかを
再現可能なcontractで測ることです。

parameter化と16x8表示
----------------------

``ParallelLIFBank``、top、DVI visualizerを ``neuron_count`` でparameter化しました。
64と128だけを明示的に許可し、次のようにprofileを選びます。

.. code-block:: bash

    pdm snn_av build \
      --hw r5 \
      --modeline 720x720p60r2 \
      --self-test \
      --neurons 128 \
      --name SNN-AV-128-LAB

128個のspike bit、膜電位上位4 bit、activity countをDVI domainへ同期し、vsyncで1回だけ
snapshotします。720x720画面では8x8のcellを横方向へ倍増した16x8 gridとして描きます。
frame中に状態を更新しないため、cell内の横縞は発生しません。

.. figure:: /_static/snn_av_128_selftest.png
   :width: 560px

   Verilator最終frame。16x8の128セルを1つのvsync snapshotとして表示します。

128-way集計で発生したtiming failure
-------------------------------------

最初の128版は機能simulationを通り、LUT4 10,052、FF 5,068、DSP 1でdeviceに収まりました。
しかしsync Fmaxは56.30 MHzで、要求60.00 MHzを満たしませんでした。``top.tim`` の最長経路は
register済みの膜電位から128要素のmonitor和を作り、``membrane_mean`` へregisterする経路です。

balanced adder treeを記述しただけでは、広いplacementを跨ぐrouting遅延を解消できません。
そこでpopulation集計を次の5段へ分けました。

#. input magnitudeと3 control modeをregister
#. 128個の膜電位とspike bitを同時更新
#. 8ニューロンごとのspike countと上位4-bit膜電位和をregister
#. 16個のgroup totalをpopulation totalへ集計
#. 4 DAC sampleへ写像

1 sample中の追加1 sync cycleは16.7 nsで、48 kHz audio period約20.83 usに対して0.08%です。
修正版はsync Fmax 81.35 MHzへ上がり、LUT4 9,494、FF 5,245、DSP 1でした。局所集計registerが
routingを短くし、LUTも減りました。64版も同じgroupingを使い、同一architectureで回帰します。

simulationとsynthesis contract
------------------------------

``gateware`` directoryで次を実行します。

.. code-block:: bash

    pdm snn_lab scale

これはSNN unit test、128-neuron Verilator integration、DVI/audio report、R5合成、128専用の
resource/timing contractを順に実行します。2026-08-17の結果は次です。

.. list-table::
   :header-rows: 1

   * - 項目
     - 結果
   * - unit test
     - 4 PASS（128-way grouped reductionの厳密一致を含む）
   * - DVI
     - 4 frames / 2,534,824 active pixels / checksum ``48775543e37f8c43``
   * - OUT 0 simulation
     - -29,494 .. +29,490 count
   * - OUT 1 / OUT 2 / OUT 3 simulation maximum
     - 9,676 / 17,998 / 10,712 count
   * - LUT4 / FF / DSP
     - 9,494 / 5,245 / 1
   * - sync Fmax
     - 81.35 MHz（要求60.00 MHz）
   * - audio / dvi / dvi5x Fmax
     - 68.98 / 66.07 / 441.31 MHz
   * - bitstream SHA-256
     - ``26bbd8ef9c85740deed22101a5d16cff23c64a7155694dbe7028966d71d689cf``

実機SRAM自己診断
----------------

private management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua session status
    bin/tiliqua snn-test --neurons 128

``--neurons 128`` は ``build/snn-av-128-lab-r5/top.bit`` を選び、揮発性SRAMだけへloadします。
ES-9の全出力へzeroを送りながら、Tiliqua OUT 0..3をphysical IN 1..4で同時captureします。
SPI flashとcalibration EEPROMは変更しません。結果JSONには128 neurons、48 kHz、6.144 M
parallel updates/sとbitstream SHAを保存します。

fresh実測は次の通りです。

.. list-table::
   :header-rows: 1

   * - 項目
     - 実測
     - 判定
   * - OUT 0 p01 / p99
     - -5.576 / +5.638 V
     - PASS
   * - OUT 0 RMS
     - 2.231 V
     - PASS
   * - OUT 1 low / high activity
     - +0.157 / +0.666 V
     - PASS
   * - OUT 2 low / high activity時
     - +3.054 / +4.719 V
     - PASS
   * - OUT 3 low / high activity時
     - +2.506 / +1.796 V
     - PASS
   * - activityからburst gateへの相関
     - +0.99587
     - PASS
   * - activityから膜電位への相関
     - -0.99813
     - PASS

manifestとCLIの小さな制約
-------------------------

最初のbuildはmanifestの右側help label ``16x8 neuron self-test`` が21文字で停止しました。
Tiliquaのhelp fieldは20文字以内なので、``16x8 SNN self-test`` へ短縮しました。RTLやtiming以前に
metadata validationで停止することがあるため、profile名とpanel labelもbuild contractの一部です。

topのvideo mode引数は ``--resolution`` ではなく ``--modeline 720x720p60r2`` です。
個別コマンドを手で組むより ``pdm snn_lab scale`` を使い、simulationとbuildの引数を同じrunnerへ
固定します。

証明範囲と次の段階
------------------

このPASSは128-way LIF update、8-neuron grouped reduction、16x8 DVI snapshot、4 DAC、ES-9 return、
R5 timing/resource closureを証明します。128 live input、4 controlの実機scan、256ニューロン、
学習則、PSRAM結合行列、USB/MIDI、物理DVI captureはまだ未検査です。

次は128 live profileで同じ4 CV controlを検査します。その後、完全結合をlogicへ複製せず、疎結合や
PSRAM上の重み、複数populationを使って音と映像の状態空間を増やします。
