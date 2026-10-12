static int ce_pr_dispatch(ce_pr_context *c,const ce_pr_item *p) {
    switch(c->q->op) {
    case PR_QUANTIZE:case PR_TRUNCATE:case PR_SPARSE:return ce_pr_quantize(c,p);
    case PR_MIX:return ce_pr_mix(c,p);
    case PR_RANGE:return ce_pr_range(c,p);
    case PR_MASTER:return ce_pr_master(c,p);
    case PR_DEFAULT:case PR_READ:return ce_pr_default_read(c,p);
    case PR_MOTOR:return ce_pr_motor(c,p);
    case PR_VIRTUAL:return ce_pr_virtual(c,p);
    case PR_FILL:return ce_pr_fill(c,p);
    case PR_ADVANCE:return ce_pr_advance(c,p);
    case PR_FINISH:return ce_pr_finish(c,p);
    case PR_SCRUB:return ce_pr_scrub(c,p);
    case PR_DEBOUNCE:return ce_pr_debounce(c,p);
    case PR_MEAN:return ce_pr_mean(c,p);
    case PR_CURVE:return ce_pr_curve(c,p);
    case PR_JITTER:return ce_pr_jitter(c,p);
    case PR_FLOAT:return ce_pr_convert_float(c,p);
    case PR_TICK:return ce_pr_tick(c,p);
    case PR_Z_FINAL:return ce_pr_z_final(c,p);
    case PR_ROUND:return ce_pr_round_typed(c,p);
    case PR_JITTER_BOUNDS:return ce_pr_jitter_bounds(c,p);
    default:return -1;
    }
}
CE_API int ce_programmer_row_eval(const ce_pr_request *q,ce_pr_report *r) {
    if(!r) return -1;
    memset(r,0,sizeof(*r));r->index=-1;
    int rc=ce_pr_validate(q);if(rc) { r->status=rc;return rc; }
    ce_pr_context context;memset(&context,0,sizeof(context));context.q=q;context.r=r;
    context.integers.integers=q->integers;context.integers.integer_size=q->integer_size;
    if(q->op==PR_FILL) {
        for(int64_t i=0;i<q->item_count;++i) {
            rc=ce_pr_fill_program(q,q->items+i);if(rc) { r->status=rc;return rc; }
        }
        if(q->value_count) {
            context.fill_cells=(ce_traj_value*)malloc((size_t)q->value_count*sizeof(ce_traj_value));
            if(!context.fill_cells) { r->status=-2;return -2; }
            memcpy(context.fill_cells,q->values,(size_t)q->value_count*sizeof(ce_traj_value));
        }
    }
    for(int64_t i=0;i<q->item_count;++i) {
        context.item=i;rc=ce_pr_dispatch(&context,q->items+i);
        if(rc) break;
        ++r->completed;
    }
    free(context.fill_cells);
    if(rc<0) { r->status=rc;return rc; }
    r->status=0;return 0;
}
