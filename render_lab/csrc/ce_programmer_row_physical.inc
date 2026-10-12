/* Ordered conversion/quantization; original min-first/max-first NaN policies. */
static double ce_pr_min_first(double value,double cap) {
    double inner=value<cap?value:cap;return inner>0.?inner:0.;
}
static double ce_pr_max_first(double value,double cap) {
    double inner=value>0.?value:0.;return inner<cap?inner:cap;
}
static int ce_pr_int_value(ce_pr_context *c,const ce_traj_value *v,ce_pr_number *out,int stage) {
    if(v->kind==CE_TR_INT || v->kind==CE_TR_BOOL) {
        int rc=ce_pr_num_get(c,v,out,stage);out->selector=-1;return rc;
    }
    double number;int rc=ce_pr_double(c,v,&number,stage);if(rc) return rc;
    if(isnan(number)||isinf(number))
        return ce_pr_fail(c,isnan(number)?CE_TR_NAN:CE_TR_INF,stage,v->selector);
    return ce_pr_num_integral(c,number,out,stage);
}
static int ce_pr_round_out(ce_pr_context *c,double value,int stage,int selector) {
    double rounded;int rc=ce_tr_round(value,&rounded);
    if(rc) return ce_pr_fail(c,rc,stage,selector);
    ce_pr_number integer={0};rc=ce_pr_num_integral(c,rounded,&integer,stage);
    if(!rc) rc=ce_pr_value_out(c,&integer);
    ce_pr_num_clear(&integer);return rc;
}
static int ce_pr_byte_int(ce_pr_number *integer) {
    if(integer->integer.sign<0) { ce_pr_num_clear(integer);return ce_pr_num_small(integer,0); }
    if(integer->integer.size>1) { ce_pr_num_clear(integer);return ce_pr_num_small(integer,255); }
    return 0;
}
static int ce_pr_quantize(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;int rc;
    if(p->mode==3 || c->q->op==PR_TRUNCATE || c->q->op==PR_SPARSE) {
        ce_pr_number integer={0};rc=ce_pr_int_value(c,v,&integer,1);
        if(!rc && c->q->op!=PR_TRUNCATE) rc=ce_pr_byte_int(&integer);
        if(!rc) rc=ce_pr_value_out(c,&integer);
        ce_pr_num_clear(&integer);
        if(!rc) { if(c->q->op==PR_SPARSE) ++c->r->physical;else ++c->r->row; }
        if(!rc && c->q->op==PR_QUANTIZE) ce_pr_number_out(c,0.);
        return rc;
    }
    double number;rc=ce_pr_double(c,v,&number,1);if(rc) return rc;
    if(p->mode==1) number=ce_pr_min_first(number,255.);
    if(p->mode==2) number=ce_pr_max_first(number,255.);
    double rounded;rc=ce_tr_round(number,&rounded);
    if(rc) return ce_pr_fail(c,rc,2,v->selector);
    rounded=ce_pr_min_first(rounded,255.);
    rc=ce_pr_round_out(c,rounded,2,v->selector);
    if(!rc) {
        ce_pr_number_out(c,number);
        if(p->mode==2) ++c->r->physical;else ++c->r->row;
    }return rc;
}
static int ce_pr_default_read(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;double value;
    int rc=ce_pr_double(c,v,&value,1);if(rc) return rc;
    if(c->q->op==PR_DEFAULT) {
        ce_pr_number_out(c,ce_pr_min_first(value/255.,1.));++c->r->default_value;return 0;
    }
    value=ce_pr_min_first(value,1.)*255.;
    rc=ce_pr_round_out(c,value,2,v->selector);
    if(!rc) { ce_pr_number_out(c,value);++c->r->read; }return rc;
}
static int ce_pr_motor(ce_pr_context *c,const ce_pr_item *p) {
    double value;int rc=ce_pr_double(c,c->q->values+p->value_offset,&value,1);
    if(rc) return rc;uint32_t pair=ce_programmer_motor_16bit(value);
    ce_pr_index_out(c,(pair>>8)&255);ce_pr_index_out(c,pair&255);++c->r->physical;return 0;
}
static int ce_pr_virtual(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;double value;
    int rc=ce_pr_double(c,v,&value,1);if(rc) return rc;
    if(p->flags&1) value=ce_pr_max_first(value,255.);
    double gain=ce_pr_min_first(value/255.,1.);ce_pr_number_out(c,gain);
    for(int i=0;i<4;++i) {
        double color=0.;
        if(p->present&(1u<<i)) { rc=ce_pr_double(c,v+i+1,&color,i+2);if(rc) return rc; }
        rc=ce_pr_round_out(c,color*gain,i+2,(v+i+1)->selector);if(rc) return rc;
    }
    ++c->r->physical;return 0;
}
static int ce_pr_convert_float(ce_pr_context *c,const ce_pr_item *p) {
    double value;int rc=ce_pr_double(c,c->q->values+p->value_offset,&value,1);
    if(!rc) { ce_pr_number_out(c,value);++c->r->row; }return rc;
}
