Tutorial 17: E/I抑制強度をsweepして可変weight化の基準を作る
==============================================================

Tutorial 16では3:1 E/I ringの抑制結合を固定 ``-1024`` としました。次の可変weightや学習則へ進む前に、定数を
``512 / 1024 / 1536`` と変えたとき、32-lane memory実装が完全並列参照と一致し、audio/video出力が観測可能に
変化し、両端のR5 synthesisが閉じるかを調べます。

parameterと等価性
-----------------

``ParallelLIFBank`` と ``MemoryBatchedLIFBank`` に ``inhibitory_strength`` を追加しました。値は256--2048の
256刻みです。E/I sourceが発火したとき、後続neuronのcandidateからこの定数をsaturating subtractionします。
``top.py`` では ``--ei-ring`` と組み合わせた1024x32 profileだけが非default値を受け付けます。

unit regressionは512、1024、1536の各強度を8 sampleずつ完全並列/32-laneで比較します。4 DAC output、1024
spike bit、spike count、2-bitへ圧縮したdisplay levelがbit一致し、3 profile相互のtrajectoryが異なることも要求します。

.. code-block:: bash

    cd gateware
    pdm snn_lab quick

AV simulation sweep
-------------------

.. code-block:: bash

    pdm snn_lab inhibition-study

各強度を4-frame Verilator simulationへ通し、通常のaudio/DVI regressionをPASSした後、4 outputのmean absoluteと
DVI checksumを比較します。結果JSONはignored ``build/snn-inhibition-study.json`` に保存されます。

.. list-table::
   :header-rows: 1

   * - inhibition
     - DVI checksum
     - ch0 spike
     - ch1 activity
     - ch2 burst
     - ch3 membrane
   * - 512
     - ``b92bdf7ce58f0374``
     - 24,998.3
     - 17,682.7
     - 16,518.6
     - 6,867.6
   * - 1024
     - ``63562c9d861e7bb7``
     - 26,374.4
     - 17,318.6
     - 17,265.7
     - 6,874.4
   * - 1536
     - ``ecff60c4f347df5b``
     - 26,096.5
     - 17,324.8
     - 17,211.6
     - 6,873.5

3つのDVI checksumはすべて異なり、audio mean absoluteのspanはch0--3で1,376.1 / 364.1 / 747.0 / 6.8です。
512から1024でspike/burstは増え、1024から1536では少し戻りました。したがってこの自律networkでは
「抑制を強くすればactivityや音量が単調に下がる」と仮定できません。saturation、reset、ring phaseを含む集合力学として
sweepする必要があります。

R5 endpoint synthesis
----------------------

.. code-block:: bash

    pdm snn_lab --with-build inhibition-study
    pdm snn_lab inhibition-study-report

.. list-table::
   :header-rows: 1

   * - inhibition
     - LUT4 / FF / DP16KD / DSP
     - physical COMB / FF / DP16KD
     - sync / audio / dvi / dvi5x MHz
     - SHA-256
   * - 512
     - 4,775 / 5,247 / 18 / 1
     - 32% / 21% / 32%
     - 66.60 / 73.66 / 66.09 / 337.95
     - ``04e0da4d1299e1deb77f9ab95113ba95a50ae3bf4f289e6ee5d9f2541b4b4f9b``
   * - 1536
     - 4,795 / 5,247 / 18 / 1
     - 32% / 21% / 32%
     - 65.13 / 73.22 / 67.10 / 318.37
     - ``542a4ec64d6bf462eca8dbea2c6504fa2155d52efda58fe4473956723d066289``

両端ともTutorial 16のself-test resource/timing contractをPASSしました。定数幅の変更はLUT4を20個程度しか変えず、
18 DP16KDと5,247 FFは同じです。

default live profileの再検証境界
--------------------------------

parameter化後にdefault強度1024もself/liveを再buildしました。self-testはSHA
``362315f237191036c195419539b9283f47e97cc8d8c006feffaa634e151e06c5`` までTutorial 16と一致しました。一方、liveは
LUT4 5,806、FF 5,365、sync/audio/dvi/dvi5x 62.01/67.94/61.50/405.35 MHz、SHA
``46f6f88d18da195b50c419d136feb8d214e9ebf423d5d3ebe5653e92e5261b9a`` となり、2回buildして同じSHAを再現しました。

全clock/resource、AV metrics、並列参照等価はPASSしていますが、以前実機でPASSしたlive SHA ``60d57b...`` とは異なります。
bitstream生成や機能simulationを実機PASSへ読み替えず、現行live SHAは次の権限sessionで固定ES-9のlive/control/tone試験を
再実行するまでsynthesis-onlyとします。

証明範囲と次段階
----------------

3強度のmodel equivalenceとAV simulation、512/1536 endpointのR5配置配線まで証明しました。権限session終了後に
実行したため、endpoint bitstreamはSRAMへloadしておらず実機PASSではありません。実機で可変weightに進む前に、次のsessionで
endpointの固定ES-9 self-testを行います。その後、定数をCV制御またはper-neuron block-memory weightへ移し、weight更新と
audio/video観測を同じclosure loopで追加します。
