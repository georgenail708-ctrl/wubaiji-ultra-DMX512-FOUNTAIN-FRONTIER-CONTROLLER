/* Cold sequential numeric preparation; the caller owns all dynamic buffers.
 * Pause input for normalize/gate is (conversion_valid,value) pairs. Strings and
 * object conversion remain metadata; nonnegative clamps and ratios stay here. */
static double _ce_prelude_positive(double value)
{
    return value > 0.0 ? value : 0.0;
}

static int _ce_prelude_batch_count(int groups, int declared)
{
    groups = groups < 1 ? 1 : groups;
    if (declared <= 0) return groups;
    return declared < groups ? declared : groups;
}

static int _ce_prelude_normalize(const int* i, const double* v,
    const double* input, double* out, double* result)
{
    int count = i[0], raw = i[1], capacity = i[2];
    if (count < 0 || raw < 0 || raw > capacity || count > capacity) return -1;
    if (count && (!result || (raw && !input))) return -1;
    double fallback = _ce_prelude_positive(v[0]);
    for (int p = 0; p < count; ++p) {
        double value = p < raw && input[2*p] ? input[2*p+1] : fallback;
        result[p] = _ce_prelude_positive(value);
    }
    out[0] = count;
    return 0;
}

static int _ce_prelude_offsets(const int* i, const double* v,
    const double* input, double* out, double* result)
{
    int raw = i[2], capacity = i[3];
    if (raw < 0 || capacity < 1 || raw > capacity) return -1;
    int count = i[1] ? (i[0] < 1 ? 1 : i[0]) : raw + 1;
    if (count > capacity || !result || (raw && !input)) return -1;
    double step = _ce_prelude_positive(v[0]), cursor = 0.0;
    for (int b = 0; b < count; ++b) {
        result[b] = cursor;
        cursor += step;
        if (b < count - 1)
            cursor += b < raw ? _ce_prelude_positive(input[b]) : 0.0;
    }
    out[0] = count;
    return 0;
}

static int _ce_prelude_gate(const int* i, const double* v,
    const double* input, double* out, double* result)
{
    int groups = i[0] < 1 ? 1 : i[0];
    int batches = _ce_prelude_batch_count(groups, i[1]);
    double single = _ce_prelude_positive(v[2]), delay = _ce_prelude_positive(v[3]);
    double fade = _ce_prelude_positive(v[4]);
    int wing = single > 1e-4 || delay > 1e-4 || (i[3] && fade > 1e-4 && i[2]);
    out[5] = groups; out[6] = batches; out[7] = i[3] && groups > 1 && wing;
    out[8] = batches - 1; out[9] = single; out[10] = delay; out[11] = i[2] ? fade : 0.0;
    out[12] = _ce_prelude_positive(v[1]);
    if (i[6]) return 0; /* Numeric gate only, before exact-count metadata parsing. */
    int normalization[3] = {batches - 1, i[4], i[5]};
    double fallback[1] = {v[1]}, normalized[1];
    int status = _ce_prelude_normalize(normalization, fallback, input, normalized, result);
    if (status) return status;
    out[0] = single; out[1] = -1.0; out[2] = 0.0; out[3] = 1.0; out[4] = 0.0;
    if (out[7]) {
        int window[3] = {batches, batches - 1, 2};
        double numbers[4] = {v[0], single, delay, i[2] ? fade : 0.0};
        _ce_clock_window(window, numbers, result, out, result);
    }
    return 0;
}
