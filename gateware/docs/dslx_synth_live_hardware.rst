Tutorial 5: 物理pitch CVとgateでlive synthを校正する
=====================================================

内部self-testがPASSした後、同じ常設配線を逆方向にも使います。ES-9から既知のpitch CVとgateを
Tiliquaへ送り、ADC、``QuantizedPitchCV``、gate detector、ADSR、NCO、DACのlive経路を端から
端まで測ります。

.. code-block:: text

    ES-9 OUT 1 -- -1 / 0 / +1 V --> Tiliqua IN 0 --> QuantizedPitchCV
    ES-9 OUT 2 --  0 / 5 V -----> Tiliqua IN 1 --> GateDetector
                                                        |
                                                        v
                       ES-9 IN 1..4 <-- OUT 0..3 <-- live synth

最初の実測は失敗させる
----------------------

default ``pitch_zero_counts=0`` のlive bitstreamへ ``-1/0/+1 V`` を送ると、octave ratioは
正確でしたが、全体が1 semitone低い結果になりました。

.. list-table::
   :header-rows: 1

   * - 入力
     - 期待
     - 未補正の実測
     - 実際のnote
   * - -1 V
     - C2 65.406 Hz
     - 61.776 Hz
     - B1
   * - 0 V
     - C3 130.813 Hz
     - 123.393 Hz
     - B2
   * - +1 V
     - C4 261.626 Hz
     - 247.423 Hz
     - B3

内部self-testのC3/C4は正しかったのでNCO ROMや48 kHz clockの問題ではありません。物理0 Vの
ADC値を ``0 count`` と仮定したcompile-time pitch変換だけが約1 semitoneずれていました。

pitch zero countを物理測定から推定する
---------------------------------------

新しいEEPROM calibrationを書かず、既に測った2つの物理baselineを使います。

``mirror_zero_return``
    ES-9から0 VをTiliqua IN 0へ入れ、mirrorを通してOUT 0で測った値: -0.01667 V

``synth_internal_zero_output``
    live synthのgate OFF中、内部sample=0をOUT 0で測った値: +0.06068 V

同じOUT 0のDAC offsetは差分で消えます。mirror path gain 1.000998とASQの4000 count/Vを使うと、
物理0 Vの推定値は次です。

.. code-block:: text

    zero_counts
      = (-0.016672 - 0.060682) / 1.000998 * 4000
      = about -309 counts

量子化境界から十分離れ、実測noteが中央に入る ``-307`` をこの個体のcompile-time値として採用
しました。これはsynthのpitch mapping用の追加値であり、audio boardのcalibration EEPROMを
上書きするものではありません。別個体へそのままコピーしません。

校正live bitstreamを作る
------------------------

.. code-block:: bash

    pdm dslx_synth build \
      --hw r5 \
      --modeline 720x720p60r2 \
      --name DSLX-SYNTH-CAL \
      --pitch-zero-counts=-307

生成先は ``build/dslx-synth-cal-r5/top.bit`` です。R5の配置配線結果はLUT4 1303、FF 1137、
DSP 2で、最終Fmaxはdvi5x 326.69 MHz、audio 76.21 MHz、sync 64.92 MHz、dvi 95.36 MHzでした。
全clockが制約を満たします。

自動実機テスト
--------------

private management repositoryから実行します。

.. code-block:: bash

    bin/tiliqua synth-live-test

1つの8.6秒patternに次を含めます。

#. 全zero
#. -1 Vをsettleして5 V gate: C2
#. 0 Vをsettleして5 V gate: C3
#. +1 Vをsettleして5 V gate: C4
#. 全zero

ES-9 outputは開閉を繰り返さず1 streamだけです。patternは+-1 V pitch、0/5 V gateの保守的な
範囲に限定し、最後は必ずzeroです。Tiliquaへは校正版bitstreamをSRAMロードし、SPI flashは
変更しません。

校正後の実測
------------

2026-08-17のfresh captureは全contractを通りました。

.. list-table::
   :header-rows: 1

   * - 入力
     - note
     - 実測
     - 誤差
   * - -1 V
     - C2
     - 65.395 Hz
     - -0.011 Hz
   * - 0 V
     - C3
     - 130.790 Hz
     - -0.023 Hz
   * - +1 V
     - C4
     - 261.582 Hz
     - -0.044 Hz

octave ratioは1段目2.00000、2段目2.00001です。return gateはlow +0.007 V、high +4.969 V、
検出burst数は3でした。OUT 3と ``abs(OUT 0)`` はrelative lag -10 sampleで相関0.99222、
gain 0.9988です。

なぜ固定時刻ではなくreturn gateで区間を切るか
------------------------------------------------

AudioToolbox outputとAVFoundation inputを同じES-9で同時使用すると、capture timestampに欠落区間が
入ることがあります。pattern上の開始sampleへ固定offsetを足す方法では、後半のC4を誤った場所で
測る可能性があります。

live testはTiliqua OUT 2から戻る5 V gateをcapture内で検出し、長い3区間を時系列にC2/C3/C4へ
対応させます。各区間のedgeを100 ms捨て、正方向zero crossing periodのmedianを測ります。
これによりhost timestampの欠落をpitch誤差と混同しません。

テストと校正の境界
------------------

このPASSは ``-1..+1 V`` の3点でoffsetと1 V/oct scaleが正しいことを示します。半音量子化版なので、
連続pitch bend、cent単位linearity、温度driftはまだ評価しません。次に連続pitchへ進む場合は、
最低5点以上を測り、offset/scaleとnonlinearityを分けます。

``pitch_zero_counts`` は個体設定です。公開sourceのdefaultへ ``-307`` を埋め込まず、private device
configとbuild commandで再現します。bitstreamは測定中もSRAMだけに置き、長時間安定性とrelease
手順が整うまでSPI flashへ保存しません。

再発防止テスト
--------------

hardware analyzerには、正しいC2/C3/C4 fixtureに加え、全noteを1 semitone下げたB1/B2/B3 fixtureを
明示的にFAILさせるtestがあります。octave ratioだけを見るとどちらも2.0なので、各絶対周波数も
必須contractにします。
