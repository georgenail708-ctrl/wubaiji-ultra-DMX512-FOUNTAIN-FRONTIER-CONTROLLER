/* Current R16 scalar easing and exact legacy f32 array transition semantics. */
static double _ce_motion_out_bounce(double t)
{
    if (t < 1.0 / 2.75) return 7.5625 * t * t;
    if (t < 2.0 / 2.75) { t -= 1.5 / 2.75; return 7.5625 * t * t + 0.75; }
    if (t < 2.5 / 2.75) { t -= 2.25 / 2.75; return 7.5625 * t * t + 0.9375; }
    t -= 2.625 / 2.75;
    return 7.5625 * t * t + 0.984375;
}

static double _ce_motion_family(int family, int out, double t)
{
    double inv = 1.0 - t;
    switch (family) {
    case 0: return out ? 1.0 - inv * inv : t * t;
    case 1: return out ? 1.0 - inv * inv * inv : t * t * t;
    case 2: return out ? 1.0 - inv * inv * inv * inv : t * t * t * t;
    case 3: return out ? 1.0 - _ce_pow_ucrt(inv, 5.0) : _ce_pow_ucrt(t, 5.0);
    case 4: return out ? sin(t * M_PI / 2.0) : 1.0 - cos(t * M_PI / 2.0);
    case 5:
        if (t <= 0.0 || t >= 1.0) return t;
        return out ? 1.0 - _ce_pow_ucrt(2.0, -10.0 * t)
                   : _ce_pow_ucrt(2.0, 10.0 * t - 10.0);
    case 6:
        if (out) { t -= 1.0; return sqrt(fmax(0.0, 1.0 - t * t)); }
        return 1.0 - sqrt(fmax(0.0, 1.0 - t * t));
    case 7:
        if (t <= 0.0 || t >= 1.0) return t;
        if (out) return _ce_pow_ucrt(2.0, -10.0 * t)
            * sin((t - 0.075) * (-2.0 * M_PI / 0.3)) + 1.0;
        t -= 1.0;
        return -_ce_pow_ucrt(2.0, 10.0 * t) * sin((t - 0.075) * (2.0 * M_PI) / 0.3);
    case 8:
        if (out) { t -= 1.0; return t * t * ((1.70158 + 1.0) * t + 1.70158) + 1.0; }
        return t * t * ((1.70158 + 1.0) * t - 1.70158);
    case 9: return out ? _ce_motion_out_bounce(t) : 1.0 - _ce_motion_out_bounce(inv);
    default: return t;
    }
}

static double _ce_motion_library(int code, double t)
{
    int key = code - 100, family = key / 4, variant = key % 4;
    if (variant < 2) return _ce_motion_family(family, variant, t);
    int first_out = variant == 3;
    if (t < 0.5) return 0.5 * _ce_motion_family(family, first_out, t * 2.0);
    return 0.5 + 0.5 * _ce_motion_family(family, !first_out, (t - 0.5) * 2.0);
}

static double _ce_motion_curve64(double t, int fade_in, int code, int strategy)
{
    if (t < 0.0) t = 0.0;
    else if (t > 1.0) t = 1.0;
    if (code < 100) return _ce_transition_gain64(t, fade_in, code, strategy);
    double g = _ce_motion_library(code, t);
    switch (strategy) {
    case 1: g = 1.0; break;
    case 2: if (!fade_in) g = sqrt(g); break;
    case 3: if (!fade_in) g = t >= 0.6 ? 1.0 : t / 0.6; break;
    case 4: g = fade_in ? floor(g * 4.0) / 4.0 : ceil(g * 4.0) / 4.0; break;
    case 5: g = g * g * g * (g * (g * 6.0 - 15.0) + 10.0); break;
    case 6: if (!fade_in) g = 1.0; break;
    }
    return g < 0.0 ? 0.0 : (g > 1.0 ? 1.0 : g);
}

static float _ce_motion_curve32(float t, int fade_in, int code, int strategy)
{
    if (code < 100) return _ce_transition_gain(t, fade_in, code, strategy);
    int idx = isfinite(t) ? (int)ce_vc_round_even((double)t * 1023.0) : 0;
    if (idx < 0) idx = 0;
    else if (idx > 1023) idx = 1023;
    float g = (float)_ce_motion_library(code, (double)idx / 1023.0);
    switch (strategy) {
    case 1: g = 1.0f; break;
    case 2: if (!fade_in) g = sqrtf(g); break;
    case 3: if (!fade_in) g = t >= .6f ? 1.0f : t / .6f; break;
    case 4: g = fade_in ? floorf(g * 4.0f) / 4.0f : ceilf(g * 4.0f) / 4.0f; break;
    case 5: g = g * g * g * (g * (g * 6.0f - 15.0f) + 10.0f); break;
    case 6: if (!fade_in) g = 1.0f; break;
    }
    return ce_np_minf(ce_np_maxf(g, 0.0f), 1.0f);
}

static double _ce_motion_force_time(double t, double duration, double a, double c, double d)
{
    if (duration <= 1e-6) return t < 0.0 ? 0.0 : t;
    double p = t / duration;
    p = p < 0.0 ? 0.0 : (p > 1.0 ? 1.0 : p);
    double speed = 1.0 / (c + 0.5 * (a + d)), value;
    if (p < a) value = 0.5 * speed * p * p / a;
    else if (p < a + c) value = 0.5 * speed * a + speed * (p - a);
    else {
        double elapsed = p - a - c;
        elapsed = elapsed < 0.0 ? 0.0 : (elapsed > d ? d : elapsed);
        value = 0.5 * speed * a + speed * c + speed * (elapsed - 0.5 * elapsed * elapsed / d);
    }
    double shaped = value * duration, cap = duration - 1e-4;
    cap = cap < 0.0 ? 0.0 : cap;
    return shaped < 0.0 ? 0.0 : (shaped > cap ? cap : shaped);
}

CE_API double ce_source_motion_fade(double dur, double fi, double fo, double t,
    int enabled, int curve_in, int curve_out, int strategy_in, int strategy_out)
{
    if (dur <= 0.0) return 1.0;
    fi = fi < 0.0 ? 0.0 : (fi > dur ? dur : fi);
    fo = fo < 0.0 ? 0.0 : (fo > dur ? dur : fo);
    double result = 1.0;
    if (fi > 1e-6 && t < fi) result = _ce_motion_curve64(t / fi, 1,
        enabled ? curve_in : 3, enabled ? strategy_in : 0);
    if (fo > 1e-6 && t > dur - fo) {
        double gain = _ce_motion_curve64((dur - t) / fo, 0,
            enabled ? curve_out : 3, enabled ? strategy_out : 0);
        if (gain < result) result = gain;
    }
    return result;
}
