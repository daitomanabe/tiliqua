DSLXでリソース効率の良い1ボイスを作る
=========================================

このvoiceは、3台のエフェクターへ同じ音を送り、最後に1台だけ選ぶ構成ではありません。
先に波形を1つ選び、その1本だけをdriveやVCAへ送る小さなペダルボードです。FPGAではこの順序が
重要で、波形ごとに乗算してから選ぶと、聞こえない経路にもDSP multiplierを使う可能性があります。

.. code-block:: text

                           DSLX voice_sample
    phase accumulator ---> saw -----------+
                      +--> triangle ------+--> select --> x1/x2/x4/x8 --> saturate
                      +--> square --------+                         |
                                                                    v
                              dyadic constant gain ----------> one audio stream
                                                                    |
                                                    shared ADSR VCA / MAC

役割の分割
----------

``BasicNCO``
    32bit phase accumulatorからフルスケールのsaw、triangle、squareを同時生成します。gainや
    clipping policyは持ちません。

``DSLXVoice``
    ``dslx/voice.x`` から生成した組合せ回路を1段のelastic output registerで包みます。波形選択、
    power-of-two drive、signed saturationを担当します。

``BasicVoice``
    ``BasicNCO`` と ``DSLXVoice`` を型付きstreamで接続します。入力transactionはphase increment、
    waveform、driveをまとめて運ぶため、backpressure中にcontrolだけが先へ進みません。

``DyadicGain``
    ``numerator / 2**fractional_bits`` の定数gainです。乗算器ではなくshift-addへ合成され、
    ``FLOOR`` または ``NEAREST_AWAY`` のsigned roundingを選べます。

DSLXカーネルを読む
-------------------

入口は次の純粋関数です。

.. code-block:: text

    voice_sample(
        saw: s16,
        triangle: s16,
        square: s16,
        waveform: u2,
        drive: u2,
    ) -> s16

``waveform`` と ``drive`` の対応は固定しています。

.. list-table::
   :header-rows: 1

   * - 値
     - waveform
     - drive
   * - 0
     - saw
     - x1
   * - 1
     - triangle
     - x2
   * - 2
     - square
     - x4
   * - 3
     - silence
     - x8

driveは一度19bitへ符号拡張してから左shiftします。その後、``32767`` より大きければ正側上限、
``-32768`` より小さければ負側上限へclipします。16bitのままshiftしてから判定すると先にoverflow
してしまうため、判定用の幅を広げる順序が重要です。

生成とbit-accurate test
-----------------------

.. code-block:: bash

    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/test_dslx_voice.sh

    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/generate_dslx_voice.sh

固定ベクターは4波形の選択、正負両側のsaturation、drive、silenceを検査します。生成RTLだけを
テストするのではなく、Amaranth simulation modelにも同じ境界テストを通します。これにより
DSLX toolを入れていない環境でも上位streamの単体試験を実行できます。

丸めを選ぶ
----------

自己診断の振幅はフルスケールの ``3/8`` です。たとえば入力 ``2`` は正確には ``0.75`` になります。

.. list-table::
   :header-rows: 1

   * - 入力
     - FLOOR
     - NEAREST_AWAY
   * - 2
     - 0
     - 1
   * - -1
     - -1
     - 0
   * - 4
     - 1
     - 2
   * - -4
     - -2
     - -2

単なるarithmetic right shiftは負数を負の無限大方向へ丸めます。音声では小振幅の負側だけにbiasが
残るため、自己診断ではnearestを使います。CPUのfloatへ暗黙変換せず、RTL上のpolicyとして
選択するのがポイントです。

backpressureと1サンプルの先行
------------------------------

``DSLXVoice`` のoutput registerが空なら、下流が停止中でも最初の1サンプルを受け取れます。その後は
``ready`` が戻るまで出力、control、位相を保持します。したがって観測用 ``sample_index`` よりNCO
phaseが最大1サンプル先行することがあります。これはデータ損失ではなくelastic buffer内の保留分です。

.. code-block:: text

    output empty:  NCO --transfer--> [pending sample] --X--> downstream
    output full:   NCO -----hold----> [stable sample] --X--> downstream
    ready again:  NCO --next-------> [next sample] -------> downstream

資源とtiming
------------

R5、720x720p60r2、自己診断AV topの配置配線結果です。``DSLX voice`` 単体の段階と、
``DSLXADSR`` / 共有VCAまで統合した現在を比較します。

.. list-table::
   :header-rows: 1

   * - 指標
     - DSLX voice追加後
     - PlayableVoice統合後
     - 現contract
   * - ``LUT4``
     - 1146
     - 1264
     - 1400以下
   * - ``TRELLIS_FF``
     - 1037
     - 1107
     - 1200以下
   * - ``MULT18X18D``
     - 1
     - 2
     - 2以下
   * - sync最大周波数
     - 65.91 MHz
     - 62.06 MHz
     - 60 MHz以上

波形選択とdriveだけではDSP multiplierは増えません。ADSRで変化するgainを適用する共有VCAを
加えたため1個増え、現在はcodec DC calibration用1個とvoice用1個です。詳細は
:doc:`dslx_playable_voice` を参照してください。

次の実機確認
------------

実機が手元に戻ったら、まず自己診断bitstreamをSRAMへロードし、triangleの周波数、正負電圧、
burst境界、DVIを測ります。その後 ``BasicVoice`` の ``waveform`` を0、1、2へ順に切り替えたbench
bitstreamを用意し、OUT 0でsaw、triangle、squareを確認します。SPI flashへは書き込みません。

よくあるつまずき
----------------

* saturation判定より前に16bitへ縮めると、overflow後の値をclipするため極性まで誤ります。
* ``valid`` が1で ``ready`` が0の間は、waveformやdriveもpayloadと一緒に保持します。
* driveはVCAではありません。連続CVによる音量制御には、選択後の1信号を共有 ``VCA`` / ``MAC``
  へ送ります。
* elastic registerを追加するとlatencyは増えます。throughputは1 sample/clockのままです。
