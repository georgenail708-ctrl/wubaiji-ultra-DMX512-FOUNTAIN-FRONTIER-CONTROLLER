/* R211 current AudioMod points/ramp/wave + cached-file control mathematics. */
static double ca_points(const double *points, int64_t n, double u)
{
    if (!n) return 0.0;
    u = ca_clamp(u);
    if (u <= points[0]) return points[1];
    if (u >= points[(n - 1) * 3]) return points[(n - 1) * 3 + 1];
    int64_t lo = 0, hi = n;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (points[mid * 3] <= u) lo = mid + 1; else hi = mid;
    }
    int64_t i = lo - 1;
    if (i > n - 2) i = n - 2;
    const double *a = points + i * 3, *b = a + 3;
    double span = b[0] - a[0];
    if (span <= 0.0) return b[1];
    double s = ca_clamp((u - a[0]) / span), c = a[2], shape = s;
    if (fabs(c) >= 1e-9) {
        double q = _ce_pow_ucrt(16.0, ca_clamp(fabs(c)));
        shape = c > 0.0 ? _ce_pow_ucrt(s, q) : 1.0 - _ce_pow_ucrt(1.0 - s, q);
    }
    return a[1] + shape * (b[1] - a[1]);
}

static double ca_clip_ramp(const double *clip, const int64_t *offsets,
                           const double *points, int64_t i, const double *d)
{
    double t = d[0], span;
    if (offsets[i + 1] > offsets[i]) {
        span = clip[1] - clip[0];
        if (span < 1e-6) span = 1e-6;
        return ca_points(points + offsets[i] * 3, offsets[i + 1] - offsets[i],
                         (t - clip[0]) / span);
    }
    double attack = clip[2] < 0.0 ? d[1] : clip[2];
    double release = clip[3] < 0.0 ? d[2] : clip[3];
    if (t < clip[0] + attack) {
        span = attack > 1e-6 ? attack : 1e-6;
        double v = (t - clip[0]) / span;
        return v < 1.0 ? v : 1.0;
    }
    if (t > clip[1] - release) {
        span = release > 1e-6 ? release : 1e-6;
        double v = (clip[1] - t) / span;
        return v < 1.0 ? v : 1.0;
    }
    return 1.0;
}

static double ca_envelope(int64_t count, const double *clips,
                          const int64_t *offsets, const double *points,
                          const double *d)
{
    double out = 0.0;
    for (int64_t i = 0; i < count; ++i) {
        const double *clip = clips + i * 6;
        if (clip[0] <= d[0] && d[0] <= clip[1]) {
            double ramp = ca_clip_ramp(clip, offsets, points, i, d);
            double span = clip[1] - clip[0];
            if (span < 1e-6) span = 1e-6;
            double freq = clip[4] > 0.0 ? clip[4] : 0.0;
            double phase = ((d[0] - clip[0]) / span) * freq + d[3];
            out = ca_clamp(ramp * ca_wave(phase, (int)clip[5]));
        }
    }
    return out;
}

static int64_t ca_lower(const double *times, int64_t n, double t, int right)
{
    int64_t lo = 0, hi = n;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (times[mid] < t || (right && times[mid] == t)) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static void ca_file_controls(const int64_t *iv, const double *d, void **p)
{
    const double *times = (const double *)p[0], *curves = (const double *)p[1];
    const double *beats = (const double *)p[2], *strength = (const double *)p[3];
    double *out = (double *)p[4];
    memset(out, 0, 10 * sizeof(double));
    out[6] = 120.0;
    if (!iv[1] || d[0] < 0.0 || d[0] >= d[1]) return;
    out[9] = 1.0;
    int64_t i = ca_lower(times, iv[1], d[0], 0);
    if (i >= iv[1]) i = iv[1] - 1;
    int64_t previous = i > 0 ? i - 1 : 0;
    double width = times[i] - times[previous];
    double amount = width > 0.0 ? ca_clamp((d[0] - times[previous]) / width) : 0.0;
    double values[6];
    for (int c = 0; c < 6; ++c) {
        double a = curves[previous * 6 + c], b = curves[i * 6 + c];
        if (!isfinite(a)) a = 0.0;
        if (!isfinite(b)) b = 0.0;
        values[c] = a + amount * (b - a);
    }
    out[0] = ca_unit(values[0]); out[5] = ca_unit(values[1]);
    out[2] = ca_unit(values[3]); out[3] = ca_unit(values[4]); out[4] = ca_unit(values[5]);
    out[8] = values[2] > 1e-5;
    out[6] = isfinite(d[2]) && d[2] > 1.0 ? d[2] : 1.0;
    if (!isfinite(d[2])) out[6] = 120.0;
    if (!iv[2]) { out[7] = _ce_mod_div(d[0] * d[2] / 60.0, 1.0); return; }
    i = ca_lower(beats, iv[2], d[0], 1) - 1;
    if (i < 0) i = 0;
    double period = i + 1 < iv[2] ? beats[i + 1] - beats[i] : 60.0 / out[6];
    if (period < 1e-9) period = 1e-9;
    out[7] = _ce_mod_div((d[0] - beats[i]) / period, 1.0);
    out[1] = i < iv[3] ? ca_unit(strength[i]) : 0.0;
}

/* Defined by the scope domain; diagnostics and postmix share exact sampling. */
static double ca_legacy_feature(const double *beats, const double *series,
                                int64_t count, double t, int kind);

CE_API int ce_audio_math(const int64_t *iv, const double *d, void **p)
{
    if (!iv || !d || !p || iv[1] < 0) return -1;
    if (iv[0] == 0 || iv[0] == 6) {
        double *out = (double *)p[3];
        out[0] = ca_envelope(iv[1], (const double *)p[0], (const int64_t *)p[1],
                              (const double *)p[2], d);
        out[1] = out[0] <= 0.0 ? 0.0 : ca_clamp(out[0] * d[4] * d[5]);
        if (iv[0] == 6) out[2] = 1.0;
        return 0;
    }
    if (iv[0] == 1) { ca_file_controls(iv, d, p); return 0; }
    if (iv[0] == 2 || iv[0] == 7) {
        const double *values = (const double *)p[0];
        double *out = (double *)p[1];
        for (int64_t i = 0; i < iv[1]; ++i) out[i] = values[i] * d[0];
        if (iv[0] == 7) out[iv[1]] = (double)iv[1];
        return 0;
    }
    if (iv[0] == 3) {
        double *out = (double *)p[0];
        out[0] = d[0]; out[1] = d[1]; out[2] = d[2];
        if (iv[1] && iv[2]) {
            if (d[3] > out[0]) out[0] = d[3];
            if (d[4] > out[1]) out[1] = d[4];
            out[2] += 1.0;
        }
        return 0;
    }
    if (iv[0] == 4) {
        ((double *)p[0])[0] = !iv[2] && iv[3] && !(d[0] - d[1] > .5);
        return 0;
    }
    if (iv[0] == 5) {
        double args[6] = {d[0], d[1], d[2], 0.0, 1.0, d[3]};
        double env = ca_envelope(iv[1], (const double *)p[0],
                                (const int64_t *)p[1], (const double *)p[2], args);
        double *out = (double *)p[5];
        out[0] = env <= 0.0 ? 0.0 : ca_clamp(env * ca_legacy_feature(
            (const double *)p[3], (const double *)p[4], iv[2], d[0], (int)iv[3]) * d[3]);
        out[1] = 1.0;
        return 0;
    }
    return -2;
}
