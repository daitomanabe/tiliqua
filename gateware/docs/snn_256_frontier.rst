Tutorial 11: 256完全並列SNNでR5の境界を測る
================================================

128ニューロン版と同じinteger LIFを256個へ増やし、各48 kHz audio sampleで全ニューロンを
同時更新します。論理上の処理量は ``256 * 48000 = 12.288 M`` neuron update/sです。
このTutorialの目的はbitstreamを無理に作ることではなく、機能simulationとFPGA実装可能性を
分けて測り、次のarchitectureを数値から選ぶことです。

32x8 simulationは成立する
---------------------------

``ParallelLIFBank``、top、frame-buffer-free visualizerを256までparameter化しました。
population集計は8ニューロン単位のregisterを使うため、32 groupの合計も厳密です。
Verilatorでは32x8の全256セル、4 audio output、DVI snapshotが成立しました。

.. figure:: /_static/snn_av_256_selftest.png
   :width: 560px

   256ニューロン完全並列版のVerilator最終frame。白はsnapshot時のspikeです。

2026-08-17のsimulation contractは次の通りです。

.. list-table::
   :header-rows: 1

   * - 項目
     - 結果
   * - unit test
     - 6 PASS（256-way grouped reductionとfrontier parserを含む）
   * - DVI
     - 4 frames / 2,534,824 active pixels / checksum ``489a1b5c3d6835e3``
   * - OUT 0 simulation
     - -29,494 .. +29,490 count
   * - OUT 1 / OUT 2 / OUT 3 simulation maximum
     - 24,882 / 17,998 / 10,482 count
   * - simulation判定
     - PASS

合成と配置配線は別の契約
--------------------------

同じdesignをR5のLFE5U-25Fへ合成すると、Yosysまでは正常に完了します。しかしnextpnrの
packing後の物理resourceは次の値になり、legal placementを作れませんでした。

.. list-table::
   :header-rows: 1

   * - resource
     - 使用 / 搭載
     - 使用率
   * - LUT4（Yosys論理セル）
     - 18,412
     - 参考値
   * - TRELLIS_FF
     - 12,342 / 24,288
     - 50%
   * - TRELLIS_COMB
     - 35,628 / 24,288
     - 146%
   * - MULT18X18D
     - 1 / 28
     - 3%

停止理由は ``Unable to find legal placement for all cells, design is probably at
utilisation limit`` です。LUT4だけを見ると収まりそうに見えますが、carry、wide mux、packingに必要な
``TRELLIS_COMB`` を見ると物理容量の146%です。これはrouting seedや長時間探索で直るtiming問題ではなく、
architectureを変える必要がある容量超過です。

期待される失敗をcontractにする
-------------------------------

``gateware`` directoryで次を実行すると、unit test、256 simulation、R5 buildを順に実行します。

.. code-block:: bash

    pdm snn_lab frontier

buildが非zero終了しただけではPASSにしません。runnerは次をすべて検査します。

#. 256-neuron audio/DVI simulation contractがPASSする
#. nextpnr reportの ``TRELLIS_COMB`` が搭載量を超える
#. characterizedしたlegal-placement errorが存在する
#. ``top.bit`` が生成されていない

既存の結果だけを再判定する場合は ``pdm snn_lab frontier-report`` を使います。将来の最適化で
256完全並列が収まった場合、このnegative contractは意図的にFAILし、通常のtiming/resource contractへ
置き換える必要があることを知らせます。

なぜSRAMへloadしないか
-----------------------

このprobeは ``top.bit`` を生成していないため、実機SRAMへloadする対象がありません。古い同名bitstreamを
誤用しないようrunnerもbitstream不在を契約に含めます。SPI flash、calibration EEPROM、ES-9設定には一切
触れません。

次のarchitecture
-----------------

R5で256以上の状態数を得るには、ニューロン数と物理演算lane数を分離します。次は128 physical laneを
2 batchとして使い、256 logical neuronをaudio sample間の複数sync cycleで更新します。60 MHzでは
1 audio sampleあたり約1,250 cycleあるため、2 batchの計算latencyは十分小さい一方、比較器・加算器を
複製せず再利用できます。その後は状態をBRAM/PSRAMへ置き、疎結合行列や複数populationへ拡張します。

この境界試験が証明したのは「256モデルが動かない」ことではありません。256個を同一cycleに物理複製する
方式がR5の組合せlogic容量を超えることです。simulationで成立したモデルを保ったまま、実装方式だけを
time-multiplexへ移すのが次の段階です。
