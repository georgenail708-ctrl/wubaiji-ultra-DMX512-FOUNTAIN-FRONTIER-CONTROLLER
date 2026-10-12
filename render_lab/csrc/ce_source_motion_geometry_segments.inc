CE_API int ce_source_geometry_assign(const ce_source_geometry* s, const uint8_t* used, int* labels)
{
    if (!_ce_geo_spec_valid(s) || (s->n && !labels)) return -1;
    for (int i = 0; i < s->n; ++i) {
        labels[i] = -1;
        if (used && used[i]) continue;
        int best = -1; double best_error = 999999999.0;
        for (int shape = 0; shape < s->ns; ++shape) {
            double metric[2]; _ce_geo_metric(s->shapes + 5*shape, s->xy + 2*i, metric);
            if (metric[0] < best_error) { best = shape; best_error = metric[0]; }
        }
        if (best < 0) continue;
        const double* shape = s->shapes + 5*best;
        double radius = (shape[2] + shape[3]) * 0.5;
        double tolerance = radius * (s->ns > 1 ? 0.15 : 0.22);
        double minimum = s->ns > 1 ? 16.0 : 24.0;
        if (!(tolerance > minimum)) tolerance = minimum;
        if (best_error <= tolerance) labels[i] = best;
    }
    return 0;
}

static void _ce_geo_inferred_append(int start, int n, const double* fit, int* offsets,
                                    double* centers, int* count)
{
    offsets[2 * *count] = start; offsets[2 * *count + 1] = start + n;
    centers[2 * *count] = fit ? fit[0] : 0.0;
    centers[2 * *count + 1] = fit ? fit[1] : 0.0;
    ++*count;
}

CE_API int ce_source_geometry_infer(const ce_source_geometry* s, int* offsets,
                                   double* centers, int* count)
{
    if (!_ce_geo_spec_valid(s) || !count || !offsets || !centers) return -1;
    *count = 0; offsets[0] = 0;
    if (s->n < 10) return 0;
    double bounds[4]; _ce_geo_bounds(s->xy, s->n, bounds);
    double span = bounds[1] - bounds[0], sy = bounds[3] - bounds[2];
    if (sy > span) span = sy; if (!(span > 1.0)) span = 1.0;
    double tolerance = span * 0.035; if (!(tolerance > 18.0)) tolerance = 18.0;
    int minimum = s->n < 24 ? 5 : 6, start = 0;
    for (int i = 0; i < s->n; ++i) {
        int n = i - start + 1; double fit[4];
        int status = _ce_geo_circle(s->xy + 2*start, n, fit);
        if (status < 0) return status;
        double threshold = status ? fit[2] * 0.10 : tolerance;
        if (!(threshold > tolerance)) threshold = tolerance;
        if (i > start && n >= minimum && status && fit[3] > threshold) {
            if (n - 1 >= minimum) {
                double previous[4]; status = _ce_geo_circle(s->xy + 2*start, n - 1, previous);
                if (status < 0) return status;
                _ce_geo_inferred_append(start, n - 1, status ? previous : NULL,
                    offsets, centers, count);
            }
            start = i;
        }
    }
    int n = s->n - start;
    if (n >= minimum) {
        double fit[4]; int status = _ce_geo_circle(s->xy + 2*start, n, fit);
        if (status < 0) return status;
        double threshold = status ? fit[2] * 0.10 : tolerance;
        if (!(threshold > tolerance)) threshold = tolerance;
        _ce_geo_inferred_append(start, n, status && fit[3] <= threshold ? fit : NULL,
            offsets, centers, count);
    }
    if (*count <= 1) { *count = 0; offsets[0] = 0; }
    return 0;
}

/* Integer group arithmetic is native too; metadata only indexes returned bounds. */
CE_API int ce_source_geometry_groups(int mode, int count, const int* input, int* output)
{
    if (!_ce_geo_count(count, 3 * sizeof(int)) || (count && (!input || !output))) return -1;
    if (mode == 0) {
        int groups = input[0], segments = input[1];
        if (groups < 1 || segments < 1 || count != segments) return -1;
        int start = 0;
        for (int i = 0; i < segments; ++i) {
            int share = groups < segments ? 1 : groups / segments + (i < groups % segments);
            output[2*i] = share; output[2*i+1] = start;
            if (input[2+i]) start += share;
        }
        return 0;
    }
    if (mode == 1) {
        int n = input[0], groups = input[1], base = input[2];
        if (n < 0 || groups < 1 || count != groups) return -1;
        int width = n / groups + (n % groups != 0);
        for (int i = 0; i < groups; ++i) {
            int64_t start = (int64_t)i * width, end = start + width;
            if ((int64_t)base+i > INT_MAX || base < 0) return -1;
            output[3*i] = start > n ? n : (int)start;
            output[3*i+1] = end > n ? n : (int)end;
            output[3*i+2] = base+i;
        }
        return 0;
    }
    if (mode == 2) {
        int groups = input[0]; if (groups < 1) return -1;
        int width = count / groups + (count % groups != 0);
        for (int i = 0; i < count; ++i) {
            int group = width ? i / width : 0;
            output[i] = group < groups ? group : groups - 1;
        }
        return 0;
    }
    return -1;
}
