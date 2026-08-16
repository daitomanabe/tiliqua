FPGA DSPライブラリ比較とTiliqua向け設計方針
================================================

FPGA DSP環境は楽器工房に似ています。FloPoCoは高精度な専用工具、Vitis DSPは特定工場向けの
大型加工ライン、Hardcamlは型安全な工具一式です。Tiliquaで必要なのは、工具を全部入れ替える
ことではなく、ECP5、codec、DVI、既存Amaranth回路をつなぐ作業台を保ったまま、適切な工具を
演算ごとに使い分けることです。

.. code-block:: text

    control / codec / DVI          arithmetic kernel            expensive multiply
    Amaranth typed stream  ----->  DSLX fn / generated RTL ---> shared Tiliqua MAC
             |                              |                            |
             +-- FIFOでtiming分割           +-- vector / QuickCheck      +-- DSP予算

比較
----

.. list-table::
   :header-rows: 1
   :widths: 18 23 25 18 24

   * - 環境
     - 得意分野
     - 精度・パイプライン
     - Tiliqua適合性
     - 判断
   * - Tiliqua DSP + Amaranth
     - codec、stream、CDC、ECP5、audio rateのMAC共有
     - fixed shapeを明示。pipelineはRTLで明示
     - 非常に高い。既存資産をそのまま使える
     - システム骨格として継続
   * - Google XLS / DSLX
     - 任意bit幅の純粋関数、テスト可能な演算kernel、RTL生成
     - scheduler/codegenでpipelineを生成可能
     - 高い。生成VerilogをAmaranth componentで包める
     - 新規DSPアルゴリズムの第一選択
   * - FloPoCo 5
     - 乗算、FMA、浮動小数、平方根などの専用算術core
     - 必要bitだけを計算し、丸めと目標周波数を指定
     - 中。生成VHDLのwrapperと検証が必要
     - 複雑な算術coreが必要な時だけ導入
   * - Hardcaml + circuits / fixed_point
     - 強い型、parameterized RTL、simulation、固定小数点
     - roundingとoverflow policyを明示できる
     - 中から低。OCamlへの設計移植が必要
     - 設計規範を採用し、全面移行はしない
   * - AMD Vitis DSP IP Library
     - FIR、FFT、高throughput accelerator
     - cascade、shift、rounding、stream幅を詳細設定
     - 低い。AMD/Xilinx PL・AI Engine中心でECP5と合わない
     - Tiliqua本体には採用しない

根拠となる公式資料は `XLS overview <https://google.github.io/xls/>`_、
`XLS codegen options <https://google.github.io/xls/codegen_options/>`_、
`FloPoCo <https://flopoco.org/>`_、
`FloPoCo operator list <https://www.flopoco.org/operators_5.0.git.html>`_、
`Hardcaml <https://github.com/janestreet/hardcaml>`_、
`AMD Vitis DSP IP Library 2025.2 <https://docs.amd.com/r/en-US/Vitis_IP_Libraries/dsp/release.html_0>`_、
`Amaranth data streams <https://amaranth-lang.org/docs/amaranth/latest/stdlib/stream.html>`_ を参照しました。

最適化方針
----------

比較から次の規則をTiliqua DSLX labへ適用します。

#. ``ready`` と ``valid``、codec、CDC、DVIはAmaranth componentに置く。
#. filter、waveshaper、envelopeのような副作用のない演算はDSLXの小さな関数にする。
#. DSLX入出力は幅とsignednessを固定し、生成RTLを薄いstream wrapperで包む。
#. 定数gainは乗算せず、dyadic fraction（整数 / 2のべき乗）のshift-addへ落とす。
#. 可変gainやvariable-by-variable積だけを ``MuxMAC`` / ``RingMAC`` / ``VCA`` へ渡す。
#. timingを長い式の途中で切る必要があれば、block境界に小さなFIFOを入れる。
#. waveform、DSLX vector、AV integration、配置配線QoRを同じコマンドで検査する。

NCOで行った最適化
-------------------

以前の ``BasicNCO`` はsaw、triangle、squareの全出力に可変振幅乗算を記述していました。
利用していない波形は合成時に消えるものの、自己診断で選んだtriangleにはDSP tileが1個必要でした。
現在はNCOがフルスケール波形だけを作り、選択後にgainを適用します。自己診断のgainは正確な
``3/8`` なので次の形です。

.. code-block:: text

    y = (x + (x << 1)) >> 3

これは乗算器を使わず、加算器1個と配線上のshiftだけで実装できます。任意のCVで振幅を変える
実際のvoiceでは、選択済みの1波形だけを既存 ``VCA`` に通します。

配置配線後の変化
----------------

同じR5、720x720p60r2、self-test topでnative OSS CAD Suiteを使って比較しました。

.. list-table::
   :header-rows: 1

   * - 指標
     - 変更前
     - 変更後
     - 差
   * - ``MULT18X18D``
     - 2
     - 1
     - -1、自己診断NCO分を除去
   * - ``LUT4``
     - 1114
     - 1084から1090
     - -24から-30
   * - ``TRELLIS_FF``
     - 1020
     - 1016
     - -4
   * - sync最大周波数
     - 64.75 MHz
     - 64.23から66.41 MHz
     - 60 MHz制約をPASS

次の段階でDSLXによる波形選択、drive、saturationと、signed nearest roundingを追加した時点では
``LUT4=1146``、``TRELLIS_FF=1037``、``MULT18X18D=1``、sync最大周波数 ``65.91 MHz`` でした。
音色処理を増やしてもDSP tile数は維持されています。

残る1個の ``MULT18X18D`` はcodecのDC calibration用です。NCO最適化によりDSP使用数は
50%減り、同期clockには約7%の余裕があります。配置配線結果にはseedやtool versionによる
揺れがあるため、周波数の小差ではなく制約PASSと資源上限を回帰条件にします。

QoR回帰contract
---------------

``dslx/lab_synthesis_contract.json`` はLUT、FF、DSP tile、各clockの上限・下限を定義します。
``pdm dslx_lab build`` と ``pdm dslx_lab --with-build check`` は ``top.rpt`` と ``top.tim`` を
読み、音や映像が正しくてもFPGA資源またはtimingが悪化した変更を失敗にします。

.. code-block:: text

    FPGA resource summary
      LUT4                1146 / 1200
      TRELLIS_FF          1037 / 1100
      MULT18X18D             1 / 1

他ライブラリを追加する条件
--------------------------

平方根、特殊関数、異なる精度のFMAなどをDSLXで手書きする前にFloPoCoを評価します。ただし、
generated VHDLのbit-accurate比較、license、wrapper、ECP5 timingを導入条件にします。FFTやFIRは
まず既存 ``tiliqua.dsp.fft``、``filters``、共有MACを測定します。AMD Vitisの実装例はarchitecture
やbenchmarkの参考にしますが、ECP5向け設計へvendor IPを直接持ち込みません。

よくあるつまずき
----------------

* 全波形へ先にVCAを置くと、選択前の不要な乗算器まで生成されます。波形選択後にgainを掛けます。
* ``>>`` の丸めは負数でfloor方向です。音質に影響する段では、truncate、round-to-nearest、saturateを
  API名とテストで明示します。
* audio sample rateが低くてもsystem clockの組合せpathは60 MHz制約を受けます。sample間に時間が
  あることだけではtimingは緩和されません。
* 自動pipelineはlatencyを変えます。audio/video controlを並走させる場合は、sample tagまたは遅延を
  contractへ含めます。
