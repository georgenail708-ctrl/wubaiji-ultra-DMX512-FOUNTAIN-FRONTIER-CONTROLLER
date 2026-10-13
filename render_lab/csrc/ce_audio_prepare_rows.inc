/* Original consumed error/drop order, complete child skipping and bit output. */
static int cap_clip_input(cap_batch *batch, const ce_audio_prepare_step_v1 *step, double *start, double *end)
{
    int status = cap_input(batch, step->input[0], 0.0, start);
    if (status) return status;
    if (batch->status[step->input[0]] == 1) return 2;
    status = cap_input(batch, step->input[1], 0.0, end);
    if (status) return status;
    return batch->status[step->input[1]] == 1 ? 2 : 0;
}

static int cap_clip(cap_batch *batch, int *position)
{
    int index = *position;
    const ce_audio_prepare_step_v1 *step = batch->step + index;
    if (step->skip_to <= index || step->skip_to > batch->steps) return -1;
    batch->report->clip_rows++;
    double start, end;
    int status = cap_clip_input(batch, step, &start, &end);
    if (status == 1 || status < 0) return status;
    int keep = status != 2 && isfinite(start) && isfinite(end);
    if (keep) { start = start > 0.0 ? start : 0.0; keep = !(end - start < step->fallback); }
    if (!keep) { *position = step->skip_to - 1; return 0; }
    batch->output[index * 4] = _ce_audio_decimal34(start, 4);
    batch->output[index * 4 + 1] = _ce_audio_decimal34(end, 4);
    batch->report->round_values += 2;
    batch->flags[index] = 1;
    return 0;
}

static int cap_point(cap_batch *batch, int index)
{
    const ce_audio_prepare_step_v1 *step = batch->step + index;
    double value[3];
    batch->report->point_rows++;
    for (int field = 0; field < 3; ++field) {
        int status = cap_input(batch, step->input[field], 0.0, value + field);
        if (status) return status;
        value[field] = cap_point_clamp(value[field], field == 2 ? -1.0 : 0.0, 1.0);
    }
    if (isnan(value[0]) || isnan(value[1]) || isnan(value[2])) return 0;
    batch->output[index * 4] = _ce_audio_decimal34(value[0], 4);
    batch->output[index * 4 + 1] = _ce_audio_decimal34(value[1], 4);
    batch->flags[index] = 1;
    batch->report->round_values += 2;
    if (fabs(value[2]) > 1e-9) {
        batch->output[index * 4 + 2] = _ce_audio_decimal34(value[2], 3);
        batch->flags[index] = 3;
        batch->report->round_values++;
    }
    return 0;
}
