#pragma once

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

template <typename DutT> class DVIDriver {
private:
    DutT* dut;

    uint32_t im_stride = 3;
    uint8_t *image_data = nullptr;
    uint32_t frames = 0;
    uint32_t x = 0;
    uint32_t y = 0;
    bool vsync_seen=false;
    uint64_t pixel_count = 0;
    uint64_t pixel_checksum = 1469598103934665603ULL;
    uint8_t channel_min[3] = {255, 255, 255};
    uint8_t channel_max[3] = {0, 0, 0};

public:
    explicit DVIDriver(DutT* dut) : dut(dut) {
        image_data = (uint8_t*)malloc(DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
        memset(image_data, 0, DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
    }

    ~DVIDriver() {
        free(image_data);
    }

    uint32_t get_frame_count() const { return frames; }
    uint64_t get_pixel_count() const { return pixel_count; }
    uint64_t get_pixel_checksum() const { return pixel_checksum; }
    uint8_t get_channel_min(uint8_t channel) const {
        return channel < 3 ? channel_min[channel] : 0;
    }
    uint8_t get_channel_max(uint8_t channel) const {
        return channel < 3 ? channel_max[channel] : 0;
    }

    void post_edge() {
        if (dut -> dvi_vsync) {
            vsync_seen = true;
        }
        if (dut->clk_dvi) {
            if (dut->dvi_de && vsync_seen) {
                ++x;
                if (x >= DVI_H_ACTIVE) {
                    x = 0;
                    ++y;
                }
                if (y >= DVI_V_ACTIVE) {
                    char name[64];
                    snprintf(name, sizeof(name), "frame%02d.bmp", frames);
                    printf("DVIDriver: %s\n", name);
                    stbi_write_bmp(name, DVI_H_ACTIVE, DVI_V_ACTIVE, 3, image_data);
                    ++frames;
                    y = 0;
                }
                const uint8_t pixel[3] = {
                    static_cast<uint8_t>(dut->dvi_r),
                    static_cast<uint8_t>(dut->dvi_g),
                    static_cast<uint8_t>(dut->dvi_b),
                };
                for (uint8_t channel = 0; channel < 3; ++channel) {
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + channel] = pixel[channel];
                    channel_min[channel] = pixel[channel] < channel_min[channel]
                        ? pixel[channel] : channel_min[channel];
                    channel_max[channel] = pixel[channel] > channel_max[channel]
                        ? pixel[channel] : channel_max[channel];
                    pixel_checksum ^= pixel[channel];
                    pixel_checksum *= 1099511628211ULL;
                }
                ++pixel_count;
            }
        }
    }
};
