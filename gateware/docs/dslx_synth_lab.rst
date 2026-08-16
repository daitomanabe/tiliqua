DSLX Synth Lab: 実機がなくても改善を続ける
===========================================

この開発環境の狙いは、Tiliqua が手元にない期間にもシンセ回路を変更し、壊れた場所を
自動的に絞り込めるようにすることです。実機が戻ったときは、PC上と同じ決定論的信号を
DACとDVIへ出し、最後の物理層だけを測定します。

比喩で捉えると、これはシンセ開発用の「階段状の検査場」です。最初の段では小さな部品、
次の段ではDSLX、次にcodecを含む仮想Tiliqua、最後に実機を検査します。下の段で失敗した
まま上へ進まないため、問題がアルゴリズム、接続、タイミング、物理配線のどこにあるかを
切り分けられます。

.. code-block:: text

    速い・毎回実行
       |
       +-- Python unit test -------- NCO、型、backpressure
       +-- DSLX vector test -------- 境界値、ゲート、RGB
       +-- Verilator integration --- I2S、DSLX、DVI、4フレーム
       +-- Contract evaluation ----- 音声統計、RGB範囲、checksum
       +-- FPGA build -------------- 配置配線、全clock timing
       +-- Hardware bench ---------- 電圧、周波数、画面、ケーブル
       |
    遅い・実機があるとき

最初に実行するコマンド
----------------------

``gateware`` ディレクトリから、次の1コマンドでローカル回帰を実行します。

.. code-block:: bash

    pdm dslx_lab check

``xlsynth-driver`` が PATH にない場合でも、チェックイン済みの生成RTLを使って
シミュレーションできます。DSLXソースの固定ベクターも実行する場合は明示します。

.. code-block:: bash

    pdm dslx_lab \
      --xlsynth-driver /absolute/path/to/xlsynth-driver \
      check

コマンドは次のように分割できます。

.. list-table::
   :header-rows: 1

   * - コマンド
     - 用途
   * - ``pdm dslx_lab doctor``
     - ツール、生成RTL、試験契約の存在確認
   * - ``pdm dslx_lab quick``
     - NCO単体テストとDSLX固定ベクター
   * - ``pdm dslx_lab sim``
     - 自己診断AVシミュレーションと自動判定
   * - ``pdm dslx_lab report``
     - 最新メトリクスを試験契約で再判定
   * - ``pdm dslx_lab build``
     - 自己診断bitstreamを作り、timingのFAILも検出
   * - ``pdm dslx_lab check``
     - doctor、quick、simをまとめて実行

native FPGA toolchainが設定済みなら、配置配線まで含めた全検査も1コマンドです。

.. code-block:: bash

    pdm dslx_lab --with-build check

演奏用 ``dslx_synth`` は、内部信号版だけでなくlive ADC版のtimingも必ず検査します。

.. code-block:: bash

    pdm dslx_synth_lab --with-build check

この段では ``dslx/lab_synthesis_contract.json`` によりLUT、FF、DSP multiplier、全clockの
budgetも自動判定します。

PCに接続されたTiliquaを変更せず一覧だけ確認したい場合は、doctorへ
``--hardware`` を付けます。

.. code-block:: bash

    pdm dslx_lab --hardware doctor

再利用できるシンセ部品
----------------------

``src/tiliqua/dsp/synth.py`` に、小さく独立した2つの部品があります。

``BasicNCO``
    32ビット位相、フルスケール16ビット出力のNCOです。1つの位相から saw、triangle、
    square を同時に返します。入力と出力は ``ready`` / ``valid`` stream なので、下流が
    止まると位相も完全に停止します。gainは波形選択後に適用し、不要な乗算器を生成しません。

``SynthTestSource``
    ``PlayableVoice`` を使う決定論的4ch信号源です。1024サンプルごとにADSR gateを切り替えるため、
    voice VCAとreactor包絡線のattack/release、ゲートのON/OFFを1回の試験で通せます。

``BasicVoice`` / ``DSLXVoice``
    NCOの3波形から1つを先に選び、DSLXでpower-of-two driveとsigned saturationを適用します。
    詳細は :doc:`dslx_synth_voice` を参照してください。

``CVPlayableVoice`` / ``DSLXADSR``
    ASQ pitch CVを半音量子化1V/octへ変換し、gate-driven ADSRと共有VCAを適用します。詳細は
    :doc:`dslx_playable_voice` を参照してください。

``DyadicGain``
    multiplierを使わない定数gainです。signed ``FLOOR`` と ``NEAREST_AWAY`` の丸めを明示的に
    選択できます。自己診断の3/8 gainには ``NEAREST_AWAY`` を使います。

自己診断入力の内容は次の通りです。

.. list-table::
   :header-rows: 1

   * - channel
     - 内容
     - 目的
   * - 0
     - 440 Hz triangleへADSRと3/8 gainを適用
     - 符号、周波数、attack/sustain/release
   * - 1
     - 4096 count の一定しきい値
     - gate detector
   * - 2
     - +/-12288 count のburst marker
     - 試験タイミングの観測
   * - 3
     - triangle の絶対値
     - 整流と単極信号

自己診断gain ``3/8`` はshift-addで実装されるためDSP multiplierを使いません。
``phase_increment()`` は周波数とサンプルレートから32ビット位相増分を計算します。
例えば ``phase_increment(440.0, 48_000)`` の量子化誤差は 0.001 Hz 未満です。

自己診断トップ
--------------

``dslx_av`` に ``--self-test`` を付けると、ADCの代わりに ``SynthTestSource`` が
``reactor.x`` へ接続されます。DSLX以降はliveモードと同じ経路です。

.. code-block:: text

    SynthTestSource
          |
          v
    DSLX reactor -----> codec DAC -----> OUT 0..3
          |
          +-----------> clock crossing -> DSLX visualizer -> DVI

したがってPCシミュレーションで成功し、実機で失敗した場合は、codec設定、校正、DAC、
GPDI、screen、クロックなど物理側へ調査範囲を狭められます。

自動判定されるもの
------------------

専用Verilator harnessは ``dslx-av-metrics.json`` を生成します。

.. code-block:: text

    audio[0..3]
      samples, min, max, nonzero, zero_crossings, mean_abs

    dvi
      frames, pixels, RGB min/max, pixel checksum

    internal
      self_test flag, accepted sample count, NCO phase

判定基準は ``dslx/lab_contract.json`` に置いています。現在は特定1点との完全一致ではなく、
「音声0が両極性で8000 countを越える」「ゲートが0と15000以上の両方を通る」
「RGB各成分に8以上の変化がある」のような不変条件です。合成ツール更新による無関係な
差分に強く、本当に音や映像が止まった場合には失敗します。

.. figure:: /_static/dslx_lab_selftest.png
   :width: 480px

   内部burst信号で生成した自己診断フレーム。

空白を含む作業パスへの対応
--------------------------

Verilatorが生成するGNU Makefileは、Dropboxのように空白を含むパスを正しく扱えない
場合があります。simulation helperはコンパイル入力だけを ``/tmp`` の短いパスへコピーし、
実行結果を元の ``gateware`` ディレクトリへ出します。手動worktreeは不要です。

実機が手元に戻ったとき
----------------------

まず自己診断版をビルドします。

.. code-block:: bash

    cd gateware
    pdm dslx_av build \
      --hw r5 \
      --modeline 720x720p60r2 \
      --self-test

macOSでYoWASP版Yosysまたはnextpnrが停止する場合は、native OSS CAD Suiteの実行ファイルを
環境変数で指定します。

.. code-block:: bash

    YOSYS=/path/to/oss-cad-suite/bin/yosys \
    NEXTPNR_ECP5=/path/to/oss-cad-suite/bin/nextpnr-ecp5 \
    ECPPACK=/path/to/oss-cad-suite/bin/ecppack \
      pdm dslx_av build \
        --hw r5 --modeline 720x720p60r2 --self-test

接続対象を確認後、SPI flashではなく揮発性SRAMへロードします。

.. code-block:: bash

    openFPGALoader --scan-usb
    openFPGALoader -c dirtyJtag build/dslx-av-lab-r5/top.bit

期待する実機出力は次の通りです。

* OUT 0: 約 +/-3 V の440 Hz triangleが約21.3 msごとにON/OFF
* OUT 1: burstへ速く追従し、ゆっくり戻る単極包絡線
* OUT 2: 0 V / 5 V のゲート
* OUT 3: 0 V以上の全波整流triangle
* TLQ-SCREEN: 中央の四角と十字がburstに合わせて伸縮

オシロスコープまたはDC対応audio interfaceでOUT 0から3を記録し、TLQ-SCREENを目視または
captureします。これが成功したら、同じbitstreamを ``--self-test`` なしで作り、IN 0/1を
使うliveモードへ戻します。

安全な改善ループ
----------------

#. DSLXまたは ``BasicNCO`` を小さく変更する
#. ``pdm dslx_lab quick`` で部品と境界値を確認する
#. ``pdm dslx_lab sim`` でI2S/DVI全経路を確認する
#. ``dslx-av-metrics.json`` の変化が意図通りか確認する
#. FPGA build後に全clockが ``PASS`` か確認する
#. 実機があるときだけSRAMへロードして物理信号を測る
#. 良い変化なら試験ベクターまたはcontractへ新しい不変条件を加える

よくあるつまずき
----------------

* 位相、包絡線、乱数などの状態は、stream転送成立時だけ進めます。backpressure中に進む回路は
  音の再現性がなくなります。
* 画面画像のhashだけを合否にすると、無害な1画素差でも失敗します。まずRGB範囲や画素数を
  不変条件にし、厳密な見た目が必要な部分だけgolden image比較を追加します。
* VerilatorのPASSはanalog出力電圧やDVI eye patternを保証しません。最後の実機測定は省略
  できません。
* ``--self-test`` は入力ジャックを使いません。live入力の検証では必ずフラグを外します。
* 実機開発中はSRAMロードを使います。SPI flashへの永続書き込みは、十分に検証したrelease
  bitstreamだけにします。
