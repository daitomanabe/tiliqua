// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include "dvi.h"
#include "i2s.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <vector>

#ifndef REGRESSION_NAME
#define REGRESSION_NAME "SNN2-AV"
#endif

#ifndef METRICS_FILENAME
#define METRICS_FILENAME "snn2-av-metrics.json"
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

int main(int argc, char** argv) {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Vtiliqua_soc top{&context};

    constexpr uint64_t simulation_time_ps = 100'000'000'000ULL;
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
    for (int sample = 0; sample != 6000; ++sample) {
        i2s_driver.inject_sample(0, static_cast<int16_t>(
            8000.0 * std::sin(2.0 * 3.141592653589793 * 640.0 * sample / 48000.0)));
        i2s_driver.inject_sample(1, 0);
        i2s_driver.inject_sample(2, 0);
        i2s_driver.inject_sample(3, 0);
    }

    uint32_t maximum_excitatory = 0;
    uint32_t maximum_inhibitory = 0;
    uint32_t maximum_scheduler = 0;
    uint32_t band_activity_mask = 0;
    bool observed_fault = false;

    while (context.time() < simulation_time_ps && !context.gotFinish()) {
        const uint64_t timestamp_ns = context.time() / 1000;
        if (timestamp_ns % (ns_per_dvi_cycle / 2) == 0) {
            top.clk_dvi = !top.clk_dvi;
            dvi_driver.post_edge();
        }
        if (timestamp_ns % (ns_per_sync_cycle / 2) == 0) {
            top.clk_sync = !top.clk_sync;
        }
        if (timestamp_ns % (ns_per_audio_cycle / 2) == 0) {
            top.clk_audio = !top.clk_audio;
            i2s_driver.post_edge();
        }
        context.timeInc(1000);
        top.eval();

        maximum_excitatory = top.excitatory_count_debug > maximum_excitatory
            ? top.excitatory_count_debug : maximum_excitatory;
        maximum_inhibitory = top.inhibitory_count_debug > maximum_inhibitory
            ? top.inhibitory_count_debug : maximum_inhibitory;
        maximum_scheduler = top.scheduler_cycles_debug > maximum_scheduler
            ? top.scheduler_cycles_debug : maximum_scheduler;
        observed_fault |= top.snn2_fault != 0;
        const uint64_t bands = top.band_levels_debug;
        for (unsigned band = 0; band != 8; ++band) {
            if (((bands >> (band * 8)) & 0xffU) != 0) {
                band_activity_mask |= 1U << band;
            }
        }
    }

    AudioMetrics audio[4];
    for (int channel = 0; channel != 4; ++channel) {
        audio[channel] = measure_audio(i2s_driver.get_captured_samples(channel));
    }

    bool passed = top.self_test_active;
    passed &= top.test_sample_index > 3000;
    passed &= dvi_driver.get_frame_count() >= 4;
    passed &= dvi_driver.get_pixel_count() > 1'500'000;
    passed &= dvi_driver.get_channel_max(0) - dvi_driver.get_channel_min(0) >= 16;
    passed &= dvi_driver.get_channel_max(1) - dvi_driver.get_channel_min(1) >= 16;
    passed &= dvi_driver.get_channel_max(2) - dvi_driver.get_channel_min(2) >= 16;
    passed &= !observed_fault;
    passed &= maximum_scheduler > 0 && maximum_scheduler <= 640;
    passed &= maximum_excitatory > 0 && maximum_inhibitory > 0;
    passed &= band_activity_mask == 0xff;
    const bool performance = top.performance_output_active != 0;
    if (performance) {
        for (int channel = 0; channel != 2; ++channel) {
            passed &= audio[channel].samples > 3000;
            passed &= audio[channel].minimum < -4000;
            passed &= audio[channel].maximum > 4000;
            passed &= audio[channel].minimum >= -17000;
            passed &= audio[channel].maximum <= 17000;
            passed &= audio[channel].zero_crossings > 10;
        }
        passed &= audio[2].minimum >= 0;
        passed &= audio[2].maximum <= 6000;
        passed &= audio[2].nonzero > 1000;
        passed &= audio[3].minimum >= 0;
        // The calibrated I2S model applies the codec path's approximately
        // 0.9 gain, so an internal 20,000-count (+5 V) gate captures near
        // 18,000 counts here.
        passed &= audio[3].maximum >= 17000;
        passed &= audio[3].maximum <= 20000;
        passed &= audio[3].nonzero > 500;
    } else {
        passed &= audio[0].samples > 3000;
        passed &= audio[0].minimum < -100 && audio[0].maximum > 100;
        passed &= audio[1].minimum >= 0 && audio[1].maximum > 1000;
        passed &= audio[2].minimum >= 0 && audio[2].maximum > 50;
        passed &= audio[3].minimum >= -20000 && audio[3].maximum <= 20000;
        passed &= audio[3].nonzero > 1000;
    }

    FILE* metrics_file = std::fopen(METRICS_FILENAME, "w");
    if (metrics_file == nullptr) {
        std::perror(METRICS_FILENAME);
        return 3;
    }
    std::fprintf(metrics_file,
        "{\n"
        "  \"pass\": %s,\n"
        "  \"self_test\": %s,\n"
        "  \"performance\": %s,\n"
        "  \"test_sample_index\": %u,\n"
        "  \"test_phase\": %u,\n"
        "  \"network\": {\"fault\": %s, \"maximum_scheduler_cycles\": %u, "
        "\"maximum_excitatory_spikes\": %u, \"maximum_inhibitory_spikes\": %u, "
        "\"band_activity_mask\": %u},\n"
        "  \"dvi\": {\"frames\": %u, \"pixels\": %llu, "
        "\"checksum\": \"%016llx\", \"r\": [%u, %u], "
        "\"g\": [%u, %u], \"b\": [%u, %u]},\n"
        "  \"audio\": [\n",
        passed ? "true" : "false",
        top.self_test_active ? "true" : "false",
        performance ? "true" : "false",
        top.test_sample_index,
        top.test_phase,
        observed_fault ? "true" : "false",
        maximum_scheduler,
        maximum_excitatory,
        maximum_inhibitory,
        band_activity_mask,
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
