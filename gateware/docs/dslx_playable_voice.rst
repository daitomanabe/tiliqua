1V/oct CV・DSLX ADSR・共有VCAで演奏可能な1ボイスを作る
=========================================================

この段階では、前章の発振器を「一定音を出す部品」から、pitch CVとgateで演奏できる1ボイスへ
拡張します。実装の中心は ``CVPlayableVoice`` です。

.. code-block:: text

    pitch CV (ASQ) --> QuantizedPitchCV --> phase increment --+
                                                            |
    waveform/drive ---------------------> BasicVoice -------+--> one VCA --> sample
    gate + A/D/S/R ----------------------> DSLXADSR ---------+       |
                                                                    +--> envelope/phase

波形を選んだ後にVCAを1台だけ置くため、saw、triangle、squareの3経路を用意しても音量制御用の
DSP multiplierは1個です。自己診断top全体の2個は、codec DC calibration用1個とvoice VCA用
1個です。

1V/octを整数回路へ変換する
--------------------------

TiliquaのASQは1 Vを4000 countとして扱います。tutorialの基準を ``0 V = MIDI note 48 (C3)``
とし、入力を半音へ量子化します。

.. code-block:: text

    semitone = round(abs(cv_counts) * 49 / 16384)

``49 = 32 + 16 + 1`` なので、これは乗算器を使わずshift-addで実装できます。
``12 / 4000`` の近似ですが、4000 countごとのoctave点では正確です。

.. list-table::
   :header-rows: 1

   * - CV
     - count
     - note
   * - -1 V
     - -4000
     - 36 (C2)
   * - 0 V
     - 0
     - 48 (C3)
   * - +0.5 V
     - 2000
     - 54 (F#3)
   * - +1 V
     - 4000
     - 60 (C4)
   * - +2 V
     - 8000
     - 72 (C5)

noteは0から127へclipし、128エントリのROMから32bit NCO phase incrementを読みます。ROM値の
周波数量子化誤差はA4=440 Hzで0.001 Hz未満です。この初版は半音量子化であり、連続pitch bend、
温度補償、個体ごとの1V/oct calibrationはまだ含みません。

ADSRを純粋関数として書く
-------------------------

``dslx/adsr.x`` の入口 ``adsr_step`` は、現在の入力と前回状態だけから次状態を返す純粋関数です。

.. code-block:: text

    adsr_step(
        gate: u1, previous_gate: u1,
        phase: u3, level: u16,
        attack: u16, decay: u16, sustain: u16, release_amount: u16,
    ) -> u20

返り値は ``level[15:0]``、``phase[18:16]``、``gate[19]`` の順にpackします。phaseは
IDLE、ATTACK、DECAY、SUSTAIN、RELEASEの5状態です。加算前に17bitへ広げるattack saturation、
decay/releaseのunderflow防止、release中のretriggerを固定ベクターで検査します。

最初は引数名を ``release`` としましたが、Verilogの予約語なのでRTL name legalizationで拒否されました。
DSLXで型検査を通っても、生成先HDLの予約語まで安全とは限りません。``release_amount`` のように
生成先でも安全なport名を使います。

状態を持つのはwrapper
----------------------

``DSLXADSR`` は純粋関数を1段のelastic registerで包み、previous gate、phase、levelを保持します。
状態更新条件は必ず次のstream転送成立です。

.. code-block:: text

    accept = input.valid && input.ready

``valid=1``、``ready=0`` の間は、出力だけでなくADSR stateも停止します。そのためPC simulation、
実機、下流負荷の違いで包絡線時間がずれません。

VCAのtransactionを安全にする
-----------------------------

既存 ``VCA`` は入力を受理した次のstateでMACを実行します。stream producerは受理直後にpayloadを
変更してよいため、MACが直接 ``i.payload`` を読むと次transactionを誤って乗算できます。今回、
受理時に2 operandをregisterへ保存し、MACはそのregisterだけを読むよう修正しました。

ADSRのunsigned Q0.16 levelは1bit右shiftし、VCAのsigned Q1.15 gainへ変換します。最大値65535は
32767、すなわち約0.99997になります。16bit ASQ operandで十分なので、汎用18bit Q3.15 VCAより
operand routingを狭くしています。

実行するテスト
--------------

.. code-block:: bash

    # Amaranth: CV、ADSR sequence、retrigger、VCA alignment、backpressure
    pdm run pytest -q tests/test_synth.py tests/test_dslx_lab.py

    # DSLX: pure functionのbit-accurate固定ベクター
    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/test_dslx_adsr.sh

    # I2S、codec path、DVIまで
    pdm dslx_lab sim

R5 / 720x720p60r2の統合simulationでは、ADSR自己診断音声が約-10186から+10618 count、
包絡線が0から8904、5V gate経路が0から17998となり、4 DVI frameのreaction contractもPASSしました。

配置配線結果
------------

.. list-table::
   :header-rows: 1

   * - 指標
     - 実測
     - contract
   * - LUT4
     - 1264
     - 1400以下
   * - TRELLIS_FF
     - 1107
     - 1200以下
   * - MULT18X18D
     - 2
     - 2以下
   * - sync最大周波数
     - 62.06 MHz
     - 60 MHz以上

DSPは3波形分の3個ではなく、共有VCAの1個だけ増えました。LFE5U-25F全体ではDSP 2/28、FFは約4%、
combinational cellは約10%です。bitstreamは生成済みですが、リモート作業中は実機へロードしていません。

実機で次に測るもの
------------------

#. 自己診断bitstreamをSPI flashではなくSRAMへロードする
#. OUT 0のattack、sustain、releaseと440 Hzをoscilloscopeで測る
#. OUT 1の包絡線、OUT 2の5 V gate、OUT 3の整流信号を同時に記録する
#. TLQ-SCREENが同じgate/envelopeへ追従することを確認する
#. その後にlive input版へ切り替え、IN 0へpitch CV、gate inputへgateを接続する

``CVPlayableVoice`` を物理jackへ割り当てる専用 ``dslx_synth`` topも追加しました。入力配線、
自己診断/live両profileのtiming検査、コンパイル時pitch校正は :doc:`dslx_live_synth` を参照してください。
実機での1V/oct測定と個体EEPROM calibrationの自動読出しは次段階です。
