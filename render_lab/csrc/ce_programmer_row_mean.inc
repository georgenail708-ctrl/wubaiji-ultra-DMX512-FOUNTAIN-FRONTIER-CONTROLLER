/* Exact typed builtin sum state machine adapted from CPython01104ce
 * Python/bltinmodule.c2554-2762, PSF v2. Win64 ssize64 / C long32.
 * Float compensation is not applied to integer additions or generic tails. */
static void ce_pr_compensate(double *sum,double *low,double value) {
    double next=*sum+value;
    if(fabs(*sum)>=fabs(value)) *low+=(*sum-next)+value;
    else *low+=(value-next)+*sum;
    *sum=next;
}
static double ce_pr_compensated(double sum,double low) {
    return low!=0. && isfinite(low)?sum+low:sum;
}
static int ce_pr_i64_add(int64_t a,int64_t b,int64_t *out) {
    if((b>0 && a>INT64_MAX-b) || (b<0 && a<INT64_MIN-b)) return 0;
    *out=a+b;return 1;
}
static int ce_pr_mean_generic(ce_pr_context *c,ce_pr_number *sum,const ce_pr_number *value) {
    ce_pr_number next={0};int rc=ce_pr_add_numbers(c,sum,value,&next,1);
    if(!rc) ce_pr_move(sum,&next);ce_pr_num_clear(&next);return rc;
}
static int ce_pr_sum_values(ce_pr_context *,const ce_traj_value *,int64_t,ce_pr_number *);
static int ce_pr_mean(ce_pr_context *c,const ce_pr_item *p) {
    ce_pr_number sum={0},final={0};double quotient=0.;
    int rc=ce_pr_sum_values(c,c->q->values+p->value_offset,p->value_count,&sum);
    if(!rc && !p->value_count) rc=ce_pr_value_out(c,&sum);
    else if(!rc) {
        if(sum.kind==CE_TR_FLOAT) quotient=sum.number/(double)p->value_count;
        else rc=ce_pr_true_divide(&sum.integer,(uint64_t)p->value_count,&quotient);
        if(rc>0) rc=ce_pr_fail(c,rc,3,-1);
        if(!rc) rc=ce_pr_num_integral(c,quotient,&final,4);
        if(!rc) { rc=ce_pr_value_out(c,&final);if(!rc) ++c->r->mean; }
    }
    ce_pr_num_clear(&sum);ce_pr_num_clear(&final);return rc;
}
