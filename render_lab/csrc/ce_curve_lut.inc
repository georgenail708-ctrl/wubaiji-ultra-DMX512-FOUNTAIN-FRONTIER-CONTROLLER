/* Cold CurveMapping vector LUT, preserving the original model's Bezier32 AST.
 * Interpolation/search adapted from NumPy compiled_base.c at
 * 70fa99843c75ae12434805bb4b1b07b94598c5c9; the host ABI and Bezier adapter are
 * Qascade-specific. Preserve this notice in source and binary distributions.
 *
 * Copyright (c) 2005-2025, NumPy Developers.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * * Redistributions of source code must retain the above copyright notice,
 *   this list of conditions and the following disclaimer.
 * * Redistributions in binary form must reproduce the above copyright notice,
 *   this list of conditions and the following disclaimer in the documentation
 *   and/or other materials provided with the distribution.
 * * Neither the name of the NumPy Developers nor the names of any contributors
 *   may be used to endorse or promote products derived from this software
 *   without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

typedef struct { double *x, *y; int64_t n; } CeCurvePoints;

static int64_t ccl_search(double key, const double *arr, int64_t len, int64_t guess)
{
    int64_t imin = 0, imax = len;
    if (key > arr[len - 1]) return len;
    if (key < arr[0]) return -1;
    if (len <= 4) {
        int64_t i = 1;
        while (i < len && key >= arr[i]) ++i;
        return i - 1;
    }
    if (guess > len - 3) guess = len - 3;
    if (guess < 1) guess = 1;
    if (key < arr[guess]) {
        if (key < arr[guess - 1]) {
            imax = guess - 1;
            if (guess > 8 && key >= arr[guess - 8]) imin = guess - 8;
        } else return guess - 1;
    } else {
        if (key < arr[guess + 1]) return guess;
        if (key < arr[guess + 2]) return guess + 1;
        imin = guess + 2;
        if (guess < len - 9 && key < arr[guess + 8]) imax = guess + 8;
    }
    while (imin < imax) {
        int64_t mid = imin + ((imax - imin) >> 1);
        if (key >= arr[mid]) imin = mid + 1; else imax = mid;
    }
    return imin - 1;
}

static double ccl_interp(const CeCurvePoints *p, double key, int64_t *guess)
{
    int64_t j = ccl_search(key, p->x, p->n, *guess);
    *guess = j;
    if (j == -1) return p->y[0];
    if (j == p->n) return p->y[p->n - 1];
    if (j == p->n - 1 || p->x[j] == key) return p->y[j];
    double slope = (p->y[j + 1] - p->y[j]) / (p->x[j + 1] - p->x[j]);
    double result = slope * (key - p->x[j]) + p->y[j];
    if (isnan(result)) {
        result = slope * (key - p->x[j + 1]) + p->y[j + 1];
        if (isnan(result) && p->y[j] == p->y[j + 1]) result = p->y[j];
    }
    return result;
}

static double ccl_coord(double t, double p0, double p1, double p2, double p3)
{
    double u = 1.0 - t;
    return u * u * u * p0 + 3.0 * u * u * t * p1
        + 3.0 * u * t * t * p2 + t * t * t * p3;
}

static double ccl_ordered_clamp(double value, double low, double high)
{
    /* Python min(max(value, low), high) keeps the first unordered operand. */
    double result = low > value ? low : value;
    return high < result ? high : result;
}

static void ccl_segment(CeCurvePoints *p, const double *a, const double *b,
                        const uint8_t *af, const uint8_t *bf)
{
    if (!(af[2] || af[3] || bf[0] || bf[1])) {
        p->x[p->n] = b[0]; p->y[p->n++] = b[1];
        return;
    }
    double c2x = af[2] ? a[4] : a[0], c2y = af[3] ? a[5] : a[1];
    double c1x = bf[0] ? b[2] : b[0], c1y = bf[1] ? b[3] : b[1];
    double low = a[0] <= b[0] ? a[0] : b[0];
    double high = a[0] <= b[0] ? b[0] : a[0];
    c2x = ccl_ordered_clamp(c2x, low, high);
    c1x = ccl_ordered_clamp(c1x, c2x, high);
    for (int k = 1; k <= 32; ++k) {
        double t = k / 32.0;
        p->x[p->n] = ccl_coord(t, a[0], c2x, c1x, b[0]);
        p->y[p->n++] = ccl_coord(t, a[1], c2y, c1y, b[1]);
    }
}

static void ccl_keep_original_adjacent(CeCurvePoints *p)
{
    int bad = 0;
    for (int64_t i = 1; i < p->n; ++i)
        if (p->x[i] - p->x[i - 1] <= 0.0) { bad = 1; break; }
    if (!bad) return;
    int64_t used = 1;
    double previous = p->x[0];
    for (int64_t i = 1; i < p->n; ++i) {
        double current = p->x[i];
        if (current - previous > 0.0) {
            p->x[used] = current; p->y[used++] = p->y[i];
        }
        previous = current;
    }
    p->n = used;
}

static int ccl_prepare(CeCurvePoints *p, int64_t n, const double *rows,
                        const uint8_t *flags)
{
    int handles = 0;
    for (int64_t i = 0; i < n * 4; ++i) if (flags[i]) { handles = 1; break; }
    if (handles && n > (INT64_MAX - 1) / 32 + 1) return -1;
    int64_t cap = handles && n > 1 ? (n - 1) * 32 + 1 : n;
    if ((uint64_t)cap > SIZE_MAX / sizeof(double)) return -1;
    p->x = (double *)malloc((size_t)cap * sizeof(double));
    p->y = (double *)malloc((size_t)cap * sizeof(double));
    if (!p->x || !p->y) { free(p->x); free(p->y); return -2; }
    p->n = 0;
    if (!handles) {
        for (int64_t i = 0; i < n; ++i) {
            p->x[p->n] = rows[i * 6]; p->y[p->n++] = rows[i * 6 + 1];
        }
    } else {
        p->x[0] = rows[0]; p->y[0] = rows[1]; p->n = 1;
        for (int64_t i = 0; i < n - 1; ++i)
            ccl_segment(p, rows + i * 6, rows + (i + 1) * 6,
                        flags + i * 4, flags + (i + 1) * 4);
    }
    ccl_keep_original_adjacent(p);
    return 0;
}

CE_API int ce_curve_lut_eval(int64_t n, const double *points,
                                          const uint8_t *handle_present, float *out256)
{
    if (n < 0 || !out256) return -1;
    if ((uint64_t)n > SIZE_MAX / (6 * sizeof(double)) || n > INT64_MAX / 6) return -1;
    if (n && (!points || !handle_present)) return -1;
    if (!n) { memset(out256, 0, 256 * sizeof(float)); return 0; }
    CeCurvePoints p = { NULL, NULL, 0 };
    int status = ccl_prepare(&p, n, points, handle_present);
    if (status) return status;
    int64_t guess = 0;
    for (int k = 0; k < 256; ++k) {
        double value = p.n == 1 ? p.y[0] : ccl_interp(&p, (double)k, &guess);
        float quantized = (float)value;
        float result = quantized / 255.0f;
        out256[k] = result < 0.0f ? 0.0f : (result > 1.0f ? 1.0f : result);
    }
    free(p.x); free(p.y);
    return 0;
}
