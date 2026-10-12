/* R211 AudioMod scalar primitives. Include after existing CE mathematical core.
 * Reuse project CE modulo/UCRT pow/split helpers and CPython colorsys branches.
 * No allocation, Python callback, analysis worker, or changed public old ABI. */
#ifndef CE_AUDIO_COMMON_INCLUDED
#define CE_AUDIO_COMMON_INCLUDED

static double ca_unit(double value)
{
    if (!isfinite(value)) return 0.0;
    return value < 0.0 ? 0.0 : value > 1.0 ? 1.0 : value;
}

static double ca_clamp(double value)
{
    return value > 0.0 ? (value < 1.0 ? value : 1.0) : 0.0;
}

static double ca_rand(double value)
{
    uint64_t k = (uint64_t)(int64_t)_ce_round_half_even(value * 100000.0);
    k = (k ^ (k >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    k = (k ^ (k >> 27)) * UINT64_C(0x94D049BB133111EB);
    k ^= k >> 31;
    return (double)(k & UINT64_C(0xFFFFFFFF)) / 4294967296.0;
}

static double ca_wave(double value, int wave)
{
    if (wave < 0) return 1.0;
    if (wave == 4) return ca_rand(value);
    if (wave < 8) return _ce_wave(value, wave);
    if (wave == 8)
        return (1.0 - exp(-5.0 * _ce_mod_div(value, 1.0))) / (1.0 - exp(-5.0));
    if (wave == 9) return ca_rand(floor(value));
    if (wave == 10) {
        double n = floor(value), u = _ce_mod_div(value, 1.0);
        double a = ca_rand(n), b = ca_rand(n + 1.0);
        u = ca_clamp(u);
        double shape = u * u * (3.0 - 2.0 * u);
        return a + (b - a) * shape;
    }
    return 1.0;
}

static float ca_mod_f32(float value)
{
    float result = fmodf(value, 1.0f);
    if (result < 0.0f) result = result + 1.0f;
    if (result == 0.0f) result = 0.0f;
    return result;
}

static void ca_rgb_hsv_f32(const float rgb[3], float hsv[3])
{
    float r = rgb[0] / 255.0f, g = rgb[1] / 255.0f, b = rgb[2] / 255.0f;
    float hi = r, lo = r;
    if (g > hi) hi = g;
    if (b > hi) hi = b;
    if (g < lo) lo = g;
    if (b < lo) lo = b;
    float range = hi - lo;
    hsv[2] = hi;
    if (lo == hi) { hsv[0] = 0.0f; hsv[1] = 0.0f; return; }
    hsv[1] = range / hi;
    float rc = (hi - r) / range, gc = (hi - g) / range, bc = (hi - b) / range;
    float hue;
    if (r == hi) hue = bc - gc;
    else if (g == hi) { float a = 2.0f + rc; hue = a - bc; }
    else { float a = 4.0f + gc; hue = a - rc; }
    hsv[0] = ca_mod_f32(hue / 6.0f);
}

static void ca_hsv_rgb_f32(const float hsv[3], float rgb[3])
{
    float h = hsv[0], s = hsv[1], v = hsv[2];
    if (s == 0.0f) { rgb[0] = rgb[1] = rgb[2] = v; return; }
    float h6 = h * 6.0f;
    int i = (int)h6;
    float f = h6 - (float)i;
    float p = v * (1.0f - s);
    float sf = s * f, q = v * (1.0f - sf);
    float smf = s * (1.0f - f), t = v * (1.0f - smf);
    i %= 6;
    if (i == 0) { rgb[0] = v; rgb[1] = t; rgb[2] = p; }
    else if (i == 1) { rgb[0] = q; rgb[1] = v; rgb[2] = p; }
    else if (i == 2) { rgb[0] = p; rgb[1] = v; rgb[2] = t; }
    else if (i == 3) { rgb[0] = p; rgb[1] = q; rgb[2] = v; }
    else if (i == 4) { rgb[0] = t; rgb[1] = p; rgb[2] = v; }
    else { rgb[0] = v; rgb[1] = p; rgb[2] = q; }
}
#endif
