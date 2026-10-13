/* R215-C06 typed preparation ABI v1. Research candidate only. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <limits.h>

enum { CAP_PROCESSOR=1, CAP_CLAMP=2, CAP_MAX=3, CAP_FREQ=4, CAP_FADE=5,
       CAP_CLIP=6, CAP_POINT=7, CAP_PAIR=8, CAP_ERROR=9,
       CAP_CLIP_SORT=10, CAP_POINT_SORT=11 };

typedef struct {
    int op, input[3], skip_to, first, last, auxiliary;
    double fallback, low, high;
} ce_audio_prepare_step_v1;

typedef struct {
    int abi, struct_size, semantic, error_selector;
    uint64_t processor_values, state_values, clip_rows, point_rows;
    uint64_t round_values, sort_rows;
} ce_audio_prepare_report_v1;

typedef struct {
    int steps, values;
    const ce_audio_prepare_step_v1 *step;
    const double *value;
    const int *status, *errors;
    double *output;
    int *flags, *order, *count;
} ce_audio_prepare_request_v1;

typedef struct {
    int steps, values;
    const ce_audio_prepare_step_v1 *step;
    const double *value;
    const int *status, *errors;
    double *output;
    int *flags, *order, *count;
    ce_audio_prepare_report_v1 *report;
} cap_batch;

typedef struct { int index; double key, secondary; } cap_sort_row;

CE_API int ce_audio_prepare_abi(void) { return 1; }
CE_API int ce_audio_prepare_size(void) { return (int)sizeof(ce_audio_prepare_report_v1); }
CE_API int ce_audio_prepare_step_size(void) { return (int)sizeof(ce_audio_prepare_step_v1); }
CE_API int ce_audio_prepare_request_size(void) { return (int)sizeof(ce_audio_prepare_request_v1); }

static int cap_semantic(cap_batch *batch, int selector)
{
    batch->report->semantic = 1;
    batch->report->error_selector = selector;
    return 1;
}

/* 0 parsed ordinary value; 1 original caught/default; 2 original uncaught.
 * Caller decides whether a caught input discards a row before later input. */
static int cap_input(cap_batch *batch, int index, double fallback, double *value)
{
    if (index < 0 || index >= batch->values) return -1;
    if (batch->status[index] == 2) return cap_semantic(batch, batch->errors[index]);
    if (batch->status[index] != 0 && batch->status[index] != 1) return -1;
    *value = batch->status[index] == 1 ? fallback : batch->value[index];
    return 0;
}

static double cap_point_clamp(double value, double low, double high)
{
    return value < low ? low : value > high ? high : value;
}

static double cap_ordered_clamp(double value, double low, double high)
{
    /* Original min(high,max(low,value)): first operands win NaN/ties. */
    double lower = value > low ? value : low;
    return lower < high ? lower : high;
}
