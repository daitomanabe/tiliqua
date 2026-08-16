Tutorial 18: 1024 E/I networkのpopulation balanceを分離して測る
=================================================================

Tutorial 16--17では3:1 E/I ringの4 audio monitor、DVI、抑制強度sweepを検査しました。次に可塑性や
per-neuron weightへ進む前に、768 excitatory neuronと256 inhibitory neuronがそれぞれ発火しているかを分離して測ります。
この段階ではRTLやbitstreamを変更せず、既存 ``spike_vector`` をoffline simulationで分類します。

実行方法
--------

``gateware`` directoryで実行します。

.. code-block:: bash

   pdm snn_lab population-study

各抑制強度512、1024、1536について、neutral controlでdrive 3000を192 sample、続けて12000を192 sample与えます。
各区間の先頭64 sampleをsettlingとして捨て、残る128 sampleからpopulation別の平均spike数を求めます。全384 sampleで
``excitatory + inhibitory == spike_count`` を検査するため、分類漏れや二重countも同時に検出します。ignored resultは
``build/snn-population-study.json`` です。

3:1なのでraw countを比較しない
------------------------------

興奮性は768個、抑制性は256個なので、raw spike数なら同じper-neuron activityでも興奮性が約3倍になります。表はそれぞれを
population sizeで割った1-neuron / 1-sample当たりのrateです。

同じcommandを独立に2回実行したresult JSONはともにSHA-256
``1e06f3435b0a9e1858d2359377f2acea2008528d40c19a709f513339742bb30c`` で、初期state、stimulus、分類結果が
byte-identicalであることも確認しました。

.. list-table:: deterministic RTL simulation result
   :header-rows: 1

   * - inhibition
     - drive
     - excitatory rate
     - inhibitory rate
     - inhibitory / excitatory
   * - 512
     - low / high
     - 0.03079 / 0.08575
     - 0.03250 / 0.08365
     - 1.05550 / 0.97544
   * - 1024
     - low / high
     - 0.02995 / 0.08354
     - 0.03223 / 0.08420
     - 1.07609 / 1.00792
   * - 1536
     - low / high
     - 0.02901 / 0.08111
     - 0.03189 / 0.08319
     - 1.09923 / 1.02571

3強度とも両populationが発火し、高driveで両rateが増えました。抑制を強めると低/高driveの興奮性rateはともに単調低下し、
正規化したinhibitory/excitatory比は単調上昇しました。この固定窓のpopulation rateと、Tutorial 17の長いself-test AV出力が
非単調だったことは矛盾しません。後者はdrive phase、膜電位、reset、非線形DAC monitorを含む別の集合観測です。

「抑制性spike」の意味
---------------------

このmodelでspike自体は全neuronとも0/1です。抑制性neuronが負のspike値を生成するのではなく、index ``3 mod 4`` のneuronが
発火したとき、その次のneuronへ届く結合だけが負になります。したがってpopulationのspike rate比は発火参加率であり、
興奮/抑制synaptic currentの比ではありません。weight magnitude、target membrane、saturationを含めたcurrent balanceには
別のinstrumentationが必要です。

証明範囲と次段階
----------------

このstudyは ``MemoryBatchedLIFBank`` のdeterministic RTL simulationだけを証明し、合成logic、DAC、ES-9、実機のpopulation別
観測を追加しません。次のplasticity実験では、同じ正規化rateとtotal-count identityを基準にし、weight updateを入れる前後で
population silence、runaway excitation、分類破綻を検出します。実機へpopulation balanceを出す場合は、既存4 DACの意味を
壊さずDVI overlayまたは明示的debug profileを追加し、別bitstreamとしてtimingとSHAを閉じます。
