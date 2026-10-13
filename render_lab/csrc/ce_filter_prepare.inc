/* R215-C02 ordinary filter parameter/span preparation.
 * Original clamp and positive-span/median operation order is preserved.
 * Reference NumPy be93fe2960dbf49b4647f5783c66d967fb2c65b5 _median/_mean.
 * NumPy BSD-3 notice: licenses/NumPy-2.4.4-BSD-3-Clause.txt.
 */
#include <math.h>
#include <stdlib.h>
#include <stdint.h>
#include <limits.h>

typedef struct {
    int abi, struct_size, semantic, semantic_selector, warning_axis;
    uint64_t parameters, span_inputs, span_values, median_values, default_axes;
} ce_filter_prepare_report_v1;

CE_API int ce_filter_prepare_abi(void) { return 1; }
CE_API int ce_filter_prepare_size(void) { return (int)sizeof(ce_filter_prepare_report_v1); }

/* Input status: 0 successful ordinary float conversion; 1 original caught
 * TypeError/ValueError/IndexError-default; 2 original uncaught conversion error.
 * Callers retain exact error objects/messages; C selects only first consumed.
 */
static int _ce_filter_prepare_report_ok(ce_filter_prepare_report_v1* report)
{
    return report && report->abi == 1
        && report->struct_size == (int)sizeof(*report);
}

static double _ce_filter_prepare_clamp(double value)
{
    /* Python min(1.0, value), then max(0.0, selected): first argument wins ties
     * and unordered NaN comparisons. In particular NaN -> 1, -0 -> +0. */
    double upper = value < 1.0 ? value : 1.0;
    return upper > 0.0 ? upper : 0.0;
}

static double _ce_filter_prepare_number(double value, int status, double fallback)
{
    return status == 1 ? fallback : value;
}

CE_API int ce_filter_prepare_params_v1(int n, const double* input,
    const int* status, double* output, uint8_t* active,
    ce_filter_prepare_report_v1* report)
{
    if (!_ce_filter_prepare_report_ok(report) || n < 0
        || n > INT_MAX / 3 || (n && (!input || !status || !output || !active))) return -1;
    report->semantic = 0; report->semantic_selector = -1; report->warning_axis = -1;
    report->parameters = 0;
    for (int row = 0; row < n; ++row) {
        const int base = row * 3;
        for (int field = 0; field < 3; ++field) {
            if (status[base + field] == 2) {
                report->semantic = 1;
                report->semantic_selector = base + field;
                return 0;
            }
            if (status[base + field] < 0 || status[base + field] > 2) return -1;
        }
        double motor = _ce_filter_prepare_clamp(
            _ce_filter_prepare_number(input[base], status[base], 1.0));
        double dps = _ce_filter_prepare_number(input[base + 1], status[base + 1], 0.0);
        dps = dps > 0.0 ? dps : 0.0;
        double pump = _ce_filter_prepare_clamp(
            _ce_filter_prepare_number(input[base + 2], status[base + 2], 1.0));
        output[base] = motor; output[base + 1] = dps; output[base + 2] = pump;
        active[row] = motor < 1.0 - 1e-6 || dps > 1e-6 || pump < 1.0 - 1e-6;
        report->parameters += 3;
    }
    return 0;
}

static int _ce_filter_prepare_compare(const void* left, const void* right)
{
    const double a = *(const double*)left, b = *(const double*)right;
    return a < b ? -1 : a > b ? 1 : 0;
}

/* Rows are original fixture/axis visit order. Pair values have already had only
 * side-effect-free builtin conversion serialized; status uses right-then-left
 * consumed error selector (2*row+1 right, 2*row left). 1 skips a caught range.
 * The metadata order of the three original MOTOR_COARSE_CHANNELS is supplied.
 * C derives actual first-positive-bucket insertion order and returns it, so the
 * host never computes a span merely to discover original warning order.
 */
CE_API int ce_filter_prepare_spans_v1(int n, const int* axis,
    const double* low, const double* high, const int* status_low,
    const int* status_high, const int* metadata_order, int* result_order,
    double* output, uint8_t* overflow, ce_filter_prepare_report_v1* report)
{
    if (!_ce_filter_prepare_report_ok(report) || n < 0
        || n > INT_MAX / 2 || !metadata_order || !result_order || !output || !overflow
        || (n && (!axis || !low || !high || !status_low || !status_high))) return -1;
    report->semantic = 0; report->semantic_selector = -1; report->warning_axis = -1;
    report->span_inputs = report->span_values = report->median_values = report->default_axes = 0;
    double* spans = n ? (double*)malloc((size_t)n * sizeof(double)) : NULL;
    if (n && !spans) return -2;
    int counts[3] = {0, 0, 0}, order_count = 0;
    for (int row = 0; row < n; ++row) {
        if (axis[row] < 0 || axis[row] > 2) { free(spans); return -1; }
        ++report->span_inputs;
        if (status_high[row] == 1) { spans[row] = 0.0; continue; }
        if (status_high[row] == 2 || status_low[row] == 2) {
            report->semantic = 1;
            report->semantic_selector = 2 * row + (status_high[row] == 2);
            free(spans); return 0;
        }
        if (status_low[row] == 1) { spans[row] = 0.0; continue; }
        if (status_high[row] != 0 || status_low[row] != 0) { free(spans); return -1; }
        double span = fabs(high[row] - low[row]);
        spans[row] = span > 1.0 ? span : 0.0;
        if (spans[row] > 0.0) {
            if (!counts[axis[row]]) result_order[order_count++] = axis[row];
            ++counts[axis[row]]; ++report->span_values;
        }
    }
    int metadata_seen = 0;
    for (int step = 0; step < 3; ++step) {
        const int group = metadata_order[step];
        if (group < 0 || group > 2 || (metadata_seen & (1 << group))) { free(spans); return -1; }
        metadata_seen |= 1 << group;
        if (!counts[group]) result_order[order_count++] = group;
    }
    double* selected = n ? (double*)malloc((size_t)n * sizeof(double)) : NULL;
    if (n && !selected) { free(spans); return -2; }
    int seen = 0;
    for (int step = 0; step < 3; ++step) {
        const int group = result_order[step];
        if (group < 0 || group > 2 || (seen & (1 << group))) {
            free(spans); free(selected); return -1;
        }
        seen |= 1 << group;
        overflow[group] = 0;
        if (!counts[group]) { output[group] = 360.0; ++report->default_axes; continue; }
        int count = 0;
        for (int row = 0; row < n; ++row)
            if (axis[row] == group && spans[row] > 0.0) selected[count++] = spans[row];
        qsort(selected, (size_t)count, sizeof(double), _ce_filter_prepare_compare);
        const int middle = count / 2;
        if (count & 1) output[group] = selected[middle];
        else {
            const double a = selected[middle - 1], b = selected[middle];
            const double sum = a + b;
            overflow[group] = isfinite(a) && isfinite(b) && isinf(sum);
            output[group] = sum / 2.0;
        }
        report->median_values += (count & 1) ? 1 : 2;
    }
    free(spans); free(selected);
    return 0;
}
