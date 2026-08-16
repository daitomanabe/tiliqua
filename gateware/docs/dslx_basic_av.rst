Tutorial 3: DSLX で音を映像にする
=================================

このチュートリアルでは、Google DSLX で書いた小さな回路を Tiliqua に組み込み、
入力音の大きさに反応する 720 x 720 の映像を TLQ-SCREEN に表示します。
完成する ``dslx_av`` ビットストリームは、音声をそのまま返しながら包絡線、
ゲート、瞬時振幅を生成し、その3値で中央の四角、十字、背景色を変化させます。

最初に比喩で捉えると、DSLX の関数は「一画素を塗るための判定スタンプ」です。
CPU が画面全体を描き終えてから表示するのではありません。DVI の走査線が来るたびに、
現在座標と音の特徴量をスタンプへ入れ、その瞬間の RGB を返します。FPGA はこのスタンプを
毎ピクセル同時進行で動かせるため、フレームバッファなしでも映像を作れます。

この段階のゴール
------------------

* IN 0 の音声または CV が OUT 0 へそのまま出る
* OUT 1 にアタック／リリース付き包絡線が出る
* OUT 2 にヒステリシス付き 5 V ゲートが出る
* OUT 3 に全波整流した瞬時振幅が出る
* TLQ-SCREEN に音量で伸縮する四角と十字が出る

完成後の信号経路は次のようになります。

.. code-block:: text

    Eurorack IN 0/1
           |
           v
    +------------------+       ready / valid
    | I2S ADC + 校正   | --------------------------+
    +------------------+                           |
                                                    v
                                           +----------------+
                                           | reactor.x      |
                                           | 音量・包絡線   |
                                           | ゲート判定     |
                                           +----------------+
                                             |           |
                                  OUT 0..3 <-+           +-> clock domain crossing
                                                             |
                                                             v
       DVI timing (x, y, frame) --------------------> +------------------+
                                                     | visualizer.x     |
                                                     | 1 pixel -> RGB   |
                                                     +------------------+
                                                             |
                                                             v
                                                       DVI / TLQ-SCREEN

ソースコードの地図
------------------

この例では「アルゴリズム」と「Tiliqua への接続」を分けています。

.. code-block:: text

    dslx/reactor.x                         音声1サンプルの計算
    dslx/visualizer.x                      映像1ピクセルの計算
    dslx/generated/*.v                     XLS が生成した Verilog
    src/tiliqua/dsp/dslx_reactor.py        音声 stream と状態のラッパー
    src/tiliqua/video/dslx_visualizer.py   RGB 信号のラッパー
    src/top/dslx_av/top.py                 ADC、DSLX、DVI、DAC の結線

生成済み Verilog はリポジトリに含めています。そのため、通常のシミュレーションや
ビットストリーム作成に XLS は不要です。DSLX を変更して Verilog を再生成するときだけ
``xlsynth-driver`` を使います。

1. DSLX を「幅が決まった純粋関数」として読む
---------------------------------------------

映像側の入口は ``visualizer_pixel`` です。型名の数字がそのまま配線幅になります。

.. code-block:: text

    pub fn visualizer_pixel(
        x: u12,
        y: u12,
        center_x: u12,
        center_y: u12,
        envelope: u16,
        gate: u1,
        magnitude: u16,
        frame: u8,
    ) -> u24

``u12`` は符号なし12ビット、``u16`` は符号なし16ビット、``u1`` は1ビットです。
戻り値 ``u24`` には RGB の3バイトを ``0xBBGGRR`` の順で詰めています。DSLX は
暗黙の幅変更をほとんど許さないので、例えば ``frame`` を座標へ足す前に
``frame as u12`` と明示します。これは少し厳格ですが、合成後に上位ビットが意図せず
消える問題をソース上で発見しやすくします。

画素の計算手順は次の5段階です。

#. 現在座標と画面中心の距離 ``dx``、``dy`` を求める
#. 包絡線を四角の半径 ``32..95`` ピクセルへ縮小する
#. 瞬時振幅を十字の幅 ``8..39`` ピクセルへ縮小する
#. フレーム番号を横方向へ加え、動く32ピクセル周期のチェック柄を作る
#. ゲート、四角、十字、チェック柄の優先順で RGB を選ぶ

この関数にはレジスタ状態がありません。同じ入力なら常に同じ RGB が返る純粋な
組み合わせ回路です。状態を持つフレームカウンタは Amaranth 側に置いています。

2. 音声では状態をラッパー側に置く
----------------------------------

``reactor_step`` も DSLX 上では純粋関数ですが、引数として前回の包絡線とゲートを受け、
次回値も戻します。

.. code-block:: text

    (sample, threshold_cv, previous_envelope, previous_gate)
                                |
                                v
                         reactor_step
                                |
                                v
       (next_envelope, next_gate, passthrough, envelope, gate, magnitude)

包絡線は信号が大きくなったとき差分の ``1/8``、小さくなったとき差分の ``1/128``
だけ追従します。立ち上がりは速く、戻りは遅くなります。ゲートは設定しきい値で ON、
その ``7/8`` で OFF にします。ON と OFF を別の値にするヒステリシスにより、しきい値
付近でゲートが細かく往復するのを防ぎます。

DSLX の結果は81ビットへ詰めて返します。``dslx_reactor.py`` は各範囲を切り出し、
Tiliqua の4チャンネル audio stream に戻します。同時に、次のサンプルで使う
``previous_envelope`` と ``previous_gate`` をレジスタへ保存します。

3. ``ready`` / ``valid`` で1サンプルを確定する
------------------------------------------------

音声 stream は「荷物」と「受領確認」の組です。``valid`` は送り側が有効な荷物を
置いたこと、``ready`` は受け側が受け取れることを表します。両方が1のクロックだけが
転送成立です。

.. code-block:: text

    upstream       DSLX reactor         downstream
       valid -----> [ payload register ] -----> valid
       ready <----- [ one-entry buffer  ] <----- ready

``DSLXReactor`` は1エントリの elastic register を持ちます。下流が停止した場合は、
出力値だけでなく包絡線とゲートの状態も保持します。これを守らないと、聞こえるサンプルは
止まっているのに内部の包絡線だけ進む、というずれが起きます。

4. 音声クロックから映像クロックへ渡す
--------------------------------------

このトップには主に3つのクロック領域があります。

.. code-block:: text

    sync   60.000 MHz   DSLX 音声リアクターと制御
    audio  12.288 MHz   I2S codec
    dvi    39.070 MHz   720 x 720 の画素生成
    dvi5x 195.350 MHz   DVI シリアライズ

最後に受理した envelope、gate、magnitude を ``sync`` 側で保持し、2段同期器で
``dvi`` 側へ渡します。この簡易方式では複数ビットが同時に切り替わる瞬間に、1画素程度の
古い値と新しい値が混ざる可能性があります。今回のゆっくり変化する視覚パラメータでは
許容しています。計測値を完全に一致させる用途では、非同期 FIFO または snapshot
handshake に置き換えてください。

5. DSLX の固定テストを走らせる
--------------------------------

このプロジェクトは ``xlsynth-driver 0.65.0`` と、それに含まれる Google XLS
``v0.54.6`` を固定しています。driver の絶対パスを指定してテストします。

.. code-block:: bash

    cd gateware
    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/test_dslx_reactor.sh
    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/test_dslx_visualizer.sh

音声テストにはゼロ、符号付き最小値、しきい値境界、ゲートの ON/OFF 境界を含めています。
映像テストには無音、中心／画面端、ゲート ON、チェック柄のフレーム移動を含めています。
成功時はそれぞれ ``vectors passed`` と表示されます。

DSLX を変更した場合は、テスト後に Verilog を再生成します。

.. code-block:: bash

    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/generate_dslx_reactor.sh
    XLSYNTH_DRIVER=/absolute/path/to/xlsynth-driver \
      ./scripts/generate_dslx_visualizer.sh

生成物の差分もレビューしてください。XLS と DSLX の学習には、公式の
`DSLX language reference <https://google.github.io/xls/dslx_reference/>`_ と
`XLS tutorials <https://google.github.io/xls/tutorials/>`_ も参照できます。

6. まずPC上で4フレーム確認する
-------------------------------

TLQ-SCREEN と同じ modeline で Verilator シミュレーションを実行します。

.. code-block:: bash

    cd gateware
    pdm dslx_av sim --hw r5 --modeline 720x720p60r2

完了すると ``frame00.bmp`` から ``frame03.bmp`` が作られます。今回の固定テスト入力では
次のようなフレームになります。

.. figure:: /_static/dslx_basic_av_sim.png
   :width: 480px

   DSLX が毎画素生成したシミュレーションフレーム。中央形状と背景が音に反応する。

7. Tiliqua R5 用ビットストリームを作る
---------------------------------------

.. code-block:: bash

    cd gateware
    pdm dslx_av build --hw r5 --modeline 720x720p60r2

生成先は ``build/dslx-av-r5/top.bit`` です。配置配線後の timing report で、少なくとも
``sync``、``audio``、``dvi``、``dvi5x`` がすべて ``PASS`` であることを確認します。

まずは SPI flash を変更せず、揮発性 SRAM へロードします。

.. code-block:: bash

    openFPGALoader --scan-usb
    openFPGALoader -c dirtyJtag build/dslx-av-r5/top.bit

ロード前に一覧が Tiliqua R5 1台だけであることを確認してください。SRAM の内容は
電源再投入で消えるため、開発中の試行に向いています。映像が出ない場合は、起動時に
bootloader が選んだ映像クロックと ``--modeline`` が一致しているかを最初に確認します。

入出力と見え方
--------------

.. list-table::
   :header-rows: 1

   * - 端子
     - 役割
   * - IN 0
     - 解析する音声または CV
   * - IN 1
     - しきい値 CV。絶対値が小さいときは既定値 4096、約 1.024 V
   * - OUT 0
     - IN 0 のパススルー
   * - OUT 1
     - 包絡線
   * - OUT 2
     - 5 V ゲート
   * - OUT 3
     - 瞬時振幅
   * - GPDI
     - 固定 720 x 720 DVI 映像

よくあるつまずき
----------------

* **1ビット抽出も slice にする:** この版の DSLX では ``phase_x[5]`` が配列 index と
  解釈されるため、1ビット幅の ``phase_x[5:6]`` を使います。
* **幅は勝手に広がらない:** ``u8`` の ``frame`` と ``u12`` の ``x`` を足す前に
  ``frame as u12`` が必要です。シフト量にも ``u3:2`` のように型を付けます。
* **RGB の並びを両側で合わせる:** DSLX は ``0xBBGGRR``、Python wrapper は
  ``[0:8]`` を R、``[8:16]`` を G、``[16:24]`` を B として取り出します。
* **状態更新は転送成立時だけ:** ``valid`` と ``ready`` の両方が1のときだけ
  envelope と gate を更新します。
* **modeline は固定:** TLQ-SCREEN は scaler を持たないので、``720x720p60r2`` と
  異なる映像モードでは表示できません。
* **合成できても timing は別問題:** DSLX の式を大きくしたら ``top.tim`` の
  ``dvi`` 最大周波数を再確認します。必要なら式を複数段へ pipeline 化します。

次に試す小さな改造
------------------

#. ``radius`` の右シフト量を変え、包絡線に対する四角の感度を変える
#. ``red``、``green``、``blue`` の条件を交換して配色を作る
#. ``frame`` のシフト量を変え、背景の移動速度を変える
#. IN 1 に CV を入れ、ゲートが発火する音量を演奏中に変える

変更のたびに「固定ベクターテスト → 4フレームシミュレーション → timing 確認 → SRAM
ロード」の順に進めると、DSLX の計算ミスと実機固有の問題を切り分けやすくなります。
