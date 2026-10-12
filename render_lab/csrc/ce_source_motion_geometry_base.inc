/* Source geometry: metadata stays public; numeric work is synchronous here. */
typedef struct {
    const double *xy, *scope, *shapes, *reference;
    int n, sn, ns, mode, flags, clockwise, start_valid;
    double start;
} ce_source_geometry;

static int _ce_geo_count(int n, size_t width)
{
    return n >= 0 && (size_t)n <= SIZE_MAX / width;
}

static double _ce_geo_mod(double value)
{
    double result = fmod(value, 6.283185307179586476925286766559);
    if (result < 0.0) result += 6.283185307179586476925286766559;
    if (result == 0.0) result = 0.0;
    return result;
}

static void _ce_geo_rotate(double x, double y, double angle, double* out)
{
    double rad = angle * (3.1415926535897932384626433832795 / 180.0);
    double c = cos(rad), s = sin(rad);
    out[0] = x * c - y * s;
    out[1] = x * s + y * c;
}

static void _ce_geo_metric(const double* shape, const double* xy, double* out)
{
    double rx = shape[2] > 1.0 ? shape[2] : 1.0;
    double ry = shape[3] > 1.0 ? shape[3] : 1.0;
    double rotated[2], dx = xy[0] - shape[0], dy = xy[1] - shape[1];
    if (fabs(shape[4]) > 0.001) {
        _ce_geo_rotate(dx, dy, -shape[4], rotated);
        dx = rotated[0]; dy = rotated[1];
    }
    double nx = dx / rx, ny = dy / ry;
    out[0] = fabs(ce_py_hypot2(nx, ny) - 1.0) * ((rx + ry) * 0.5);
    out[1] = _ce_geo_mod(atan2(ny, nx));
}

static int _ce_geo_axis(const double* values, int n, int stride, int* result)
{
    *result = 0;
    if (!n) return 0;
    _ce_fade_order_item* sorted = (_ce_fade_order_item*)malloc((size_t)n * sizeof(*sorted));
    _ce_fade_order_item* tmp = (_ce_fade_order_item*)malloc((size_t)n * sizeof(*tmp));
    if (!sorted || !tmp) { free(sorted); free(tmp); return -2; }
    for (int i = 0; i < n; ++i) {
        sorted[i].key = values[i * stride]; sorted[i].tie = 0.0; sorted[i].idx = i;
    }
    _ce_fade_order_sort3(sorted, tmp, n); free(tmp);
    double span = sorted[n - 1].key - sorted[0].key;
    double tol = span * 0.025; if (!(tol > 1e-6)) tol = 1e-6;
    double previous = sorted[0].key; *result = 1;
    for (int i = 1; i < n; ++i) if (fabs(sorted[i].key - previous) > tol) {
        ++*result; previous = sorted[i].key;
    }
    free(sorted); return 0;
}

static int _ce_geo_matrix(const double* xy, int n, int* result)
{
    int xc = 0, yc = 0; *result = 0;
    if (n < 4) return 0;
    int status = _ce_geo_axis(xy, n, 2, &xc);
    if (!status) status = _ce_geo_axis(xy + 1, n, 2, &yc);
    if (status) return status;
    if (xc >= 3 && yc >= 3)
        *result = (double)n / ((double)xc * yc) >= 0.80;
    return 0;
}

static void _ce_geo_bounds(const double* xy, int n, double* out)
{
    out[0] = out[1] = xy[0]; out[2] = out[3] = xy[1];
    for (int i = 1; i < n; ++i) {
        double x = xy[2 * i], y = xy[2 * i + 1];
        if (x < out[0]) out[0] = x; if (x > out[1]) out[1] = x;
        if (y < out[2]) out[2] = y; if (y > out[3]) out[3] = y;
    }
}

static int _ce_geo_mean(const double* xy, int n, double* out)
{
    out[0] = out[1] = 0.0;
    if (!n) return 0;
    double* values = (double*)malloc((size_t)n * sizeof(double));
    if (!values) return -2;
    for (int axis = 0; axis < 2; ++axis) {
        for (int i = 0; i < n; ++i) values[i] = xy[2 * i + axis];
        out[axis] = _ce_py_sum_f64(values, n) / (double)n;
    }
    free(values); return 0;
}

static double _ce_geo_ring(const double* xy, const double* center, const double* span)
{
    double sx = span[0] * 0.5, sy = span[1] * 0.5;
    if (!(sx > 1e-6)) sx = 1e-6; if (!(sy > 1e-6)) sy = 1e-6;
    double nx = fabs(xy[0] - center[0]) / sx;
    double ny = fabs(xy[1] - center[1]) / sy;
    return ny > nx ? ny : nx;
}

CE_API int ce_source_geometry_primitive(int op, int n, const double* input, double* out)
{
    if (!_ce_geo_count(n, 2 * sizeof(double)) || !out || (n && !input)) return -1;
    int value = 0, status = 0;
    switch (op) {
    case 0: if (n != 3) return -1; _ce_geo_rotate(input[0], input[1], input[2], out); break;
    case 1: status = _ce_geo_axis(input, n, 1, &value); out[0] = value; break;
    case 2: status = _ce_geo_matrix(input, n, &value); out[0] = value; break;
    case 3: if (n != 6) return -1; out[0] = _ce_geo_ring(input, input + 2, input + 4); break;
    case 4: status = _ce_geo_mean(input, n, out); break;
    default: return -1;
    }
    return status;
}

CE_API int ce_source_geometry_metrics(int n, const double* xy, const double* shape, double* out)
{
    if (!_ce_geo_count(n, 2 * sizeof(double)) || (n && (!xy || !out)) || !shape) return -1;
    for (int i = 0; i < n; ++i) _ce_geo_metric(shape, xy + 2 * i, out + 2 * i);
    return 0;
}

CE_API int ce_source_geometry_abi(void) { return 1; }
CE_API int ce_source_geometry_size(void) { return (int)sizeof(ce_source_geometry); }
