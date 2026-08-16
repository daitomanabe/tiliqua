Tutorial 6: 64ニューロン全並列SNNで音と映像を生成する
===========================================================

この段階では、Tiliqua R5上で64個のleaky-integrate-and-fire（LIF）ニューロンを
48 kHzの各audio sampleごとに全て更新します。時分割で1個ずつ計算するのではなく、64個分の
加算、減算、比較器を同時に動かすため、1秒あたり ``64 * 48000 = 3.072 M`` neuron updateです。

最初の目標は学習器ではありません。決定論的な小規模recurrent networkから、4系統のCV/audioと
8x8 DVI表示を同時に生成し、simulation、FPGA timing、ES-9実測を1つのcontractで結びます。

ネットワーク
------------

各ニューロン ``i`` は16-bit unsigned膜電位 ``v_i`` と1-bit発火状態を持ちます。audio入力の
絶対値、直前のpopulation発火数、左隣の直前発火を共通・局所結合として加えます。

.. code-block:: text

    candidate_i = v_i
                  - (v_i >> 5)             # leak
                  + bias_i
                  + (abs(input_0) >> 5)
                  + (previous_spike_count << 2)
                  + (left_neighbor_spike ? 256 : 0)

    if candidate_i >= threshold_i:
        spike_i = 1
        v_i = reset_i
    else:
        spike_i = 0
        v_i = candidate_i

``bias_i`` と ``threshold_i`` はindexごとにずらし、全ニューロンが完全同期して同じ発火だけを
繰り返さないようにします。乗算器や外部RAMは使いません。64個の状態は1 audio transactionごとに
同時更新され、streamがbackpressureを受けた間は入力ready、出力payload、膜電位、発火状態、
sample indexを全て停止します。

5段pipeline
-----------

60 MHz sync domainで64-way処理を1 cycleに詰め込まず、次の5段へ分けます。

#. 入力振幅と3つのcontrol modeをregister
#. 64個の膜電位候補と発火bitを同時更新してregister
#. 8ニューロンごとの発火数と上位4-bit膜電位和をregister
#. 8個のgroup totalをpopulation countと膜電位monitorへ集約
#. population countと膜電位monitorを4系統のDAC sampleへ写像

48 kHzでは1 sampleが約20.83 usです。5 sync cycleは約83.3 nsなので、このpipeline latencyは
音響上無視でき、timing closureには大きく効きます。OUT 3は観測用なので、内部16-bit膜電位を
変えず、各ニューロンの上位4-bitを64個加算してscaleします。平均後に下位を捨てずpopulation和を
保持するため、monitorの小さな集合変化も残ります。

出力と映像
----------

.. list-table::
   :header-rows: 1

   * - Tiliqua出力
     - 信号
     - 用途
   * - OUT 0
     - population発火数をsampleごとに符号反転
     - bipolar spike audio
   * - OUT 1
     - population発火数
     - unipolar activity CV
   * - OUT 2
     - 2個以上が同時発火したとき5 V
     - burst gate
   * - OUT 3
     - population平均膜電位
     - slow collective-state CV

DVIはframebufferを使わず、720x720 raster内へ64個を8x8で描きます。白はそのsnapshotで発火中、
青から赤への色は膜電位です。audio/sync domainからDVI domainへ同期した値はvsync立ち上がりで
一括snapshotし、1 frame中は固定します。

.. figure:: /_static/snn_av_selftest.png
   :width: 560px

   Verilator最終frame。白い2セルが発火中で、全セルの色は同じvsync snapshotに属します。

simulationとFPGA contract
-------------------------

``gateware`` directoryで次を実行します。

.. code-block:: bash

    pdm snn_lab --with-build check

この1コマンドはdoctor、unit test、4 DVI frameのVerilator integration、音声contract、R5合成、
resource/timing contractを順に実行します。2026-08-17の最終結果は次です。

.. list-table::
   :header-rows: 1

   * - 項目
     - 結果
   * - unit test
     - 4 PASS（決定性、bipolar出力、全状態freeze、3 control response、128-way集計）
   * - DVI
     - 4 frames / 2,534,824 active pixels / RGB各16以上のspan
   * - OUT 0 simulation
     - -29,494 .. +29,490 count
   * - OUT 1 simulation
     - 0 .. 7,372 count
   * - OUT 2 simulation
     - 0 .. 17,998 count
   * - OUT 3 simulation
     - 0 .. 11,288 count
   * - LUT4 / FF / DSP
     - 5,086 / 3,103 / 1
   * - sync Fmax
     - 85.47 MHz（要求60.00 MHz）
   * - audio / dvi / dvi5x Fmax
     - 67.20 / 89.78 / 436.68 MHz

実機自己診断
------------

隣接するprivate management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua session status
    bin/tiliqua snn-test

``snn-test`` は ``build/snn-av-lab-r5/top.bit`` を揮発性SRAMへロードし、ES-9 physical
OUT 1..4をzeroに保ちながら、Tiliqua OUT 0..3をES-9 physical IN 1..4で6秒間同時取得します。
SPI flashとcalibration EEPROMは変更しません。

self-test sourceは入力振幅を2,048 sampleごとに3,000/12,000 countへ切り替えます。ただし
AudioToolbox outputとAVFoundation inputのtimestampをsample-perfectとは仮定しません。capture内を
10 ms blockへ分け、OUT 1 activityのmedianで2状態を検出します。

2026-08-17のfresh実測は次の通りです。

.. list-table::
   :header-rows: 1

   * - 項目
     - 実測
     - 判定
   * - OUT 0 p01 / p99
     - -4.158 V / +4.248 V
     - PASS
   * - OUT 0 RMS
     - 1.647 V
     - PASS
   * - OUT 1 low/high activity
     - -0.005 V / +0.246 V
     - PASS
   * - OUT 2 low/high activity時の平均
     - +1.442 V / +3.836 V
     - PASS
   * - OUT 3 low/high activity時の平均
     - +2.542 V / +1.821 V
     - PASS
   * - activityとburst gateのblock相関
     - +0.99568
     - PASS
   * - activityと膜電位のblock相関
     - -0.99825
     - PASS

OUT 2はRTL上0/5 Vですが、audio sampleごとに高速に切り替わるため、analog pathと48 kHz captureでは
2状態の平均が約1.46/3.86 Vになります。静的gate testの0/5 V基準をそのまま適用せず、activityとの
正相関と4 V以上のsample分布spanで判定します。同様に、発火が多い状態ではresetが増えるため、
OUT 3平均膜電位はactivityと強い負相関になります。この2相関により、単なる固定電圧や無関係な音を
PASSさせません。

失敗から得たtimingとCDCの知見
------------------------------

最初の単段版はLUT4 7,949、sync Fmax 32.87 MHzでした。critical pathは
``candidate -> spike -> population sum -> DAC`` です。発火bitをregisterしても48.67 MHz、
population countをさらにregisterしても53.58 MHzでした。その時点の最長経路は64個の16-bit
膜電位平均からOUT 3でした。

16-bit平均をregisterすると58.56 MHzまで改善し、観測専用平均を上位8-bitへ狭めて61.71 MHzで
最初のcontractを通過しました。続く4-input版では上位4-bitのpopulation和へ変え、68.95 MHzまで
改善しました。128-neuron拡張時に8個ごとの局所集計registerを追加し、64版も共通の5段へ揃えた
現在値は85.47 MHzです。FPGAで「adder treeを書いた」だけではtiming closureを保証しません。
各失敗後に ``top.tim`` の実際のsource/sinkを読み、ネットワーク更新、集計、出力写像をregisterで
分離します。

映像の初期版は、DVI raster走査中にもニューロン値が更新され、1セル内に横縞が出ました。
個々のbitをCDCしただけではframe整合性は得られません。表示用signalはvsyncでsnapshotし、frame中に
固定します。これは可視化用の低速snapshotであり、測定用のatomic data transportではありません。

実機テストの初回には、NumPy比較結果 ``numpy.bool_`` をそのままJSONへ書き、Python 3.14の
``json.dumps`` が失敗しました。判定値を明示的に標準 ``bool`` へ変換し、synthetic fixtureのtestで
JSON serializationまで検査します。測定器は波形判定だけでなく、証跡保存もcontractに含めます。

次の実験
--------

この版が証明するのは、64個の全並列LIF更新、recurrent response、4 DAC、DVI raster生成、R5 timing、
ES-9 returnです。学習、可塑性、外部PSRAM、MIDI、USB audio、物理DVI captureはまだ検査していません。

``--self-test`` なしのtopはTiliqua IN 0をnetwork driveとして使用できます。同じ常設配線で
ES-9から複数振幅を送り、入力強度に対する発火率、膜電位、音色の応答曲線も実測しました。
続きは :doc:`snn_live_hardware` です。その後、結合行列や遅延をPSRAMへ置く版、MIDI/CVで
network parameterを変更する版へ段階的に進めます。
