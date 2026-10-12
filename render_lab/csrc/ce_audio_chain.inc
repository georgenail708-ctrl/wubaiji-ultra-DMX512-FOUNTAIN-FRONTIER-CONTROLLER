/* R211 four ordered, pre-filter AudioMod Clip processors (float32 exact). */
static double ca_slice_factor(const double *row, const double *controls)
{
    double phase = _ce_mod_div(ca_unit(controls[6]) * row[4] + row[6], 1.0);
    double gate = 0.0, edge = row[8] < row[5] * .5 ? row[8] : row[5] * .5;
    if (phase < row[5]) {
        gate = 1.0;
        if (edge > 0.0) {
            double a = phase / edge, b = (row[5] - phase) / edge;
            if (a < gate) gate = a;
            if (b < gate) gate = b;
        }
    }
    double control = ca_unit(controls[(int)row[3]]);
    return row[7] + (1.0 - row[7]) * gate * control;
}

static int ca_levels(const float *input, float *out, const uint8_t *active,
                      const uint8_t *targets, double scale)
{
    if (scale >= 1.0) return 0;
    int changed = 0;
    float gain = (float)scale;
    for (int c = 0; c < 512; ++c) {
        if (active[c] && targets[c]) {
            float value = input[c] * gain;
            if (value != input[c]) changed = 1;
            out[c] = value;
        }
    }
    return changed;
}

static int ca_motion_axis(const float *input, float *out, const double *axis,
                           const uint8_t *active, double scale)
{
    int coarse = (int)axis[0], fine = (int)axis[1];
    if (coarse < 0 || coarse >= 512 || fine >= 512) return -1;
    if (!active[coarse] || (fine >= 0 && !active[fine])) return 0;
    /* Python int truncates the finite f32 input without a 32-bit narrowing.
       Multiplication by 256 is exact; round the integer sum to f64 once. */
    double integer_coarse = trunc((double)input[coarse]);
    double logical = fine < 0 ? integer_coarse
                   : (integer_coarse * 256.0 + trunc((double)input[fine])) / 256.0;
    double value = axis[2] + (logical - axis[2]) * scale;
    if (fabs(value - logical) < 1e-12) return 0;
    float a, b = 0.0f;
    if (fine < 0) {
        double clamped = value < 255.0 ? value : 255.0;
        if (clamped < 0.0) clamped = 0.0;
        a = (float)_ce_py_round_even(clamped);
    } else {
        uint8_t ac, af;
        ce_split_16bit(value, &ac, &af);
        a = (float)ac; b = (float)af;
    }
    int changed = a != input[coarse] || (fine >= 0 && b != input[fine]);
    if (changed) { out[coarse] = a; if (fine >= 0) out[fine] = b; }
    return changed;
}

static int ca_color_group(const float *input, float *out, const int64_t *rgb,
                           const uint8_t *active, double offset, double wet)
{
    float source[3];
    for (int i = 0; i < 3; ++i) {
        if (rgb[i] < 0 || rgb[i] >= 512) return -1;
        if (!active[rgb[i]]) return 0;
        source[i] = input[rgb[i]];
    }
    float peak = source[0];
    if (source[1] > peak) peak = source[1];
    if (source[2] > peak) peak = source[2];
    if (peak <= 0.0f) return 0;
    float hsv[3], shifted[3], mixed[3];
    ca_rgb_hsv_f32(source, hsv);
    hsv[0] = ca_mod_f32(hsv[0] + (float)offset);
    ca_hsv_rgb_f32(hsv, shifted);
    float dry = (float)(1.0 - wet), gain = (float)wet;
    for (int i = 0; i < 3; ++i) {
        float a = source[i] * dry;
        float b = shifted[i] * 255.0f;
        b = b * gain;
        mixed[i] = a + b;
    }
    float maximum = mixed[0];
    if (mixed[1] > maximum) maximum = mixed[1];
    if (mixed[2] > maximum) maximum = mixed[2];
    float ratio = (float)((double)peak / (double)maximum);
    int changed = 0;
    for (int i = 0; i < 3; ++i) {
        mixed[i] = mixed[i] * ratio;
        if (mixed[i] != source[i]) changed = 1;
    }
    if (changed) for (int i = 0; i < 3; ++i) out[rgb[i]] = mixed[i];
    return changed;
}

static int ca_processor(const int64_t *iv, const double *controls, void **p,
                         int64_t index, float *input, float *out)
{
    const double *row = (const double *)p[2] + index * 14;
    const uint8_t *active = (const uint8_t *)p[1];
    const uint8_t *targets = (const uint8_t *)p[3] + index * 1024;
    if (!row[1] || row[2] <= 0.0) return 0;
    int kind = (int)row[0], changed = 0;
    double factor, scale;
    if ((int)row[3] < 0 || (int)row[3] >= 6) return -2;
    double control = ca_unit(controls[(int)row[3]]);
    if (kind == 0 || kind == 1) {
        factor = kind == 0 ? ca_slice_factor(row, controls) : _ce_pow_ucrt(control, row[9]);
        if (kind == 1) {
            if (row[10]) factor = 1.0 - factor;
            factor = row[7] + (1.0 - row[7]) * factor;
        }
        scale = 1.0 - row[2] + row[2] * factor;
        changed = ca_levels(input, out, active, targets, scale);
        if (kind == 0 && factor < .5 && row[2] >= .5) {
            for (int c = 0; c < 512; ++c)
                if (active[c] && targets[512 + c] && input[c] != 0.0f) {
                    out[c] = 0.0f; changed = 1;
                }
        }
    } else if (kind == 2) {
        factor = row[11] + (row[12] - row[11]) * control;
        scale = 1.0 - row[2] + row[2] * factor;
        if (scale >= 1.0) return 0;
        for (int64_t a = 0; a < iv[1]; ++a)
            if (((const uint8_t *)p[5])[index * iv[1] + a]) {
                int result = ca_motion_axis(input, out, (const double *)p[4] + a * 3, active, scale);
                if (result < 0) return result;
                changed |= result;
            }
    } else if (kind == 3) {
        double offset = row[13] / 360.0 * control;
        if (fabs(offset) < 1e-12) return 0;
        for (int64_t a = 0; a < iv[2]; ++a)
            if (((const uint8_t *)p[7])[index * iv[2] + a]) {
                int result = ca_color_group(input, out, (const int64_t *)p[6] + a * 3,
                                             active, offset, row[2]);
                if (result < 0) return result;
                changed |= result;
            }
    } else return -3;
    return changed;
}

CE_API int ce_audio_chain(const int64_t *iv, const double *controls, void **p)
{
    if (!iv || !controls || !p || iv[0] < 0 || iv[0] > 32 || iv[1] < 0 || iv[2] < 0)
        return -1;
    float *out = (float *)p[8], input[512];
    const float *source = (const float *)p[0];
    const uint8_t *active = (const uint8_t *)p[1];
    double *report = (double *)p[9];
    memset(report, 0, 4 * sizeof(double));
    memcpy(out, source, sizeof(input));
    if (!iv[3] || !iv[4] || !iv[5]) return 0;
    for (int64_t r = 0; r < iv[0]; ++r) {
        memcpy(input, out, sizeof(input));
        int changed = ca_processor(iv, controls, p, r, input, out);
        if (changed < 0) return changed;
        if (changed) report[0] = 1.0;
    }
    int any = 0;
    for (int c = 0; c < 512; ++c) if (active[c]) {
        if (!any || source[c] > report[1]) report[1] = source[c];
        if (!any || out[c] > report[2]) report[2] = out[c];
        any = 1;
    }
    report[3] = any;
    return 0;
}
