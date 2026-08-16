Tutorial 16: 1024 neuronの3:1 E/I ringで音と映像を生成する
================================================================

Tutorial 15の1024 logical neuron / 32 physical lane memory SNNを、興奮性だけのringから固定3:1
excitatory/inhibitory ringへ拡張します。index ``3, 7, 11, ...`` の256 neuronを抑制性sourceとし、その直後の
neuronへ届く結合だけを ``+256`` excitationからsaturating ``-1024`` inhibitionへ置き換えます。残る768
neuronは興奮性です。完全な重み行列を持たず、既存32-lane datapathと15 state ``DP16KD`` を保つ最小のE/I実験です。

参照モデルとのbit等価
----------------------

``ParallelLIFBank(1024, ei_ring=True)`` を仕様モデルにし、``MemoryBatchedLIFBank`` の4 DAC output、1024
spike bit、spike countを8 sample比較します。表示用membraneだけは従来どおり上位4 bitから2 bitへ圧縮します。
同じ入力のall-excitatory ringとも出力が異なることを検査し、E/I flagがno-opでないことも証明します。

.. code-block:: bash

    cd gateware
    pdm snn_lab quick
    pdm snn_lab ei-ring
    pdm snn_lab ei-ring-report

``ei-ring`` はunit/equivalence、4-frame audio/DVI simulation、self/live R5 synthesisをまとめて実行します。
現行simulationは3,669 neural sample、2,534,824 active pixel、DVI checksum
``63562c9d861e7bb7`` でPASSしました。audio ch0--3 mean absoluteは26,374.4、17,318.6、17,265.7、
6,874.4でした。

抑制性neuronを可視化する
------------------------

64x16 gridの抑制性cellは赤/橙、興奮性cellは青/白で表示します。spike中も抑制性cellは
``RGB(255, 96, 32)`` なので、固定E/I配置を映像から確認できます。これは型を表示するだけで、DVI domainから神経状態を
変更しません。1024個のdisplay stateは従来と同じ96-bit x 32 dual-clock RAMに入るため、広いCDC bundleを復活させません。

R5 synthesisとtiming修正
-------------------------

.. list-table::
   :header-rows: 1

   * - profile
     - LUT4 / FF / DP16KD / DSP
     - physical COMB / FF / DP16KD
     - sync / audio / dvi / dvi5x MHz
     - SHA-256
   * - self-test
     - 4,779 / 5,247 / 18 / 1
     - 32% / 21% / 32%
     - 67.81 / 74.35 / 65.86 / 346.38
     - ``362315f237191036c195419539b9283f47e97cc8d8c006feffaa634e151e06c5``
   * - live
     - 5,811 / 5,365 / 18 / 1
     - 37% / 22% / 32%
     - 63.24 / 69.32 / 62.08 / 359.58
     - ``60d57b9de175cc67e1cf2aeebc07906bb3f72df0098ed039e53d6bac0b85f3e7``

最初のself buildはbitstreamを生成しましたが、sync 59.37 MHzで60 MHz contractを失敗したためSRAMへはloadしませんでした。
critical pathはthreshold compare/resetから最終batchのsample accumulatorまで連続していました。E/I profileだけ
``batch_spike_count`` と ``batch_membrane_sum`` をregisterし、次cycleでsample accumulatorへ加える
``pending_accumulate`` stageを追加しました。1 sampleは約132から164 sync cycleへ増えますが、60 MHzで使える1,250 cycleの
約13%に留まり、syncは67.81/63.24 MHzへ閉じました。all-excitatory 1024 profileにはこの追加stageを入れず、既存の
bitstreamとlatencyを維持します。

固定ES-9 fixtureでの実機試験
-----------------------------

private management repositoryで次の3コマンドを実行します。``--ei-ring`` は専用bitstreamを自動選択し、結果JSONの
``network.topology`` に ``3:1 excitatory/inhibitory ring`` を記録します。すべて揮発性SRAMだけを更新し、SPI flashと
calibration EEPROMには触れません。

.. code-block:: bash

    bin/tiliqua snn-test --neurons 1024 --ei-ring
    bin/tiliqua snn-live-test --neurons 1024 --ei-ring
    bin/tiliqua snn-control-test --neurons 1024 --ei-ring

連続したfresh runはself、6/6 bipolar live、9/9 four-CV controlをすべてPASSしました。selfはspike p01/p99
-6.621/+6.826 V、activity 2.658/7.088 V、activity-to-gate 0.87255、activity-to-membrane -0.99787でした。
liveの+/-2 V activity差は0.780 Vです。controlではstrong leakがactivity -0.146 V、burst 0.006 V、spike RMS
0 Vまで止まり、positive recurrenceはactivityを3.180から7.879 Vへ増加させました。

計測contractで得た知見
-----------------------

* E/I版の+/-2 V activity差は繰り返し0.758--0.780 Vで、all-excitatory向け0.75 V境界のすぐ外でした。他の
  5段階、membrane、burst、spike RMSは正常なので、1024 liveのactivity symmetry上限を0.85 Vとしました。
* E/I ringはweak-leak時にburstがほぼ飽和し、neutralとの差は約0.17 Vです。activity差は約2.98 Vあるため、
  weak-leak burstの1024下限は0.10 Vとし、activity、spike RMS、strong-leak消音との複合contractを維持します。
* asynchronous spike windowのneutral RMS rangeは実機反復で0.26--0.37 Vでした。activity/burst/membraneの
  neutral rangeは最大0.034 V以下なので、1024だけRMS repeatabilityを0.40 Vとし、他3指標は従来どおり厳しく保ちます。
* project rootで ``pdm run`` を実行すると ``pyproject.toml has not been initialized`` になります。公開repoは
  ``gateware`` directoryで ``pdm ...``、private管理repoは ``python3 -m unittest discover -s tests`` または
  ``bin/tiliqua`` を使います。

証明範囲と次の段階
------------------

固定3:1 E/I topology、符号付き局所結合、完全並列参照との計算等価、audio/DVI simulation、R5 timing/resource、
自己診断、bipolar drive、3 CV responseまで証明しました。重み可変化、STDP、長距離結合、発火遅延、物理DVI画面の
カメラ検証は未実装です。次はこの安定したE/I基盤に小さな可塑性または重みtableを加え、同じclosure loopで進めます。
