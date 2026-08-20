// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0
//
// Verilator AV regression for the ADDITIVE-AV instrument: four DC CVs on the
// calibrated inputs, one host control frame injected on the byte port, four
// audio returns captured, DVI frames counted, and a frozen pass contract.

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include "dvi.h"
#include "i2s.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <vector>

#ifndef REGRESSION_NAME
#define REGRESSION_NAME "ADDITIVE-AV"
#endif

#ifndef METRICS_FILENAME
#define METRICS_FILENAME "additive-av-metrics.json"
#endif

struct AudioMetrics {
    size_t samples = 0;
    int16_t minimum = std::numeric_limits<int16_t>::max();
    int16_t maximum = std::numeric_limits<int16_t>::min();
    uint64_t nonzero = 0;
    uint64_t zero_crossings = 0;
    double mean_absolute = 0.0;
};

static AudioMetrics measure_audio(const std::vector<int16_t>& values) {
    AudioMetrics metrics;
    metrics.samples = values.size();
    int64_t absolute_sum = 0;
    int16_t previous = 0;
    bool have_previous = false;
    for (const int16_t value : values) {
        metrics.minimum = value < metrics.minimum ? value : metrics.minimum;
        metrics.maximum = value > metrics.maximum ? value : metrics.maximum;
        const int32_t widened = value;
        absolute_sum += widened < 0 ? -widened : widened;
        metrics.nonzero += value != 0;
        if (have_previous && ((previous < 0 && value >= 0) ||
                              (previous >= 0 && value < 0))) {
            ++metrics.zero_crossings;
        }
        previous = value;
        have_previous = true;
    }
    if (values.empty()) {
        metrics.minimum = 0;
        metrics.maximum = 0;
    } else {
        metrics.mean_absolute = static_cast<double>(absolute_sum) / values.size();
    }
    return metrics;
}

// CRC-16/CCITT-FALSE, matching tiliqua.additive.protocol.crc16_ccitt.
static uint16_t crc16_ccitt(const std::vector<uint8_t>& data) {
    uint16_t crc = 0xFFFF;
    for (const uint8_t byte : data) {
        crc ^= static_cast<uint16_t>(byte) << 8;
        for (int bit = 0; bit != 8; ++bit) {
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                                 : static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

static void push_u16(std::vector<uint8_t>& out, uint16_t value) {
    out.push_back(static_cast<uint8_t>(value & 0xFF));
    out.push_back(static_cast<uint8_t>(value >> 8));
}

// An independent encoder of the 45-byte control frame (OPEN FIFTH, master
// 0.60, output enabled, sub octave on) used to exercise the live link.
static std::vector<uint8_t> encode_test_frame(uint8_t sequence) {
    std::vector<uint8_t> payload;
    payload.push_back(36);      // root_midi C2
    payload.push_back(0);       // harmony OPEN FIFTH
    payload.push_back(0x03);    // flags: sub_octave | output_enabled
    payload.push_back(18);      // morph_seconds
    payload.push_back(20);      // evolution_rate_mhz
    payload.push_back(0);       // reserved
    push_u16(payload, 19660);   // master 0.60
    const uint16_t levels[10] = {32767, 17039, 11141, 7864, 5898, 4588, 3604, 2785, 2228, 1704};
    for (const uint16_t level : levels) push_u16(payload, level);
    push_u16(payload, 5000);    // detune_millicents
    push_u16(payload, 19005);   // phase_spread
    push_u16(payload, 20316);   // evolution_amount
    push_u16(payload, 26869);   // stereo_width
    push_u16(payload, 11796);   // sub_focus
    std::vector<uint8_t> body = {1, sequence, static_cast<uint8_t>(payload.size())};
    body.insert(body.end(), payload.begin(), payload.end());
    const uint16_t crc = crc16_ccitt(body);
    std::vector<uint8_t> frame = {0xA5, 0x5A};
    frame.insert(frame.end(), body.begin(), body.end());
    frame.push_back(static_cast<uint8_t>(crc >> 8));
    frame.push_back(static_cast<uint8_t>(crc & 0xFF));
    return frame;
}

int main(int argc, char** argv) {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Vtiliqua_soc top{&context};

    // Default 120 ms; ADDITIVE_SIM_MS overrides (used for offline audio dumps).
    uint64_t simulation_ms = 120;
    if (const char* ms_env = std::getenv("ADDITIVE_SIM_MS")) {
        simulation_ms = std::strtoull(ms_env, nullptr, 10);
        if (simulation_ms < 10) simulation_ms = 10;
    }
    const uint64_t simulation_time_ps = simulation_ms * 1'000'000'000ULL;
    const bool inject_frame = std::getenv("ADDITIVE_SIM_NO_FRAME") == nullptr;
    constexpr uint64_t ns_per_second = 1'000'000'000ULL;
    const uint64_t ns_per_sync_cycle = ns_per_second / SYNC_CLK_HZ;
    const uint64_t ns_per_dvi_cycle = ns_per_second / DVI_CLK_HZ;
    const uint64_t ns_per_audio_cycle = ns_per_second / AUDIO_CLK_HZ;

    context.timeInc(1);
    top.rst_sync = 1;
    top.rst_dvi = 1;
    top.rst_audio = 1;
    top.eval();
    context.timeInc(1);
    top.rst_sync = 0;
    top.rst_dvi = 0;
    top.rst_audio = 0;
    top.eval();

    I2SDriver<Vtiliqua_soc> i2s_driver(&top);
    DVIDriver<Vtiliqua_soc> dvi_driver(&top);
    // DC modulation through the inverting default input calibration of the
    // simulated codec path (about -2.2x): IN 0 about +0.9 V (master up),
    // IN 1 about +0.45 V (+0.9 st), IN 2 about -0.45 V (darker), IN 3 +0.2 V.
    for (int sample = 0; sample != 7000; ++sample) {
        i2s_driver.inject_sample(0, -1600);
        i2s_driver.inject_sample(1, -800);
        i2s_driver.inject_sample(2, 800);
        i2s_driver.inject_sample(3, -400);
    }

    const std::vector<uint8_t> frame = encode_test_frame(1);
    size_t frame_index = 0;
    uint64_t next_byte_ns = 30'000'000ULL;  // start the frame at 30 ms
    const uint64_t byte_gap_ns = 90'000ULL;   // ~115200 baud pacing
    bool observed_fault = false;
    uint32_t max_sample_cycles = 0;
    uint32_t max_block_cycles = 0;
    uint32_t master_before_frame = 0;
    uint32_t master_after_frame = 0;

    top.control_strobe = 0;
    while (context.time() < simulation_time_ps && !context.gotFinish()) {
        const uint64_t timestamp_ns = context.time() / 1000;
        if (timestamp_ns % (ns_per_dvi_cycle / 2) == 0) {
            top.clk_dvi = !top.clk_dvi;
            dvi_driver.post_edge();
        }
        bool sync_rising = false;
        if (timestamp_ns % (ns_per_sync_cycle / 2) == 0) {
            top.clk_sync = !top.clk_sync;
            sync_rising = top.clk_sync;
        }
        if (timestamp_ns % (ns_per_audio_cycle / 2) == 0) {
            top.clk_audio = !top.clk_audio;
            i2s_driver.post_edge();
        }
        // Inject one control byte per scheduled slot, held for one sync cycle.
        if (sync_rising) {
            if (top.control_strobe) {
                top.control_strobe = 0;
            } else if (inject_frame && frame_index < frame.size() && timestamp_ns >= next_byte_ns) {
                top.control_byte = frame[frame_index++];
                top.control_strobe = 1;
                next_byte_ns = timestamp_ns + byte_gap_ns;
            }
        }
        context.timeInc(1000);
        top.eval();

        observed_fault |= top.fault_debug != 0;
        if (top.sample_cycles_debug > max_sample_cycles) max_sample_cycles = top.sample_cycles_debug;
        if (top.block_cycles_debug > max_block_cycles) max_block_cycles = top.block_cycles_debug;
        if (timestamp_ns < 29'000'000ULL) master_before_frame = top.master_asq_debug;
        master_after_frame = top.master_asq_debug;
    }

    AudioMetrics audio[4];
    for (int channel = 0; channel != 4; ++channel) {
        audio[channel] = measure_audio(i2s_driver.get_captured_samples(channel));
    }
    if (const char* dump_path = std::getenv("ADDITIVE_SIM_DUMP")) {
        FILE* dump = std::fopen(dump_path, "w");
        if (dump != nullptr) {
            const size_t frames_captured = i2s_driver.get_captured_samples(0).size();
            for (size_t index = 0; index != frames_captured; ++index) {
                for (int channel = 0; channel != 4; ++channel) {
                    const auto& samples = i2s_driver.get_captured_samples(channel);
                    const float value = index < samples.size() ? samples[index] : 0.0f;
                    std::fwrite(&value, sizeof(float), 1, dump);
                }
            }
            std::fclose(dump);
            std::printf("audio dump: %s (%zu frames)\n", dump_path, frames_captured);
        }
    }

    bool passed = true;
    passed &= top.sample_index_debug > 4000;
    passed &= !observed_fault;
    passed &= max_sample_cycles > 0 && max_sample_cycles <= 1250;
    passed &= max_block_cycles > 0 && max_block_cycles < 160000;
    passed &= dvi_driver.get_frame_count() >= 5;
    passed &= dvi_driver.get_pixel_count() > 2'000'000;
    passed &= dvi_driver.get_channel_max(0) - dvi_driver.get_channel_min(0) >= 16;
    passed &= dvi_driver.get_channel_max(1) - dvi_driver.get_channel_min(1) >= 16;
    passed &= dvi_driver.get_channel_max(2) - dvi_driver.get_channel_min(2) >= 16;
    passed &= top.accepted_count_debug == (inject_frame ? 1 : 0);
    passed &= top.rejected_count_debug == 0;
    passed &= !inject_frame || top.link_alive_debug != 0;
    // IN 0 near +1 V smooths to roughly half scale (unipolar 2 V range);
    // IN 1 near +0.5 V to roughly +0.5 (bipolar 1 V range).
    passed &= top.cv0_smoothed_debug > 8000 && top.cv0_smoothed_debug < 26000;
    passed &= static_cast<int16_t>(top.cv1_smoothed_debug) > 4000
           && static_cast<int16_t>(top.cv1_smoothed_debug) < 26000;
    // The frame raises the master from 0.42 to 0.60 (plus the IN 0 offset).
    passed &= !inject_frame || master_after_frame > master_before_frame;
    passed &= top.master_asq_debug <= 8000;
    for (int channel = 0; channel != 4; ++channel) {
        passed &= audio[channel].samples > 3000;
        passed &= audio[channel].nonzero > 1000;
        passed &= audio[channel].minimum < -100;
        passed &= audio[channel].maximum > 100;
        // +/-2.0 V ceiling through the calibrated path (codec gain ~0.9).
        passed &= audio[channel].minimum >= -8000;
        passed &= audio[channel].maximum <= 8000;
        passed &= audio[channel].zero_crossings > 3;
    }

    FILE* metrics_file = std::fopen(METRICS_FILENAME, "w");
    if (metrics_file == nullptr) {
        std::perror(METRICS_FILENAME);
        return 3;
    }
    std::fprintf(metrics_file,
        "{\n"
        "  \"pass\": %s,\n"
        "  \"sample_index\": %u,\n"
        "  \"fault\": %s,\n"
        "  \"max_sample_cycles\": %u,\n"
        "  \"max_block_cycles\": %u,\n"
        "  \"control\": {\"accepted\": %u, \"rejected\": %u, \"link_alive\": %s, "
        "\"master_before_frame\": %u, \"master_after_frame\": %u},\n"
        "  \"cv_smoothed\": [%d, %d],\n"
        "  \"master_asq\": %u,\n"
        "  \"dvi\": {\"frames\": %u, \"pixels\": %llu, "
        "\"checksum\": \"%016llx\", \"r\": [%u, %u], "
        "\"g\": [%u, %u], \"b\": [%u, %u]},\n"
        "  \"audio\": [\n",
        passed ? "true" : "false",
        top.sample_index_debug,
        observed_fault ? "true" : "false",
        max_sample_cycles,
        max_block_cycles,
        top.accepted_count_debug,
        top.rejected_count_debug,
        top.link_alive_debug ? "true" : "false",
        master_before_frame,
        master_after_frame,
        static_cast<int16_t>(top.cv0_smoothed_debug),
        static_cast<int16_t>(top.cv1_smoothed_debug),
        top.master_asq_debug,
        dvi_driver.get_frame_count(),
        static_cast<unsigned long long>(dvi_driver.get_pixel_count()),
        static_cast<unsigned long long>(dvi_driver.get_pixel_checksum()),
        dvi_driver.get_channel_min(0), dvi_driver.get_channel_max(0),
        dvi_driver.get_channel_min(1), dvi_driver.get_channel_max(1),
        dvi_driver.get_channel_min(2), dvi_driver.get_channel_max(2));
    for (int channel = 0; channel != 4; ++channel) {
        std::fprintf(metrics_file,
            "    {\"channel\": %d, \"samples\": %zu, \"min\": %d, "
            "\"max\": %d, \"nonzero\": %llu, \"zero_crossings\": %llu, "
            "\"mean_abs\": %.3f}%s\n",
            channel,
            audio[channel].samples,
            audio[channel].minimum,
            audio[channel].maximum,
            static_cast<unsigned long long>(audio[channel].nonzero),
            static_cast<unsigned long long>(audio[channel].zero_crossings),
            audio[channel].mean_absolute,
            channel == 3 ? "" : ",");
    }
    std::fprintf(metrics_file, "  ]\n}\n");
    std::fclose(metrics_file);

    std::printf("%s regression: %s (metrics: %s)\n",
        REGRESSION_NAME, passed ? "PASS" : "FAIL", METRICS_FILENAME);
    return passed ? 0 : 2;
}
