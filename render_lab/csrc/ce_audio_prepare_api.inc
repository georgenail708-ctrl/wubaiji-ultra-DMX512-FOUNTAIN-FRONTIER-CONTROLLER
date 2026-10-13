/* Include after current CE audio common/envelope/chain/scope and decimal34. */
static int cap_dispatch(cap_batch *batch, int *position)
{
    int index = *position, op = batch->step[index].op;
    if (op >= CAP_PROCESSOR && op <= CAP_FADE) return cap_scalar(batch, index);
    if (op == CAP_CLIP) return cap_clip(batch, position);
    if (op == CAP_POINT) return cap_point(batch, index);
    if (op == CAP_PAIR) return cap_pair(batch, index);
    if (op == CAP_ERROR) return cap_semantic(batch, batch->step[index].auxiliary);
    if (op == CAP_CLIP_SORT || op == CAP_POINT_SORT) return cap_sort(batch, index);
    return -1;
}

static int cap_execute(cap_batch *batch)
{
    if (!batch->report || batch->report->abi != 1 || batch->report->struct_size != (int)sizeof(*batch->report)) return -1;
    if (batch->steps < 0 || batch->values < 0 || batch->steps > INT_MAX / 4) return -1;
    if (batch->steps && (!batch->step || !batch->output || !batch->flags || !batch->order || !batch->count)) return -1;
    if (batch->values && (!batch->value || !batch->status || !batch->errors)) return -1;
    ce_audio_prepare_report_v1 *report = batch->report;
    memset(report, 0, sizeof(*report)); report->abi = 1; report->struct_size = (int)sizeof(*report); report->error_selector = -1;
    if (batch->steps) {
        memset(batch->output, 0, (size_t)batch->steps * 4 * sizeof(double));
        memset(batch->flags, 0, (size_t)batch->steps * sizeof(int));
        memset(batch->count, 0, (size_t)batch->steps * sizeof(int));
        for (int index = 0; index < batch->steps; ++index) batch->order[index] = -1;
    }
    for (int index = 0; index < batch->steps; ++index) {
        int status = cap_dispatch(batch, &index);
        if (status < 0) return status;
        if (status == 1) return 0;
    }
    return 0;
}

static int cap_request(const ce_audio_prepare_request_v1 *request, ce_audio_prepare_report_v1 *report)
{
    if (!request) return -1;
    cap_batch batch = {request->steps, request->values, request->step, request->value,
        request->status, request->errors, request->output, request->flags, request->order, request->count, report};
    return cap_execute(&batch);
}
CE_API int ce_audio_prepare_processors_v1(const ce_audio_prepare_request_v1 *request, ce_audio_prepare_report_v1 *report)
{ return cap_request(request, report); }
CE_API int ce_audio_prepare_state_v1(const ce_audio_prepare_request_v1 *request, ce_audio_prepare_report_v1 *report)
{ return cap_request(request, report); }
CE_API int ce_audio_prepare_points_v1(const ce_audio_prepare_request_v1 *request, ce_audio_prepare_report_v1 *report)
{ return cap_request(request, report); }

CE_API int ce_audio_prepare_round_v1(int n, const double *input, const int *digits, double *output,
                                    ce_audio_prepare_report_v1 *report)
{
    if (!report || report->abi != 1 || report->struct_size != (int)sizeof(*report) || n < 0) return -1;
    if (n && (!input || !digits || !output)) return -1;
    memset(report, 0, sizeof(*report)); report->abi = 1; report->struct_size = (int)sizeof(*report); report->error_selector = -1;
    for (int index = 0; index < n; ++index) {
        if (digits[index] != 3 && digits[index] != 4) return -1;
        output[index] = _ce_audio_decimal34(input[index], digits[index]);
        report->round_values++;
    }
    return 0;
}
