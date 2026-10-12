/* Prepared K2 ABI 1. Included once at the end of ce_engine.c.
 * State, source selection and ownership stay with the public Python producer.
 * Inputs are synchronous borrowed buffers; no pointer or global state is kept.
 * /fp:precise and explicit f32 intermediates preserve NumPy's operation order.
 */
#define CE_PREPARED_LIMIT 8192

typedef struct {
    const uint8_t* raw;
    const uint8_t* end;
    const uint8_t* completed;
    const float* gains;
    const float* base;
    const uint8_t* active;
    const float* changes;
    const float* idle;
    const uint8_t* prearrival;
    const float* override;
    const double* base_scores;
    int mode, ramp, changes_mode;
    double start, rate, hold_cap, tie, boost, preboost;
} ce_prepared_task;

CE_API int ce_prepared_frame_abi_version(void) { return 1; }
CE_API int ce_prepared_task_size(void) { return (int)sizeof(ce_prepared_task); }

CE_API int ce_prepared_anchor_update(const float* data, const uint8_t* active,
    const double* scores, double tie, float* anchor, double* best)
{
    if (!data || !active || !scores || !anchor || !best) return -1;
    int count = 0;
    for (int c = 0; c < 512; c++) {
        const double score = scores[c] + tie;
        if (active[c] && isfinite(score) && score >= best[c] - 1e-12) {
            anchor[c] = data[c];
            best[c] = score;
            count++;
        }
    }
    return count;
}

static int _ce_prepared_task_valid(const ce_prepared_task* t)
{
    if (t->raw == NULL || t->active == NULL) return 0;
    if (t->mode < 0 || t->mode > 1 || t->ramp < 0 || t->ramp > 1) return 0;
    if ((t->end == NULL) != (t->completed == NULL)) return 0;
    if (t->override == NULL && t->gains == NULL) return 0;
    if (t->override == NULL && t->mode == 1 && t->base == NULL) return 0;
    if (t->changes_mode == 0) return t->changes != NULL;
    return t->changes_mode == 1 && t->base_scores != NULL;
}

static float _ce_prepared_value(const ce_prepared_task* t, int c)
{
    if (t->override != NULL) return t->override[c];
    const float raw = (float)((t->completed != NULL && t->completed[c])
                            ? t->end[c] : t->raw[c]);
    if (t->mode == 1) {
        const float difference = raw - t->base[c];
        const float product = t->gains[c] * difference;
        return t->base[c] + product;
    }
    return raw * t->gains[c];
}

CE_API int ce_prepared_frame_float(int n, const ce_prepared_task* tasks, float* output)
{
    if (n < 0 || (n > 0 && (tasks == NULL || output == NULL))) return -1;
    for (int i = 0; i < n; i++) {
        if (!_ce_prepared_task_valid(&tasks[i])) return -1;
    }
    for (int i = 0; i < n; i++) {
        for (int c = 0; c < 512; c++)
            output[(size_t)i * 512 + c] = _ce_prepared_value(&tasks[i], c);
    }
    return 0;
}

static int _ce_prepared_boost(const ce_prepared_task* t, float value, int c)
{
    if (t->idle == NULL) return 1;
    const float idle = t->idle[c];
    int is_default = floorf(value) == idle;
    if (t->ramp) {
        const float value_delta = value - idle;
        const float raw_delta = (float)t->raw[c] - idle;
        const int crossing = idle > 0.0f && fabsf(value_delta) > 1e-6f
                           && fabsf(raw_delta) > 1.0f;
        is_default = is_default && !crossing;
    }
    return !is_default;
}

static double _ce_prepared_score(const ce_prepared_task* t, float value, int c)
{
    if (!t->active[c]) return -INFINITY;
    const double rate = t->rate <= 1e-9 ? 1.0 : t->rate;
    double score = t->changes_mode == 1 ? t->base_scores[c]
                                       : t->start + (double)t->changes[c] / rate;
    /* np.minimum preserves NaN; fmin would incorrectly discard it. */
    if (!isnan(score)) {
        if (isnan(t->hold_cap)) score = t->hold_cap;
        else if (score > t->hold_cap) score = t->hold_cap;
    }
    score = score + t->tie;
    if (t->boost > 0.0 && _ce_prepared_boost(t, value, c))
        score = score + t->boost;
    if (t->prearrival != NULL && t->prearrival[c])
        score = score + t->preboost;
    return score;
}

static void _ce_prepared_eval_row(const ce_prepared_task* task,
                                  float* data_f, uint8_t* data, double* scores)
{
    for (int c = 0; c < 512; c++) {
        float value = _ce_prepared_value(task, c);
        if (value <= 0.0f) value = 0.0f;
        else if (value > 255.0f) value = 255.0f;
        data_f[c] = value;
        data[c] = isnan(value) ? 0 : (uint8_t)value;
        scores[c] = _ce_prepared_score(task, value, c);
    }
}

CE_API int ce_prepared_frame_eval(int n, const ce_prepared_task* tasks,
                                 float* data_f, uint8_t* data, double* scores)
{
    if (n < 0) return -1;
    if (n == 0) return 0;
    if (tasks == NULL || data_f == NULL || data == NULL || scores == NULL) return -1;
    for (int i = 0; i < n; i++) {
        if (!_ce_prepared_task_valid(&tasks[i])) return -1;
    }
    for (int i = 0; i < n; i++) {
        const size_t offset = (size_t)i * 512;
        _ce_prepared_eval_row(&tasks[i], data_f + offset, data + offset, scores + offset);
    }
    return 0;
}

static int _ce_prepared_csr_valid(int rows, int groups, int nflat,
                                  const int* offsets, const int* flat)
{
    if (rows < 0 || groups < 0 || nflat < 0) return 0;
    if (offsets == NULL || offsets[0] != 0 || offsets[groups] != nflat) return 0;
    for (int g = 0; g < groups; g++) {
        if (offsets[g] < 0 || offsets[g] > offsets[g + 1]) return 0;
    }
    if (nflat > 0 && flat == NULL) return 0;
    for (int i = 0; i < nflat; i++) {
        if (flat[i] < 0 || flat[i] >= rows) return 0;
    }
    return 1;
}

static int _ce_prepared_max_group(int groups, const int* offsets)
{
    int max_rows = 0;
    for (int g = 0; g < groups; g++) {
        const int n = offsets[g + 1] - offsets[g];
        if (n > max_rows) max_rows = n;
    }
    return max_rows;
}

static void _ce_prepared_winner(int n, const uint8_t* effective, int* winner)
{
    for (int c = 0; c < 512; c++) {
        winner[c] = -1;
        for (int i = 0; i < n; i++) {
            if (effective[(size_t)i * 512 + c]) {
                winner[c] = i;
                break;
            }
        }
    }
}

static void _ce_prepared_mix(int n, const int* indices, const float* anchors,
                             const uint8_t* effective, const uint8_t* data_eff,
                             const int* counts, float gain, uint8_t* scratch,
                             uint8_t* out, uint8_t* out_active)
{
    uint8_t* values = scratch;
    uint8_t* active = scratch + (size_t)n * 512;
    float* layer_anchors = (float*)(scratch + (size_t)n * 1024);
    int layers = 0;
    for (int i = 0; i < n; i++) {
        if (counts[i] <= 0) continue;
        const size_t dst = (size_t)layers * 512;
        memcpy(values + dst, data_eff + (size_t)i * 512, 512);
        memcpy(active + dst, effective + (size_t)i * 512, 512);
        memcpy(layer_anchors + dst, anchors + (size_t)indices[i] * 512, 512 * sizeof(float));
        layers++;
    }
    if (layers == 1) {
        memcpy(out, values, 512);
        memcpy(out_active, active, 512);
    } else if (layers > 1) {
#ifdef CE_AVX2
        ce_mix_clips_u8_avx2(values, active, layer_anchors, layers, gain, out, out_active);
#else
        ce_mix_clips_u8(values, active, layer_anchors, layers, gain, out, out_active);
#endif
    }
}

CE_API int ce_prepared_frame_resolve_mix(
    int rows, const uint8_t* data, const uint8_t* active,
    const float* anchors, const double* scores, int groups,
    const int* offsets, const int* flat, int nflat, double mod_gain,
    uint8_t* effective, uint8_t* data_eff, int* counts, int* winner,
    uint8_t* out, uint8_t* out_active)
{
    if (!_ce_prepared_csr_valid(rows, groups, nflat, offsets, flat)) return -1;
    if (nflat > 0 && (data == NULL || active == NULL || anchors == NULL
                    || scores == NULL || effective == NULL || data_eff == NULL
                    || counts == NULL)) return -1;
    if (groups > 0 && (winner == NULL || out == NULL || out_active == NULL)) return -1;
    const int max_rows = _ce_prepared_max_group(groups, offsets);
    uint8_t* scratch = NULL;
    if (max_rows > 0) {
        scratch = (uint8_t*)malloc((size_t)max_rows * 512 * (2 + sizeof(float)));
        if (scratch == NULL) return -2;
    }
    for (int g = 0; g < groups; g++) {
        const int first = offsets[g], n = offsets[g + 1] - first;
        const size_t goff = (size_t)g * 512, foff = (size_t)first * 512;
        memset(out + goff, 0, 512);
        memset(out_active + goff, 0, 512);
        if (n == 0) {
            for (int c = 0; c < 512; c++) winner[goff + c] = -1;
            continue;
        }
#ifdef CE_AVX2
        ce_resolve_many_group_avx2(flat + first, n, data, active, scores,
                                  NULL, NULL, effective + foff, data_eff + foff, counts + first);
#else
        ce_resolve_many_group_scalar(flat + first, n, data, active, scores,
                                    NULL, NULL, effective + foff, data_eff + foff, counts + first);
#endif
        _ce_prepared_winner(n, effective + foff, winner + goff);
        _ce_prepared_mix(n, flat + first, anchors, effective + foff, data_eff + foff,
                         counts + first, (float)mod_gain, scratch, out + goff, out_active + goff);
    }
    free(scratch);
    return 0;
}
