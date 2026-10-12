/* Existing sequential physical/virtual rotation and Python ties-to-even writes. */
typedef struct {
    int x, y, xf, yf, z, zf, enabled, cached, command;
    double cosine, sine, sign, angle, base, zmin, zmax, zangle, zcommand;
} ce_domain_xy_task;

static double _ce_dom_clamp(double value)
{
    if (isnan(value) || value > 255.0) return 255.0;
    return value < 0.0 ? 0.0 : value;
}

static int _ce_dom_round(double value)
{
    const double low = floor(value), fraction = value - low;
    return (int)(low + (fraction > 0.5 || (fraction == 0.5 && fmod(low, 2.0) != 0.0)));
}

static double _ce_dom_read(const uint8_t* data, int coarse, int fine)
{
    return fine < 0 ? (double)data[coarse]
                   : ((double)data[coarse] * 256.0 + data[fine]) / 256.0;
}

static void _ce_dom_write(uint8_t* data, int coarse, int fine, double value)
{
    const double logical = _ce_dom_clamp(value);
    if (fine < 0) { data[coarse] = (uint8_t)_ce_dom_round(logical); return; }
    const int pair = logical >= 255.0 ? 65535 : _ce_dom_round(logical * 256.0);
    data[coarse] = (uint8_t)(pair >> 8);
    data[fine] = (uint8_t)(pair & 255);
}

static int _ce_dom_axis_valid(int value, int optional)
{
    return value >= (optional ? -1 : 0) && value < 512;
}

static int _ce_dom_xy_valid(const ce_domain_xy_task* t)
{
    return !t->enabled || (_ce_dom_axis_valid(t->x, 0) && _ce_dom_axis_valid(t->y, 0) &&
        _ce_dom_axis_valid(t->xf, 1) && _ce_dom_axis_valid(t->yf, 1) &&
        _ce_dom_axis_valid(t->z, 1) && _ce_dom_axis_valid(t->zf, 1));
}

static void _ce_dom_z(uint8_t* out, const ce_domain_xy_task* t)
{
    if (t->z < 0) return;
    const double current = _ce_dom_read(out, t->z, t->zf);
    if (!t->command && !(fabs(current - 128.0) < 1e-9 || (int)current == 0)) return;
    double angle = (t->command ? t->zcommand : t->zangle) - t->base;
    if (t->base >= -180.0 && t->base <= 180.0 && isfinite(angle) &&
        isfinite(t->zmin) && isfinite(t->zmax) && t->zmin <= t->zmax &&
        !(angle >= t->zmin && angle <= t->zmax)) {
        const double first = ceil((t->zmin - angle) / 360.0);
        const double last = floor((t->zmax - angle) / 360.0);
        if (first <= last) angle += 360.0 * fmax(first, fmin(0.0, last));
    }
    double span = t->zmax - t->zmin;
    if (!(span > 1e-9)) span = 1e-9;
    _ce_dom_write(out, t->z, t->zf, ((angle - t->zmin) / span) * 255.0);
}

CE_API int ce_domain_xy(int n, const ce_domain_xy_task* tasks, const uint8_t* raw,
    int inverse, uint8_t* output)
{
    if (n < 0 || (size_t)n > SIZE_MAX / sizeof(ce_domain_xy_task) || !raw || !output || (n && !tasks) ||
        (inverse != 0 && inverse != 1)) return -1;
    for (int i = 0; i < n; i++) if (!_ce_dom_xy_valid(&tasks[i])) return -1;
    memmove(output, raw, 512);
    for (int i = 0; i < n; i++) {
        const ce_domain_xy_task* t = &tasks[i];
        if (!t->enabled) continue;
        double cosine = t->cosine, sine = t->sine;
        if (t->cached != 1) {
            if (!isfinite(t->base) || !isfinite(t->angle)) continue;
            const double rad = (t->cached == 2 ? t->base - t->angle : t->angle) * (3.14159265358979323846 / 180.0);
            cosine = cos(rad); sine = sin(rad);
        }
        const double x = _ce_dom_read(output, t->x, t->xf);
        const double y = t->x == t->y ? 128.0 : _ce_dom_read(output, t->y, t->yf);
        const double dx = x - 128.0, dy = y - 128.0;
        if (hypot(dx, dy) < 1.0 || ((int)x == 0 && ((int)y == 0 || t->x == t->y))) {
            if (!inverse) {
                _ce_dom_write(output, t->x, t->xf, 128.0);
                if (t->x != t->y) _ce_dom_write(output, t->y, t->yf, 128.0);
                _ce_dom_z(output, t);
            }
            continue;
        }
        const double next_x = inverse ? dx * cosine - dy * t->sign * sine : dx * cosine + dy * sine;
        const double next_y = inverse ? dx * sine + dy * t->sign * cosine : t->sign * (-dx * sine + dy * cosine);
        _ce_dom_write(output, t->x, t->xf, 128.0 + next_x);
        if (t->x != t->y) _ce_dom_write(output, t->y, t->yf, 128.0 + next_y);
        if (!inverse) _ce_dom_z(output, t);
    }
    return 0;
}
