static int ce_cs_run(const ce_cs_request *q,ce_cs_report *r,ce_cs_sort_report *sort_report) {
    if(!r) return -1;
    memset(r,0,sizeof(*r)); r->index=-1; ce_cs_result_init(&r->result);
    int rc=ce_cs_validate(q); if(rc) { r->status=rc; return rc; }
    ce_cs_context context; memset(&context,0,sizeof(context));
    context.request=q; context.report=r; context.error_index=-1;
    context.sort_report=sort_report;
    context.integer_view.integers=q->integers;
    context.integer_view.integer_size=q->integer_size;
    if(q->mode==CS_MODEL) rc=ce_cs_model(&context,&q->argument,&r->result);
    else if(q->mode==CS_PHASE) rc=ce_cs_phase(&context,&r->result);
    else if(q->mode==CS_EVAL_PHASE) rc=ce_cs_eval_phase(&context,&q->argument,&r->result);
    else {
        ce_traj_result phase; ce_cs_result_init(&phase);
        rc=ce_cs_phase(&context,&phase);
        if(!rc && phase.kind!=CE_TR_ERROR) {
            ce_traj_value argument={CE_TR_FLOAT,-1,0,0,phase.number};
            rc=ce_cs_eval_phase(&context,&argument,&r->result);
        } else if(!rc) r->result=phase;
    }
    r->status=rc;
    if(!rc && r->result.kind==CE_TR_ERROR) {
        r->semantic=r->result.semantic; r->stage=r->result.stage;
        r->index=context.error_index; ++r->semantic_errors;
    } else if(!rc && (r->result.kind==CE_TR_SELECTED || r->result.kind==CE_TR_NUMBER))
        ++r->values;
    return rc;
}
CE_API int ce_curve_scalar_eval(const ce_cs_request *q,ce_cs_report *r) {
    return ce_cs_run(q,r,NULL);
}
CE_API int ce_curve_scalar_eval_v2(const ce_cs_request *q,ce_cs_report *r,
                                   ce_cs_sort_report *sort_report) {
    if(!sort_report) {
        if(r) { memset(r,0,sizeof(*r));r->index=-1;ce_cs_result_init(&r->result);r->status=-1; }
        return -1;
    }
    memset(sort_report,0,sizeof(*sort_report));return ce_cs_run(q,r,sort_report);
}
