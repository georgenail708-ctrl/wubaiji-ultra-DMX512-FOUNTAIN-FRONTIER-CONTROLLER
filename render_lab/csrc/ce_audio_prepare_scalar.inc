/* Ordinary scalar defaults/clamps/frequency/fades; include after decimal34. */
static int cap_scalar(cap_batch *batch, int index)
{
    const ce_audio_prepare_step_v1 *step = batch->step + index;
    double value;
    int status = cap_input(batch, step->input[0], step->fallback, &value);
    if (status) return status;
    if (step->op == CAP_PROCESSOR) {
        if (!isfinite(value)) value = step->fallback;
        value = cap_ordered_clamp(value, step->low, step->high);
        if (step->auxiliary) value = (double)ce_round_half_even(value);
        batch->report->processor_values++;
    } else if (step->op == CAP_CLAMP) {
        value = step->low == 0.0 && step->high == 1.0 ? ca_clamp(value) : cap_ordered_clamp(value, step->low, step->high);
        batch->report->state_values++;
    } else if (step->op == CAP_MAX) {
        value = value > 0.0 ? value : 0.0;
        batch->report->state_values++;
    } else if (step->op == CAP_FREQ) {
        value = value >= 0.0 ? value : step->fallback;
    } else if (step->op == CAP_FADE) {
        if (value < 0.0) value = step->fallback;
        else { value = _ce_audio_decimal34(value, 4); batch->report->round_values++; }
    } else return -1;
    batch->output[index * 4] = value;
    batch->flags[index] = 1;
    return 0;
}

static int cap_pair(cap_batch *batch, int index)
{
    const ce_audio_prepare_step_v1 *step = batch->step + index;
    if (step->first < 0 || step->last < 0 || step->first >= index || step->last >= index) return -1;
    double first = batch->output[step->first * 4], last = batch->output[step->last * 4];
    if (last < first) { double temporary = first; first = last; last = temporary; }
    batch->output[index * 4] = first;
    batch->output[index * 4 + 1] = last;
    batch->flags[index] = 1;
    batch->report->sort_rows += 2;
    return 0;
}
