Tutorial 4: DSLX synthをSRAMとES-9で実測する
=================================================

この段階ではPC上のsimulation/合成を、Tiliqua R5のcodec、DAC、パッチケーブル、ES-9 ADCまで
延長します。内部 ``SynthControlTestSource`` を使うため、pitch/gate入力の品質に左右されず、
発振器、ADSR、VCA、gate、整流出力を最初に切り分けられます。

検査する経路
------------

.. code-block:: text

    deterministic C3/C4 + gate
               |
               v
    QuantizedPitchCV -> BasicVoice -> DSLXADSR -> shared VCA
                                             |
           +---------------+-----------------+---------------+
           |               |                 |               |
        OUT 0 voice   OUT 1 envelope    OUT 2 5V gate   OUT 3 abs(voice)
           |               |                 |               |
        ES-9 IN 1       ES-9 IN 2         ES-9 IN 3       ES-9 IN 4

FPGAへの書き込みは揮発性SRAMだけです。SPI flashとcalibration EEPROMは変更しません。
常設のES-9 OUT 1..4は全zero patternを再生するので、self-test中に外部CVを加えません。

事前の完全回帰
--------------

まず ``gateware`` で全software/FPGA gateを通します。

.. code-block:: bash

    pdm dslx_synth_lab --with-build check

2026-08-17のR5結果は、21 testとVerilator integrationがPASSし、self-test profileが
LUT4 1223、FF 1067、DSP 2、sync 79.71 MHz、live-input profileがLUT4 1317、
FF 1137、DSP 2、sync 71.83 MHzでした。どちらも60 MHz contractを満たします。

実機コマンド
------------

隣接するprivate ``teliqua-management`` repositoryで、privileged sessionがactiveなことを
確認して実行します。

.. code-block:: bash

    bin/tiliqua session status
    bin/tiliqua synth-test

このコマンドは次を自動化します。

#. ES-9が16入力/16出力/48 kHzであることを確認
#. ``build/dslx-synth-lab-r5/top.bit`` の存在とTiliqua USB serialを確認
#. bitstreamを ``write-sram`` で一時ロード
#. ES-9 physical OUT 1..4をzeroに保ったまま、IN 1..4を同時capture
#. gate high/low、C3/C4、ADSR ramp、``abs(voice)`` の相関を判定
#. JSONをgit管理外の ``artifacts/dslx-synth-selftest/`` へ保存

実測結果
--------

2026-08-17のfresh captureは次の通りです。

.. list-table::
   :header-rows: 1

   * - 項目
     - 実測
     - 判定
   * - OUT 2 gate low/high
     - +0.007 V / +4.969 V
     - PASS
   * - gate duty / detected high run
     - 0.489 / 199
     - PASS
   * - OUT 0 C3
     - 130.790 Hz
     - PASS
   * - OUT 0 C4
     - 260.870 Hz
     - PASS
   * - octave ratio
     - 1.99457
     - PASS
   * - OUT 1 baseline / sustain / peak
     - -0.146 V / 5.882 V / 7.927 V
     - PASS
   * - OUT 3 vs ``abs(OUT 0)``
     - correlation 0.99086 / gain 0.9960
     - PASS

周波数は短い約21 ms burst内の正方向zero crossing間隔を多数集めてmedianを取ります。
C4の1周期はcapture上で183または184 sampleになるため、単発値は260.87または262.30 Hzです。
長いFFTのbinを厳密値と誤認せず、複数burstとoctave ratioで判定します。

analog出力同士を同じsample番号で比較しない
--------------------------------------------

OUT 3はRTL上ではOUT 0の絶対値ですが、実測captureで同じsample indexを直接比較すると相関は
0.84でした。``+-32 sample`` のrelative lagを探索すると、-10 sampleで相関0.99086、gain
0.9960になります。codec/calibration/analog return/host captureを含む物理経路では、論理的に同時の
channelでもsample-perfectに揃うと仮定しません。

このlagは波形を都合よく変形する補正ではありません。整数sample shiftだけを許し、相関、gain、
normalized errorを同時に制限します。大きな遅延や別波形はPASSしません。

テストが証明する範囲
--------------------

PASSは次をまとめて証明します。

* 32bit NCOがC3/C4の2周波数を生成する
* gateでADSRがattack/decay/sustain/releaseし、VCA後のvoiceがburstになる
* 5 V diagnostic gateが物理OUT 2まで届く
* magnitudeがvoiceの全波整流として物理OUT 3まで届く
* codec、4 DAC、4 cable、ES-9 ADCを通った電圧がcontract内にある

一方、pitch/gate ADC input、DVI/TLQ-SCREEN、MIDI、PSRAM、SPI flash永続性はこの検査では証明
しません。次は同じ配線のES-9 OUT 1/2から0/+1 V pitch CVと0/5 V gateを送り、
``--self-test`` なしのlive-input bitstreamを測定します。続きは
:doc:`dslx_synth_live_hardware` です。

失敗時の読み方
--------------

``gate FAIL``
    OUT 2/ES-9 IN 3の配線、5 V level、bitstream identityを確認します。

``C3 PASS / C4 FAIL``
    ``SynthControlTestSource`` のpitch切替、``QuantizedPitchCV`` ROM、captureのburst分類を確認します。

``envelope FAIL``
    baselineだけならgate/ADSR、gateは正常でrampが無いならADSR state/stream backpressureを確認します。

``magnitude FAIL``
    sample indexの直接比較へ戻さず、best lag、correlation、gain、normalized errorを確認します。

実行失敗または判定FAILではraw 16ch captureを残します。出力streamが途中停止した場合も、
管理scriptはtimeout付きのzero patternを1回送り、開始したFFmpeg processだけを終了します。
