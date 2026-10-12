static int ce_pl_dispatch(ce_pl_context *c) {
    switch(c->q->op) {
        case PL_QUANTIZE:return ce_pl_quantize_rows(c);
        case PL_SEGMENT:return ce_pl_segment(c);
        case PL_DIRECTION:return ce_pl_direction(c);
        case PL_REVEAL:case PL_MORPH:return ce_pl_group_rows(c);
        case PL_SHAPE:case PL_UNIT_PHASE:return ce_pl_shape(c);
        case PL_WINDOWS:return ce_pl_windows(c);
        case PL_BLEND:return ce_pl_blend_rows(c);
        case PL_PATH:return ce_pl_path(c);
        case PL_SHAPE_TIME:return ce_pl_shape_time(c);
        case PL_WINDOW_SELECT:return ce_pl_window_select(c);
        case PL_STATIC_SELECT:return ce_pl_static_select(c);
        case PL_PARTITION:return ce_pl_partition(c);
        case PL_OFFSETS:return ce_pl_offsets(c);
        case PL_EFFECTIVE:return ce_pl_effective_result(c);
        default:return -1;
    }
}
CE_API int ce_programmer_library_eval(const ce_pl_request *q,ce_pl_report *r) {
    if(!r) return -1;
    memset(r,0,sizeof(*r)); r->index=-1; ce_cs_result_init(&r->result);
    int rc=ce_pl_validate(q); if(rc) { r->status=rc; return rc; }
    ce_pl_context context; memset(&context,0,sizeof(context));
    context.q=q; context.r=r; context.scalar.request=q->scalar;
    context.scalar.report=&r->scalar; context.scalar.error_index=-1;
    context.scalar.sort_report=&r->scalar_sort;
    context.scalar.integer_view.integers=q->scalar->integers;
    context.scalar.integer_view.integer_size=q->scalar->integer_size;
    rc=ce_pl_dispatch(&context); r->status=rc; return rc;
}
CE_API int ce_programmer_library_abi(void) { return CE_PL_ABI; }
CE_API int ce_programmer_library_request_size(void) { return (int)sizeof(ce_pl_request); }
CE_API int ce_programmer_library_report_size(void) { return (int)sizeof(ce_pl_report); }
