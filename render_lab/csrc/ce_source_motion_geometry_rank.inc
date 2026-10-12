static int _ce_geo_sorted(_ce_fade_order_item* items, int n, int* order, float* ranks)
{
    _ce_fade_order_item* tmp = n ? (_ce_fade_order_item*)malloc((size_t)n * sizeof(*tmp)) : NULL;
    if (n && !tmp) return -2;
    _ce_fade_order_sort3(items, tmp, n);
    double denominator = n > 1 ? (double)(n - 1) : 1.0;
    for (int i = 0; i < n; ++i) {
        if (order) order[i] = items[i].idx;
        if (ranks) ranks[items[i].idx] = (float)((double)i / denominator);
    }
    free(tmp); return 0;
}

static int _ce_geo_centroid_keys(const ce_source_geometry* s, _ce_fade_order_item* items,
                                int check_degenerate, int* available)
{
    const double* scope = s->sn ? s->scope : s->xy;
    int sn = s->sn ? s->sn : s->n;
    if (sn < 2) { *available = 0; return 0; }
    double center[2]; int status = _ce_geo_mean(scope, sn, center);
    if (status) return status;
    int same = 1;
    for (int i = 0; i < s->n; ++i)
        if (!(fabs(s->xy[2*i] - center[0]) <= 1e-6 &&
              fabs(s->xy[2*i+1] - center[1]) <= 1e-6)) same = 0;
    if (same && check_degenerate) { *available = 0; return 0; }
    int matrix = 0; status = _ce_geo_matrix(s->xy, s->n, &matrix);
    if (status) return status;
    double bounds[4], span[2]; _ce_geo_bounds(s->xy, s->n, bounds);
    span[0] = bounds[1] - bounds[0]; span[1] = bounds[3] - bounds[2];
    double start = _ce_geo_mod(atan2(s->xy[1] - center[1], s->xy[0] - center[0]));
    for (int i = 0; i < s->n; ++i) {
        double x = s->xy[2*i] - center[0], y = s->xy[2*i+1] - center[1];
        double angle = _ce_geo_mod(atan2(y, x));
        double delta = _ce_geo_mod(s->clockwise ? angle - start : start - angle);
        items[i].idx = i;
        items[i].key = matrix ? -_ce_geo_ring(s->xy + 2*i, center, span) : delta;
        items[i].tie = matrix ? delta : -ce_py_hypot2(x, y);
    }
    return 0;
}

CE_API int ce_source_geometry_rank(const ce_source_geometry* s, float* out, int* available)
{
    if (!_ce_geo_spec_valid(s) || !available || (s->n && !out) || s->mode < 0 || s->mode > 2) return -1;
    *available = 1;
    if (!s->n) return 0;
    if (s->n == 1) { out[0] = 0.5f; return 0; }
    _ce_fade_order_item* items = (_ce_fade_order_item*)malloc((size_t)s->n * sizeof(*items));
    if (!items) return -2;
    int status = 0;
    if (s->mode) status = _ce_geo_centroid_keys(s, items, s->mode == 1, available);
    else if (!s->reference) *available = 0;
    else {
        double first[2]; _ce_geo_metric(s->reference, s->xy, first);
        double start = s->start_valid ? _ce_geo_mod(s->start) : first[1];
        for (int i = 0; i < s->n; ++i) {
            double metric[2]; _ce_geo_metric(s->reference, s->xy + 2*i, metric);
            items[i].idx = i; items[i].tie = 0.0;
            items[i].key = _ce_geo_mod(s->clockwise ? metric[1] - start : start - metric[1]);
        }
    }
    if (!status && *available) status = _ce_geo_sorted(items, s->n, NULL, out);
    free(items); return status;
}

CE_API int ce_source_geometry_slots(const ce_source_geometry* s, int* out)
{
    if (!_ce_geo_spec_valid(s) || (s->n && !out) || s->mode < 0 || s->mode > 2) return -1;
    if (!s->n) return 0;
    _ce_fade_order_item* items = (_ce_fade_order_item*)malloc((size_t)s->n * sizeof(*items));
    if (!items) return -2;
    int status = 0, matrix = 0, available = 1;
    if (!s->mode) status = _ce_geo_matrix(s->xy, s->n, &matrix);
    int row_first = matrix || s->mode == 2;
    if (!status && (row_first || s->mode == 1)) {
        for (int i = 0; i < s->n; ++i) {
            items[i].idx = i; items[i].key = s->xy[2*i + (row_first ? 1 : 0)];
            items[i].tie = s->xy[2*i + (row_first ? 0 : 1)];
        }
    } else if (!status && s->reference) {
        double first[2]; _ce_geo_metric(s->reference, s->xy, first);
        for (int i = 0; i < s->n; ++i) {
            double metric[2]; _ce_geo_metric(s->reference, s->xy + 2*i, metric);
            items[i].idx = i; items[i].key = _ce_geo_mod(metric[1] - first[1]);
            items[i].tie = metric[0];
        }
    } else if (!status) {
        status = _ce_geo_centroid_keys(s, items, 1, &available);
        if (!available) for (int i = 0; i < s->n; ++i) {
            items[i].idx = i; items[i].key = items[i].tie = 0.0;
        }
    }
    if (!status) status = _ce_geo_sorted(items, s->n, out, NULL);
    free(items); return status;
}
