/* Current clip safety math; state storage and lifecycle stay in the caller. */
typedef struct {
    const uint8_t *pump, *coarse, *fine, *light;
    const float *home, *last;
    const double *spans;
    double pump_scale, motor_scale, dps, local_t, prev_t;
    int prev_valid, finalize, suppress;
} ce_output_filter_spec;

CE_API int ce_output_filter_abi(void) { return 1; }
CE_API int ce_output_filter_size(void) { return (int)sizeof(ce_output_filter_spec); }

static int _ce_filter_valid(const ce_output_filter_spec* s)
{
    return s->pump && s->coarse && s->fine && s->light && s->home && s->spans
        && (!s->prev_valid || s->last);
}

static float _ce_filter_step(const ce_output_filter_spec* s, double dt, int fine)
{
    float result = 0.0f;
    for (int k = 0; k < 3; ++k) {
        double span = s->spans[k];
        if (!(span >= 1.0)) span = 1.0;
        double value = s->dps * (255.0 / span) * dt;
        if (fine) value = value / 256.0;
        result = ce_np_maxf(result, (float)value);
    }
    return result;
}

static int _ce_filter_apply(const ce_output_filter_spec* s, float* row)
{
    int target = 0;
    float ps = (float)s->pump_scale, ms = (float)s->motor_scale;
    for (int c = 0; c < 512; ++c) {
        if (s->pump_scale < 1.0 - 1e-6 && s->pump[c]) row[c] = row[c] * ps;
        if (s->motor_scale < 1.0 - 1e-6) {
            if (s->coarse[c]) {
                float delta = row[c] - s->home[c];
                float scaled = delta * ms;
                row[c] = s->home[c] + scaled;
            }
            if (s->fine[c]) row[c] = row[c] * ms;
        }
        target |= s->coarse[c] || s->fine[c];
    }
    return target;
}

static void _ce_filter_rate(const ce_output_filter_spec* s, float* row)
{
    double dt = s->local_t - s->prev_t;
    if (!s->prev_valid || !isfinite(dt) || dt <= 1e-9 || dt > 0.5) return;
    float coarse = _ce_filter_step(s, dt, 0), fine = _ce_filter_step(s, dt, 1);
    for (int c = 0; c < 512; ++c) {
        if (s->coarse[c] || s->fine[c]) {
            float step = s->coarse[c] ? coarse : fine;
            if (s->coarse[c] && s->fine[c]) step = ce_np_maxf(coarse, fine);
            float lo = s->last[c] - step, hi = s->last[c] + step;
            row[c] = ce_np_minf(ce_np_maxf(row[c], lo), hi);
        }
    }
}

static void _ce_filter_finalize(const ce_output_filter_spec* s, float* row)
{
    if (!s->finalize) return;
    for (int c = 0; c < 512; ++c) {
        if (s->suppress && s->light[c]) row[c] = 0.0f;
        if(row[c]<0.0f) row[c]=0.0f;
        else if(row[c]>255.0f) row[c]=255.0f;
    }
}

CE_API int ce_output_filter(int n, const ce_output_filter_spec* specs,
    float* data, float* rate_rows, double* rate_times, int* writes, int* clears)
{
    if (n < 0 || n > 8192) return -1;
    if (!n) return 0;
    if (!specs || !data || !rate_rows || !rate_times || !writes || !clears) return -1;
    for (int i = 0; i < n; ++i) if (!_ce_filter_valid(specs + i)) return -1;
    for (int i = 0; i < n; ++i) {
        const ce_output_filter_spec* s = specs + i;
        float* row = data + (size_t)i * 512;
        int active = s->pump_scale < 1.0 - 1e-6 || s->motor_scale < 1.0 - 1e-6 || s->dps > 1e-6;
        writes[i] = clears[i] = 0;
        int target = active ? _ce_filter_apply(s, row) : 0;
        if (active && s->dps > 1e-6 && target) {
            _ce_filter_rate(s, row);
            memcpy(rate_rows + (size_t)i * 512, row, sizeof(float) * 512);
            rate_times[i] = s->local_t;
            writes[i] = 1;
        }
        if (active && s->dps <= 1e-6) clears[i] = 1;
        _ce_filter_finalize(s, row);
    }
    return 0;
}

CE_API int ce_output_filter_suppress(void* data, const uint8_t* mask, int n, int kind)
{
    if (n < 0 || kind > 1 || kind < -32) return -1;
    if (!n) return 0;
    if (!data || !mask) return -1;
    for (int i = 0; i < n; ++i) if (mask[i]) {
        if (kind < 0) memset((uint8_t*)data + (size_t)i * (size_t)(-kind), 0, (size_t)(-kind));
        else if (kind) ((float*)data)[i] = 0.0f;
        else ((uint8_t*)data)[i] = 0;
    }
    return 0;
}
