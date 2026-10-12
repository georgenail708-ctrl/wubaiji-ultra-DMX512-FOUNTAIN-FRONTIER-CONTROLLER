static int ce_pr_round_typed(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;
    if(v->kind==CE_TR_FLOAT) {
        int rc=ce_pr_round_out(c,v->number,1,v->selector);
        if(!rc) ++c->r->row;return rc;
    }
    ce_pr_number integer={0};int rc=ce_pr_num_get(c,v,&integer,1);
    if(v->kind==CE_TR_BOOL) integer.selector=-1;
    if(!rc) { rc=ce_pr_value_out(c,&integer);if(!rc) ++c->r->row; }
    ce_pr_num_clear(&integer);return rc;
}
static int ce_pr_z_final(ce_pr_context *c,const ce_pr_item *p) {
    double target=c->q->numbers[p->number_offset];double delta=target-128.;
    int present=!(fabs(delta)<1.);
    double angle=present?delta*(180./128.):0.;
    ce_pr_number_out(c,angle);ce_pr_index_out(c,present);++c->r->range;return 0;
}
static int ce_pr_jitter_bounds(ce_pr_context *c,const ce_pr_item *p) {
    double pct=c->q->numbers[p->number_offset];
    double limited=pct<100.?pct:100.;double d=pct<=0.?0.:limited/100.*255.;
    ce_pr_number_out(c,-d);ce_pr_number_out(c,d);++c->r->range;return 0;
}
