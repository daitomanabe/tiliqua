Tiliqua Project
###############

The **Tiliqua Project** aims to make FPGA-based audio and video synthesis more accessible.

.. figure:: /_static/xbeam_system_side.jpg

    TLQ-MODULE and (optional) matching TLQ-SCREEN patched in a Eurorack System

Tiliqua is a **Eurorack Module**, which contains a debugger, FPGA and high-fidelity DC-coupled audio IOs, amongst lots of other features. It can store multiple bitstreams, and has a bootloader to select between them - making the hardware fully reconfigurable during live performance.

.. note::

    **If you just received Tiliqua hardware -- Quickstart is where you should begin!**

.. toctree::
   :caption: Quickstart
   :maxdepth: 2

   quickstart/tlq_module.rst
   quickstart/tlq_screen.rst
   quickstart/tlq_expander.rst
   quickstart/tlq_soldiercrab.rst

.. toctree::
   :caption: Development Guide
   :maxdepth: 3

   install
   building_flashing
   audio_bitstreams
   custom_dsp
   dslx_basic_av
   dslx_synth_lab
   dslx_synth_voice
   dslx_playable_voice
   dslx_live_synth
   dslx_synth_hardware
   dslx_synth_live_hardware
   snn_av_hardware
   snn_live_hardware
   snn_control_hardware
   snn_128_hardware
   snn_128_live_hardware
   snn_256_frontier
   snn_256_batched_hardware
   snn_512_memory_hardware
   snn_512_live_hardware
   snn_1024_memory_hardware
   snn_1024_ei_hardware
   snn_inhibition_strength_study
   snn_population_balance_study
   snn_sonification_hardware
   fpga_dsp_library_comparison
   beamrace_video
   cpu_bitstreams
   pmods
   bootloader

.. toctree::
   :caption: Reference
   :maxdepth: 3

   examples/index
   dsp/index
   calibration
   usb_troubleshooting

.. toctree::
   :caption: Hardware Details
   :maxdepth: 2

   hardware_design
   hardware_changes


.. toctree::
   :caption: Keep Updated
   :maxdepth: 2

   community
   foss_funding
   devlog/index

.. toctree::
   :caption: Links
   :maxdepth: 2

   Tiliqua Webflasher <https://apfaudio.github.io/tiliqua-webflash/>
   Tiliqua on Crowd Supply <https://www.crowdsupply.com/apfaudio/tiliqua>
   Tiliqua on GitHub <https://github.com/apfaudio/tiliqua>
   Homepage (apf.audio) <https://apf.audio/>
