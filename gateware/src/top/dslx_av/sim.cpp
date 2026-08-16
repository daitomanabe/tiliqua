// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

// End-to-end audio/video regression harness for DSLX-family tops. It emits human-
// viewable frames and a machine-readable metrics file, then returns non-zero
// if the deterministic self-test falls outside broad electrical invariants.

#ifndef REGRESSION_NAME
#define REGRESSION_NAME "DSLX-AV"
#endif

#ifndef METRICS_FILENAME
#define METRICS_FILENAME "dslx-av-metrics.json"
#endif

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include "dvi.h"
#include "i2s.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <vector>

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

    if (!values.empty()) {
        metrics.mean_absolute = static_cast<double>(absolute_sum) / values.size();
    } else {
        metrics.minimum = 0;
        metrics.maximum = 0;
    }
    return metrics;
}

static int greatest_common_divisor(int a, int b) {
    while (b != 0) {
        const int remainder = a % b;
        a = b;
        b = remainder;
    }
    return a;
}

int main(int argc, char** argv) {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Vtiliqua_soc top{&context};

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    Verilated::traceEverOn(true);
    VerilatedFstC trace;
    top.trace(&trace, 99);
    trace.open("simx.fst");
#endif

    constexpr uint64_t simulation_time_ps = 75'000'000'000ULL;
    constexpr uint64_t ns_per_second = 1'000'000'000ULL;
    const uint64_t ns_per_sync_cycle = ns_per_second / SYNC_CLK_HZ;
    const uint64_t ns_per_dvi_cycle = ns_per_second / DVI_CLK_HZ;
    const uint64_t ns_per_audio_cycle = ns_per_second / AUDIO_CLK_HZ;
    const uint64_t clock_gcd = greatest_common_divisor(SYNC_CLK_HZ, DVI_CLK_HZ);

    std::printf("sync=%d KHz, dvi=%d KHz, audio=%d KHz, gcd=%llu KHz\n",
        SYNC_CLK_HZ / 1000,
        DVI_CLK_HZ / 1000,
        AUDIO_CLK_HZ / 1000,
        static_cast<unsigned long long>(clock_gcd / 1000));

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

    // Live-input simulation uses a known tone. In self-test mode these samples
    // are deliberately ignored by the DUT, proving the internal source works.
    for (int sample = 0; sample != 5000; ++sample) {
        i2s_driver.inject_sample(0, static_cast<int16_t>(
            10000.0 * std::cos(static_cast<double>(sample) / 300.0)));
        i2s_driver.inject_sample(1, 4096);
        i2s_driver.inject_sample(2, 0);
        i2s_driver.inject_sample(3, 0);
    }

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
#if defined VM_TRACE_FST && VM_TRACE_FST == 1
        trace.dump(context.time());
#endif
    }

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    trace.close();
#endif

    AudioMetrics audio[4];
    for (int channel = 0; channel != 4; ++channel) {
        audio[channel] = measure_audio(i2s_driver.get_captured_samples(channel));
    }

    bool passed = dvi_driver.get_frame_count() >= 3;
    passed &= dvi_driver.get_pixel_count() > 1'000'000;
    passed &= dvi_driver.get_channel_max(0) > dvi_driver.get_channel_min(0);
    passed &= dvi_driver.get_channel_max(1) > dvi_driver.get_channel_min(1);
    passed &= dvi_driver.get_channel_max(2) > dvi_driver.get_channel_min(2);

    if (top.self_test_active) {
        passed &= top.test_sample_index > 2000;
        passed &= audio[0].samples > 1000;
        passed &= audio[0].minimum < -8000 && audio[0].maximum > 8000;
        passed &= audio[1].maximum > 3000;
        passed &= audio[2].minimum < 1000 && audio[2].maximum > 15000;
        passed &= audio[3].maximum > 8000;
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
        "  \"test_sample_index\": %u,\n"
        "  \"test_phase\": %u,\n"
        "  \"dvi\": {\"frames\": %u, \"pixels\": %llu, "
        "\"checksum\": \"%016llx\", \"r\": [%u, %u], "
        "\"g\": [%u, %u], \"b\": [%u, %u]},\n"
        "  \"audio\": [\n",
        passed ? "true" : "false",
        top.self_test_active ? "true" : "false",
        top.test_sample_index,
        top.test_phase,
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
