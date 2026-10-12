/* Dense ordinary range math; public typed coordinates retain their order. */
static int ce_pr_ratio_typed(ce_pr_context *c,const ce_traj_value *v,double *t,int *single) {
    ce_pr_number index={0},total={0},one={0},denominator={0};int rc,comparison;
    rc=ce_pr_num_get(c,v+1,&total,1);if(rc) goto done;
    rc=ce_pr_num_small(&one,1);if(rc) goto done;
    rc=ce_pr_num_compare(c,&total,&one,&comparison);if(rc) goto done;
    if(comparison==-1 || comparison==0) { *t=1.;*single=1;goto done; }
    rc=ce_pr_add_numbers(c,&total,&one,&denominator,-1);if(rc) goto done;
    double divisor,numerator;
    rc=ce_pr_num_double(c,&denominator,&divisor,2);if(rc) goto done;
    /* float(total-1) is evaluated before converting/dividing the index. */
    rc=ce_pr_num_get(c,v,&index,3);if(rc) goto done;
    rc=ce_pr_num_double(c,&index,&numerator,3);if(rc) goto done;
    if(divisor==0.) { rc=ce_pr_fail(c,CE_TR_ZERO,3,-1);goto done; }
    *t=ce_pr_min_first(numerator/divisor,1.);
done:
    ce_pr_num_clear(&index);ce_pr_num_clear(&total);
    ce_pr_num_clear(&one);ce_pr_num_clear(&denominator);return rc;
}
static double ce_pr_wing(double t,unsigned wing) {
    if(wing==1) return 1.-t;
    if(wing==2 || wing==4) return fabs(t*2.-1.);
    if(wing==3 || wing==5) return 1.-fabs(t*2.-1.);
    return t;
}
static int ce_pr_range(ce_pr_context *c,const ce_pr_item *p) {
    double t=1.;int rc=0,single=p->total<=1;
    if(p->flags&16u) { single=0;rc=ce_pr_ratio_typed(c,c->q->values+p->value_offset,&t,&single); }
    else if(p->total>1) t=ce_pr_min_first((double)p->index/(double)(p->total-1),1.);
    if(rc) return rc;
    double factor=p->mode==2?fabs(t*2.-1.):p->mode==1?(single?1.:ce_pr_wing(t,p->flags&7u)):1.;
    const double *d=c->q->numbers+p->number_offset;
    double difference=d[1]-d[0];double product=difference*factor;
    double value=ce_pr_min_first(d[0]+product,255.);
    ce_pr_number_out(c,factor);ce_pr_number_out(c,value);
    if(p->flags&8u) {
        ce_pr_number n={0};n.kind=CE_TR_FLOAT;n.selector=-1;n.number=value;
        rc=ce_pr_value_out(c,&n);
    } else rc=ce_pr_round_out(c,value,4,-1);
    if(!rc) ++c->r->range;return rc;
}
static int ce_pr_master(ce_pr_context *c,const ce_pr_item *p) {
    const double *d=c->q->numbers+p->number_offset;
    double value=d[0],base=d[1],target=d[2];int mode=p->mode;
    if(p->flags) { target=value;mode=p->flags==1?2:1; }
    else if(!mode) base=target=value;
    else {
        double delta=value-target;
        double lo=target<base?target:base,hi=target>base?target:base;
        if(lo+delta<0.) delta=-lo;
        if(hi+delta>255.) delta=255.-hi;
        base=base+delta;target=target+delta;
    }
    ce_pr_number_out(c,base);ce_pr_number_out(c,target);
    ce_pr_index_out(c,mode);++c->r->range;return 0;
}
static int ce_pr_jitter(ce_pr_context *c,const ce_pr_item *p) {
    const double *d=c->q->numbers+p->number_offset;double output;
    if(p->flags&1u) output=d[1]<=0.?d[0]:ce_pr_min_first(d[0]+d[2],255.);
    else {
        double pct=d[1];double limited=pct<100.?pct:100.;
        output=pct<=0.?0.:limited/100.*255.;
    }
    ce_pr_number_out(c,output);++c->r->range;return 0;
}
