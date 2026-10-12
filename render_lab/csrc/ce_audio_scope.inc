/* R211 legacy AudioMod: full source/Draw/history/fan/post-mix computation. */
typedef struct {
    const int64_t *iv;
    const double *d;
    void **p;
    double env, global, dt;
    int fan, nbucket;
} ca_scope;

static double ca_legacy_feature(const double *beats, const double *series,
                                int64_t count, double t, int kind)
{
    if (!count || kind == 0 || kind < 0 || kind > 5) return 1.0;
    int64_t i = ca_lower(beats, count, t, 0);
    if (i >= count) i = count - 1;
    if (i > 0 && fabs(beats[i - 1] - t) < fabs(beats[i] - t)) --i;
    return series[i * 5 + kind - 1];
}

static double ca_beat_feature(const ca_scope *ctx, int kind)
{
    return ca_legacy_feature((const double *)ctx->p[7], (const double *)ctx->p[8],
                              ctx->iv[5], ctx->d[0], kind);
}

static double ca_source_value(const ca_scope *ctx, int kind, double phase)
{
    if (!ctx->iv[5]) return 1.0;
    double bpm = ctx->d[7], t = ctx->d[0];
    if (kind == 6) return _ce_mod_div(t * (bpm > 1.0 ? bpm : 1.0) / 60.0 + phase, 1.0);
    if (kind == 7) {
        double low = ca_beat_feature(ctx, 2), high = ca_beat_feature(ctx, 4);
        double denominator = low + high;
        if (denominator < 1e-6) denominator = 1e-6;
        return ca_clamp(low / denominator);
    }
    if (kind == 8) {
        if (!ctx->iv[8]) return .5;
        const double *bounds = (const double *)ctx->p[9];
        double total = ctx->d[8];
        if (!total) { total = bounds[ctx->iv[8] - 1]; if (!total) total = 1.0; }
        double start = 0.0, end = total;
        for (int64_t i = 0; i < ctx->iv[8]; ++i)
            if (bounds[i] > 0.0 && bounds[i] < total && bounds[i] <= t && bounds[i] >= start)
                start = bounds[i];
        for (int64_t i = 0; i < ctx->iv[8]; ++i)
            if (bounds[i] > start && bounds[i] < end && bounds[i] < total) end = bounds[i];
        double span = end - start;
        if (span < 1e-6) span = 1e-6;
        return ca_clamp((t - start) / span);
    }
    return ca_clamp(ca_beat_feature(ctx, kind));
}

static double ca_rule_source(const ca_scope *ctx, const double *row, int64_t r)
{
    const int64_t *offsets = (const int64_t *)ctx->p[12], *indexes = (const int64_t *)ctx->p[13];
    int64_t count = offsets[r + 1] - offsets[r];
    if (!count) return ca_source_value(ctx, (int)row[0], 0.0);
    double total = 0.0;
    for (int64_t i = offsets[r]; i < offsets[r + 1]; ++i) {
        int64_t s = indexes[i];
        if (s < 0) total += ca_source_value(ctx, (int)row[0], 0.0);
        else {
            const double *source = (const double *)ctx->p[11] + s * 2;
            total += ca_source_value(ctx, source[0] < 0.0 ? (int)row[0] : (int)source[0], source[1]);
        }
    }
    return total / (double)count;
}

static double ca_rule_shape(const ca_scope *ctx, const double *row, double phase)
{
    int wave = (int)row[9];
    if (wave < 0) return 1.0;
    static const double units[5] = {1.0, 4.0, 1.5, 2.0 / 3.0, .25};
    double bpm = ctx->d[7], period = 1.0;
    int unit = (int)row[10];
    if (unit >= 0 && unit < 5) period = units[unit];
    else if (unit == 5) period = (bpm > 1e-6 ? bpm : 1e-6) / 60.0;
    double freq = row[1] > .25 ? row[1] : .25;
    double u = ctx->d[0] * (bpm > 1.0 ? bpm : 1.0) / 60.0;
    double raw = u * (freq / period) + row[5] + phase;
    if (wave == 4 && row[11]) {
        double n = floor(raw), target = ca_clamp(ca_rand(n));
        if (row[11] != 2.0) return target;
        double previous = ca_clamp(ca_rand(n - 1.0));
        double amount = (raw - n) / .25;
        if (amount > 1.0) amount = 1.0;
        return previous + (target - previous) * amount;
    }
    if (wave != 9 && wave != 10) raw = _ce_mod_div(raw, 1.0);
    return ca_clamp(ca_wave(raw, wave));
}

static double ca_rule_dynamics(ca_scope *ctx, const double *row, int64_t slot, double value)
{
    double *state = (double *)ctx->p[15] + slot * 4;
    uint8_t *flags = (uint8_t *)ctx->p[16] + slot * 5;
    double threshold = ca_clamp(row[7]);
    int opened = 1;
    if (threshold > 0.0) {
        double close = ca_clamp(row[12]);
        if (!close) close = threshold;
        double hold = row[13] > 0.0 ? row[13] : 0.0;
        opened = flags[3] && state[2] != 0.0;
        double held = flags[3] ? state[3] : 0.0;
        if (opened && value < close) { held += ctx->dt; opened = held * 1000.0 < hold; }
        else if (opened) held = 0.0;
        else if (value >= threshold) { opened = 1; held = 0.0; }
        flags[3] = 1; state[2] = opened; state[3] = held;
    }
    double shaped = 0.0;
    if (opened) {
        if (row[6] > .5) value = 1.0 - value;
        double sm = ca_clamp(row[8]), y = value;
        if (flags[1] && sm > 0.0 && ctx->dt > 0.0) {
            if (sm >= 1.0) y = state[0];
            else {
                double tau = -(1.0 / 60.0) / log(sm);
                double alpha = 1.0 - exp(-ctx->dt / tau);
                y = state[0] + alpha * (value - state[0]);
            }
        }
        state[0] = y; flags[1] = 1; shaped = ca_clamp(y);
    }
    double fy = shaped;
    if (flags[2] && ctx->dt > 0.0) {
        double tau = shaped > state[1] ? row[3] : row[4];
        if (tau > 0.0) {
            double g = exp(-ctx->dt / tau);
            fy = state[1] + (1.0 - g) * (shaped - state[1]);
        }
    }
    state[1] = fy; flags[0] = flags[2] = flags[4] = 1;
    return ca_clamp(fy * ca_clamp(row[2]));
}

static void ca_scope_fan(ca_scope *ctx)
{
    int64_t n = ctx->iv[4];
    double spread = ctx->d[4], from = ctx->d[5], to = ctx->d[6];
    if (spread < 0.0) spread = 0.0; if (spread > 360.0) spread = 360.0;
    if (from < 0.0) from = 0.0; if (from > 360.0) from = 360.0;
    if (to < 0.0) to = 0.0; if (to > 360.0) to = 360.0;
    ctx->fan = n > 1 && (spread != 0.0 || from != 0.0 || to != 0.0);
    ctx->nbucket = ctx->fan ? (n <= 64 ? (int)n : 64) : 1;
    if (!ctx->fan) return;
    double *offsets = (double *)ctx->p[20], *clips = (double *)ctx->p[22];
    int64_t *buckets = (int64_t *)ctx->p[21];
    for (int64_t i = 0; i < n; ++i) {
        double deg = from + (to - from) * ((double)i / (double)(n - 1))
                   + spread * ((double)i / (double)n);
        offsets[i] = _ce_mod_div(deg, 360.0) / 360.0;
        buckets[i] = n <= 64 ? i : _ce_py_round_even(offsets[i] * 64.0) % 64;
        double d[6] = {ctx->d[0], ctx->d[1], ctx->d[2], offsets[i], 1.0, 1.0};
        clips[i] = ca_envelope(ctx->iv[1], (const double *)ctx->p[4],
                              (const int64_t *)ctx->p[5], (const double *)ctx->p[6], d);
        clips[i] = ca_clamp(clips[i] * ca_beat_feature(ctx, (int)ctx->iv[9]) * ctx->d[3]);
    }
}

static void ca_scope_rules(ca_scope *ctx, double *report)
{
    if (!ctx->iv[2] || !ctx->iv[3] || ctx->env <= 0.0) return;
    ctx->dt = ctx->iv[7] ? ctx->d[0] - ctx->d[9] : 0.0;
    if (!(ctx->dt > 0.0 && ctx->dt <= .5)) ctx->dt = 0.0;
    report[2] = 1.0; report[3] = ctx->d[0];
    double *gains = (double *)ctx->p[18];
    uint8_t *present = (uint8_t *)ctx->p[19];
    const uint8_t *targets = (const uint8_t *)ctx->p[14];
    for (int64_t r = 0; r < ctx->iv[2]; ++r) {
        int any = 0;
        for (int64_t c = 0; c < ctx->iv[3]; ++c) any |= targets[r * ctx->iv[3] + c];
        if (!any) continue;
        const double *row = (const double *)ctx->p[10] + r * 14;
        double signal = ca_rule_source(ctx, row, r);
        for (int b = 0; b < ctx->nbucket; ++b) {
            double phase = 0.0;
            if (ctx->fan) phase = ctx->iv[4] <= 64 ? ((const double *)ctx->p[20])[b] : (double)b / 64.0;
            double value = signal * ca_rule_shape(ctx, row, phase);
            value = ca_rule_dynamics(ctx, row, r * 64 + b, value);
            report[1] += 1.0;
            for (int64_t c = 0; c < ctx->iv[3]; ++c) if (targets[r * ctx->iv[3] + c]) {
                int64_t index = c * 64 + b;
                gains[index] = 1.0 - (1.0 - gains[index]) * (1.0 - value);
                present[index] = 1;
            }
        }
    }
    double env = (ctx->env > 0.0 ? ctx->env : 0.0) * ca_clamp(ctx->d[3]);
    for (int64_t i = 0; i < ctx->iv[3] * 64; ++i)
        if (present[i]) gains[i] = ca_clamp(env * gains[i]);
}

static void ca_scope_outputs(ca_scope *ctx, double *report)
{
    uint8_t *output = (uint8_t *)ctx->p[0];
    const uint8_t *global = (const uint8_t *)ctx->p[1], *ct = (const uint8_t *)ctx->p[2];
    const uint8_t *admitted = (const uint8_t *)ctx->p[17], *present = (const uint8_t *)ctx->p[19];
    const int64_t *ordinals = (const int64_t *)ctx->p[3], *buckets = (const int64_t *)ctx->p[21];
    const double *gains = (const double *)ctx->p[18], *clips = (const double *)ctx->p[22];
    for (int64_t u = 0; u < ctx->iv[0]; ++u) {
        if (!admitted[u]) continue;
        uint8_t original[512];
        memcpy(original, output + u * 512, 512);
        for (int c = 0; c < 512; ++c) {
            int64_t index = u * 512 + c, ordinal = ordinals[index];
            double gain = ctx->global;
            int selected = global[index] != 0;
            int bucket = 0;
            if (ctx->fan && selected && ordinal >= 0) {
                bucket = (int)buckets[ordinal];
                if (ctx->iv[1]) gain = clips[ordinal];
            }
            for (int64_t kind = 0; kind < ctx->iv[3]; ++kind) {
                if (!ct[kind * ctx->iv[0] * 512 + index]) continue;
                if (ctx->fan && (!selected || ordinal < 0)) continue;
                int64_t g = kind * 64 + bucket;
                if (present[g] && (ctx->fan || gains[g] != ctx->global)) {
                    gain = gains[g]; selected = 1;
                }
            }
            if (selected) {
                output[index] = (uint8_t)_ce_round_half_even((double)original[c] * gain);
                report[0] += 1.0;
            }
        }
    }
}

CE_API int ce_audio_scope(const int64_t *iv, const double *d, void **p)
{
    if (!iv || !d || !p) return -1;
    for (int i = 0; i < 7; ++i) if (iv[i] < 0) return -2;
    ca_scope ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.iv = iv; ctx.d = d; ctx.p = p;
    double *report = (double *)p[23];
    memset(report, 0, 5 * sizeof(double));
    double env_d[6] = {d[0], d[1], d[2], 0.0, 1.0, 1.0};
    ctx.env = iv[1] ? ca_envelope(iv[1], (const double *)p[4],
                               (const int64_t *)p[5], (const double *)p[6], env_d) : 1.0;
    ctx.global = iv[1] ? ca_clamp(ctx.env * ca_beat_feature(&ctx, (int)iv[9]) * d[3]) : 1.0;
    ca_scope_fan(&ctx);
    ca_scope_rules(&ctx, report);
    if (!ctx.fan && ctx.global >= 1.0) {
        const uint8_t *present = (const uint8_t *)p[19];
        int any = 0;
        for (int64_t i = 0; i < iv[3] * 64; ++i) any |= present[i];
        if (!any) { report[4] = 1.0; return 0; }
    }
    ca_scope_outputs(&ctx, report);
    return 0;
}
