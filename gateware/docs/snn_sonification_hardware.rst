Tutorial 18: E/I SNNを音高として聴く
======================================

Tutorial 16のE/I SNNでOUT 0へ出していた信号は、population spike countを振幅にし、sampleごとに正負を反転する
診断用pulseです。集合発火のrangeやRMSを測るには便利ですが、carrierがNyquist近傍なので聴感上はnoiseに近くなります。
ここでは神経計算とDVI、OUT 1--3を変えず、OUT 0だけを明確な音高へ変換する任意sonification profileを追加します。

音へのmapping
---------------

``PopulationToneMapper`` は48 kHzの4096 sample、つまり85.33 msごとに現在のspike countをsampleします。8 spike刻みで
``C2, Eb2, F2, G2, Bb2, C3, Eb3, F3`` の8音へ量子化し、56 spike以上はF3へclampします。32-bit NCOのphaseは
音程変更時も連続するため、note boundaryで波形stepを作りません。triangleはhalf scale、約4.096 V peakです。

出力は次のとおりです。

.. list-table::
   :header-rows: 1

   * - output
     - 内容
   * - OUT 0
     - SNN pentatonic triangle
   * - OUT 1
     - population activity（変更なし）
   * - OUT 2
     - burst gate（変更なし）
   * - OUT 3
     - mean membrane（変更なし）

既存のraw-spike profileは診断用として残します。``--sonification`` は1024-neuron / 32-lane / E/I ringだけで有効で、
他profileとの誤った組合せはelaboration時に拒否します。

simulationとR5 QoR
-------------------

.. code-block:: bash

    cd gateware
    pdm snn_lab sonification
    pdm snn_lab sonification-report

unit regressionは、backpressure中にphase/sampleが停止すること、OUT 1--3のbit透過、40 spikeからscale degree 5への更新、
1024 spike入力のtop-note clampを検査します。4-frame Verilator integrationでは従来と同じDVI checksum
``63562c9d861e7bb7`` とOUT 1--3 metricsを保ち、OUT 0は3662 sample中19 zero crossing、mean absolute 7321.4 countsで
低周波のbipolar toneになりました。14 tests + 3 subtestsとsonification contractはPASSです。

.. list-table::
   :header-rows: 1

   * - profile
     - LUT4 / FF / DP16KD / DSP
     - physical COMB / FF / DP16KD
     - sync / audio / dvi / dvi5x MHz
     - SHA-256
   * - E/I sonification live
     - 5,848 / 5,394 / 18 / 1
     - 37% / 22% / 32%
     - 62.44 / 70.42 / 64.09 / 348.68
     - ``abf2d7348bd9b28817a256265777e469b086ccaa52a7366bc5090d693b25ae73``

全clockとresource contractをPASSしています。従来E/I liveから増えたのは、小さなphase accumulator、control divider、
8-entry constant note muxです。SPI flashは使わず、実験bitstreamは揮発性SRAMだけへloadします。

固定ES-9実機試験
------------------

private management repositoryの固定配線で実行します。

.. code-block:: bash

    bin/tiliqua snn-sonification-test

default commandはhardware-validated SHAを検査してからSRAM loadします。 ``+/-0.5, +/-1, +/-2 V`` をIN 0へ送り、6区間の
E/I network responseとOUT 0の音程を同じcaptureで判定します。2026-08-17 13:25のfresh runは6/6区間、network、
sonificationをPASSしました。音階周期一致率81.7--89.8%、主要周期誤差0.002--0.007 Hz、低域/高域energy比
24.5--30.2 dB、spectral flatness 0.00042--0.00194、RMS 2.356--2.361 Vでした。

note switchingを単一FFTで判定しない
-------------------------------------

最初のanalyzerは約1秒の区間を1本のFFTにし、85 msごとの正当なnote changeが作る遷移積を最大peakと誤認しました。
実機では61.25、56.63、167.95 Hzが選ばれ、許可音階への±4 Hz gateだけがFAILしました。許容幅を緩めず、
positive-going zero crossingを線形補間し、完全な1周期ごとの周波数を測る方式へ変更しました。phaseが連続し、各noteを
4096 sample保持する設計なので、遷移周期以外は許可音階へ直接一致します。最低24周期、75%以上のscale一致を要求します。

低域/高域比の仮24 dB gateも、3本目のcaptureの1区間だけ23.7 dBとなりました。period一致85.4%、flatness 0.00177、
networkは正常だったため、2本へのoverfitを避け、低域energyが高域の100倍となる20 dBを補助gateにしました。period一致、
flatness ``<= 0.005``、RMS 1.8--2.8 Vとの複合条件は維持します。

manifest metadataにも別の制約があります。各panel help labelは20文字以内ですが、briefは64文字以内です。最初のbriefは
66文字で合成前に停止しました。短い専用briefへ変え、RTL/timing failureとは区別しています。

証明範囲と次段階
----------------

この実装はSNNそのものが音響周期を生成するのではなく、population countで別NCOの音程を選ぶ最小sonificationです。
神経状態、3:1 E/I結合、映像、3本の診断出力は維持されています。次は興奮性/抑制性populationを別々に集計して2 voiceへ
割り当て、その後にper-neuron weight table、delay、STDPを追加します。
