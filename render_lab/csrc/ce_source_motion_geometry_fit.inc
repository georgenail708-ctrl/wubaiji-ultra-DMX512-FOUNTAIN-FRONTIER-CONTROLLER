/* Exact public ordered moment/three-by-three fit, rather than RANSAC. */
static int _ce_geo_circle(const double* xy, int n, double* out)
{
    if (n < 3) return 0;
    double sx = 0.0, sy = 0.0, sx2 = 0.0, sy2 = 0.0, sxy = 0.0;
    double sx3 = 0.0, sy3 = 0.0, sx2y = 0.0, sxy2 = 0.0;
    for (int i = 0; i < n; ++i) {
        double x = xy[2 * i], y = xy[2 * i + 1], x2 = x * x, y2 = y * y;
        sx += x; sy += y; sx2 += x2; sy2 += y2; sxy += x * y;
        sx3 += x2 * x; sy3 += y2 * y; sx2y += x2 * y; sxy2 += x * y2;
    }
    double matrix[3][4] = {
        {sx2, sxy, sx, -(sx3 + sxy2)}, {sxy, sy2, sy, -(sx2y + sy3)},
        {sx, sy, (double)n, -(sx2 + sy2)}
    };
    for (int col = 0; col < 3; ++col) {
        int pivot = col;
        for (int r = col + 1; r < 3; ++r)
            if (fabs(matrix[r][col]) > fabs(matrix[pivot][col])) pivot = r;
        if (fabs(matrix[pivot][col]) < 1e-9) return 0;
        if (pivot != col) for (int j = 0; j < 4; ++j) {
            double v = matrix[col][j]; matrix[col][j] = matrix[pivot][j]; matrix[pivot][j] = v;
        }
        double div = matrix[col][col];
        for (int j = col; j < 4; ++j) matrix[col][j] /= div;
        for (int r = 0; r < 3; ++r) {
            if (r == col) continue;
            double factor = matrix[r][col]; if (fabs(factor) <= 1e-12) continue;
            for (int j = col; j < 4; ++j) matrix[r][j] -= factor * matrix[col][j];
        }
    }
    double cx = -matrix[0][3] / 2.0, cy = -matrix[1][3] / 2.0;
    double r2 = cx * cx + cy * cy - matrix[2][3];
    if (r2 <= 1.0) return 0;
    double radius = sqrt(r2);
    double* squares = (double*)malloc((size_t)n * sizeof(double));
    if (!squares) return -2;
    for (int i = 0; i < n; ++i) {
        double error = fabs(ce_py_hypot2(xy[2 * i] - cx, xy[2 * i + 1] - cy) - radius);
        squares[i] = error * error;
    }
    out[0] = cx; out[1] = cy; out[2] = radius;
    out[3] = sqrt(_ce_py_sum_f64(squares, n) / (double)n);
    free(squares); return 1;
}

static int _ce_geo_select_shape(const ce_source_geometry* s, int* selected)
{
    int best_count = -1; double best_average = 999999999.0; *selected = -1;
    double* errors = s->n ? (double*)malloc((size_t)s->n * sizeof(double)) : NULL;
    if (s->n && !errors) return -2;
    for (int shape = 0; shape < s->ns; ++shape) {
        const double* item = s->shapes + 5 * shape;
        double radius = (item[2] + item[3]) * 0.5;
        double tolerance = radius * 0.22; if (!(tolerance > 24.0)) tolerance = 24.0;
        int count = 0;
        for (int i = 0; i < s->n; ++i) {
            double metric[2]; _ce_geo_metric(item, s->xy + 2 * i, metric);
            if (metric[0] <= tolerance) errors[count++] = metric[0];
        }
        double threshold = ceil((double)s->n * 0.45); if (threshold < 3.0) threshold = 3.0;
        if (count < threshold) continue;
        double average = _ce_py_sum_f64(errors, count) / (double)count;
        if (count > best_count || (count == best_count && -average > -best_average)) {
            best_count = count; best_average = average; *selected = shape;
        }
    }
    free(errors); return 0;
}

static int _ce_geo_spec_valid(const ce_source_geometry* s)
{
    return s && _ce_geo_count(s->n, 2 * sizeof(double)) &&
        _ce_geo_count(s->sn, 2 * sizeof(double)) && _ce_geo_count(s->ns, 5 * sizeof(double)) &&
        (!s->n || s->xy) && (!s->sn || s->scope) && (!s->ns || s->shapes);
}

CE_API int ce_source_geometry_fit(const ce_source_geometry* s, double* out)
{
    if (!_ce_geo_spec_valid(s) || !out) return -1;
    memset(out, 0, 8 * sizeof(double)); out[1] = -1.0;
    if (s->n < 3) return 0;
    int selected = -1, status = 0;
    if (s->flags & 1) status = _ce_geo_select_shape(s, &selected);
    if (status) return status;
    if (selected >= 0) {
        out[0] = 1.0; out[1] = selected;
        memcpy(out + 2, s->shapes + 5 * selected, 5 * sizeof(double)); return 0;
    }
    double circle[4]; status = _ce_geo_circle(s->xy, s->n, circle);
    if (status < 0) return status;
    double tolerance = status ? circle[2] * 0.22 : 24.0;
    if (!(tolerance > 24.0)) tolerance = 24.0;
    if (status && !(circle[3] > tolerance)) {
        out[0] = 1.0; out[2] = circle[0]; out[3] = circle[1];
        out[4] = out[5] = circle[2]; return 0;
    }
    if (!(s->flags & 2)) return 0;
    double bounds[4]; _ce_geo_bounds(s->xy, s->n, bounds);
    out[0] = 1.0; out[2] = (bounds[0] + bounds[1]) * 0.5;
    out[3] = (bounds[2] + bounds[3]) * 0.5;
    double sx = (bounds[1] - bounds[0]) * 0.5, sy = (bounds[3] - bounds[2]) * 0.5;
    out[4] = sx > 1.0 ? sx : 1.0; out[5] = sy > 1.0 ? sy : 1.0; out[7] = 1.0;
    return 0;
}
