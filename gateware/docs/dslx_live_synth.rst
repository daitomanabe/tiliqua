DSLX live synthを物理CV・gateへ接続する
=======================================

``dslx_synth`` は、前章の ``CVPlayableVoice`` をTiliquaのcalibrated ADC/DACとDVIへ接続する
専用topです。``dslx_av`` は入力音声を解析するreactorとして残し、演奏用synthとは分離しています。

信号経路とjack割り当て
----------------------

.. code-block:: text

    IN 0 calibrated pitch CV --+--> QuantizedPitchCV --> BasicVoice --+
                               |                                    |
    IN 1 calibrated gate ------+--> GateDetector --> DSLXADSR ------+--> VCA
                                                                         |
                                     +-----------------------------------+
                                     |
                                     +--> OUT 0 voice
                                     +--> OUT 1 ADSR envelope
                                     +--> OUT 2 aligned 5 V gate
                                     +--> OUT 3 full-wave voice magnitude
                                     +--> DSLX visualizer --> TLQ-SCREEN

gate detectorは4 Vを越えるとON、2 Vを下回るとOFFになります。hysteresis stateはstream転送時だけ
更新されるため、物理edgeからADSRへは意図的に1 audio sampleの遅延があります。

自己診断とlive版を分ける
------------------------

自己診断版はADCを使わず、0/+1 V相当のpitch CVと0/5 V gateを内部生成します。

.. code-block:: bash

    pdm dslx_synth sim \
      --hw r5 --modeline 720x720p60r2 --self-test

live版は同じcoreへ ``pmod0.o_cal`` を接続します。

.. code-block:: bash

    pdm dslx_synth build \
      --hw r5 --modeline 720x720p60r2

1コマンド回帰は、unit/DSLX/Verilatorに加えて自己診断版とlive版を **両方** 配置配線します。

.. code-block:: bash

    YOSYS=/path/to/oss-cad-suite/bin/yosys \
    NEXTPNR_ECP5=/path/to/oss-cad-suite/bin/nextpnr-ecp5 \
    ECPPACK=/path/to/oss-cad-suite/bin/ecppack \
      pdm dslx_synth_lab --with-build check

両方を合成する理由
------------------

最初の自己診断版はsync 78.68 MHzでPASSしましたが、初回live版は52.48 MHzで60 MHz制約を
満たしませんでした。ADC経路では、CV入力から定数shift-add、signed rounding、note clamp、ROM address
までが1本の組合せpathになっていたためです。

``QuantizedPitchCV`` を次の段に分割しました。

.. code-block:: text

    accept CV --> CV register
              --> shift-add / signed rounding register
              --> note clamp / ROM address register
              --> synchronous ROM read
              --> output

48 kHz audio sample間には60 MHz clockが1250 cycleあるため、数cycleのcontrol latencyは問題になりません。
修正後のlive版は73.66 MHzでPASSしました。自己診断だけを合成していた場合、この退行を発見できません。

pitch CVのコンパイル時校正
---------------------------

このno-SoC topでは、eurorack-pmodの平均的なdefault ``Ax+B`` calibrationを使います。個体EEPROMから
firmwareが読み出すcalibrationはまだありません。その上にsynth専用の2値を指定できます。

``pitch_zero_counts``
    物理0 Vに対応するcalibrated ADC countです。

``counts_per_octave``
    1 V増加したときのADC count差です。既定値は4000です。

raw countをILAや計測用firmwareで取得できた場合は、複数点からleast-squares fittingできます。

.. code-block:: bash

    pdm run python scripts/pitch_cv_calibrate.py \
      --point=-1:-3990 --point=0:100 \
      --point=1:4190 --point=2:8280

出力例は次です。

.. code-block:: text

    --pitch-zero-counts 100 --counts-per-octave 4090

この値をlive buildへ渡します。

.. code-block:: bash

    pdm dslx_synth build --hw r5 --modeline 720x720p60r2 \
      --pitch-zero-counts 100 --counts-per-octave 4090

現在の変換は半音量子化です。連続pitch bendやcent単位のtemperature compensationは次段階で追加します。

検証結果
--------

R5、720x720p60r2での結果です。

.. list-table::
   :header-rows: 1

   * - profile
     - LUT4
     - FF
     - DSP
     - sync Fmax
   * - self-test
     - 1223
     - 1067
     - 2
     - 79.71 MHz
   * - live-input
     - 1317
     - 1137
     - 2
     - 71.83 MHz
   * - contract
     - 1450以下
     - 1250以下
     - 2以下
     - 60 MHz以上

回帰は20 testと4つのDSLX vector suiteを通過し、Verilatorで3662 audio sampleと4 DVI frameを
確認しました。voiceは-28082から+25288 count、envelopeは0から29490、gateは0から17998です。

実機が手元にあるとき
----------------------

#. まずself-test版を揮発性SRAMへロードし、OUT 0から3と画面を確認する
#. live版へ切り替え、IN 0へ0 V、IN 1へ0/5 V gateを接続する
#. IN 0を+1 Vへ変え、発音pitchが正確に1 octave上がるか周波数counterで測る
#. 必要なら個体校正値をfitして再buildする
#. SPI flashへは、測定と長時間試験が完了するまで書き込まない

2026-08-17にself-test bitstreamをSRAMへロードし、ES-9で4出力を自動測定しました。
C3/C4、ADSR、5 V gate、voice magnitudeの全contractがPASSしています。手順と実測値は
:doc:`dslx_synth_hardware` を参照してください。続いてlive input版もES-9の ``-1/0/+1 V``
pitchと ``0/5 V`` gateで自動測定し、個体用 ``pitch_zero_counts=-307`` でC2/C3/C4が
全てPASSしました。校正の導出は :doc:`dslx_synth_live_hardware` を参照してください。
