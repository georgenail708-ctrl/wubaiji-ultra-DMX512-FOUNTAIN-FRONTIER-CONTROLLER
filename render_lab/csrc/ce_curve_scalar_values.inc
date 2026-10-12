static int ce_cs_integer(const ce_traj_value *v) {
    return v->kind==CE_TR_INT || v->kind==CE_TR_BOOL;
}
static int ce_cs_double(ce_cs_context *c,const ce_traj_value *v,double *out) {
    if(v->kind==CS_TEXT) { *out=v->number; return (int)v->offset; }
    if(v->kind==CS_NONE) return CE_TR_PARSE_TYPE;
    ce_traj_report stats={0};
    int rc=ce_tr_value_double(&c->integer_view,v,&stats,out);
    c->report->integer_conversions+=stats.integer_conversions; return rc;
}
static int ce_cs_big_compare(const ce_tr_big *a,const ce_tr_big *b) {
    if(a->sign!=b->sign) return a->sign>b->sign?1:-1;
    return a->sign*ce_tr_compare_abs(a,b);
}
static int ce_cs_float_big(double value,const ce_tr_big *integer,int *out) {
    if(isnan(value)) { *out=2; return 0; }
    if(isinf(value)) { *out=value>0.?1:-1; return 0; }
    double integral,fraction=modf(value,&integral); ce_tr_big truncated={0};
    int rc=ce_tr_from_double(integral,&truncated); if(rc) return rc;
    *out=ce_cs_big_compare(&truncated,integer);
    if(!*out && fraction!=0.) *out=fraction>0.?1:-1;
    ce_tr_big_clear(&truncated); return 0;
}
static int ce_cs_compare(ce_cs_context *c,const ce_traj_value *a,
                          const ce_traj_value *b,int *out) {
    ce_tr_big x={0},y={0}; int rc=0;
    if(ce_cs_integer(a)) { rc=ce_tr_decode(&c->integer_view,a,&x); if(rc) goto done; }
    if(ce_cs_integer(b)) { rc=ce_tr_decode(&c->integer_view,b,&y); if(rc) goto done; }
    if(ce_cs_integer(a) && ce_cs_integer(b)) *out=ce_cs_big_compare(&x,&y);
    else if(a->kind==CE_TR_FLOAT && ce_cs_integer(b)) rc=ce_cs_float_big(a->number,&y,out);
    else if(ce_cs_integer(a) && b->kind==CE_TR_FLOAT) {
        rc=ce_cs_float_big(b->number,&x,out); if(!rc && *out!=2) *out=-*out;
    } else if(a->kind==CE_TR_FLOAT && b->kind==CE_TR_FLOAT)
        *out=isnan(a->number)||isnan(b->number)?2:
             (a->number<b->number?-1:(a->number>b->number?1:0));
    else rc=-1;
done:
    ce_tr_big_clear(&x); ce_tr_big_clear(&y); return rc;
}
static int ce_cs_difference(ce_cs_context *c,const ce_traj_value *a,
                             const ce_traj_value *b,double *out,int *error_selector) {
    ce_tr_big x={0},y={0},difference={0}; double left,right; int rc;
    *error_selector=-1;
    if(ce_cs_integer(a) && ce_cs_integer(b)) {
        rc=ce_tr_decode(&c->integer_view,a,&x); if(rc) goto done;
        rc=ce_tr_decode(&c->integer_view,b,&y); if(rc) goto done;
        rc=ce_tr_add(&x,&y,-1,&difference); if(rc) goto done;
        ++c->report->integer_subtract; rc=ce_tr_to_double(&difference,out);
        if(!rc) ++c->report->integer_conversions;
    } else {
        if((a->kind!=CE_TR_FLOAT && !ce_cs_integer(a))
            || (b->kind!=CE_TR_FLOAT && !ce_cs_integer(b))) { rc=-1; goto done; }
        rc=ce_cs_double(c,a,&left); if(rc) { *error_selector=a->selector; goto done; }
        rc=ce_cs_double(c,b,&right); if(rc) { *error_selector=b->selector; goto done; }
        *out=left-right;
    }
done:
    ce_tr_big_clear(&x); ce_tr_big_clear(&y); ce_tr_big_clear(&difference); return rc;
}
static double ce_cs_clamp(ce_cs_context *c,double value) {
    ++c->report->clamps;
    return value<0.?0.:(value>1.?1.:value);
}
static int ce_cs_clamped_value(ce_cs_context *c,const ce_traj_value *v,
                                ce_traj_result *out) {
    double value; int rc=ce_cs_double(c,v,&value); if(rc<0) return rc;
    if(rc) { ++c->report->handled_semantic; ce_cs_number(out,0.); return 0; }
    double result=ce_cs_clamp(c,value); ce_cs_number(out,result);
    if(v->kind==CE_TR_FLOAT && v->selector>=0 && !(value<0. || value>1.)) {
        out->kind=CE_TR_SELECTED; out->selector=v->selector;
    }
    return 0;
}
static int ce_cs_truth(ce_cs_context *c,const ce_traj_value *v,int *out) {
    if(v->kind==CS_NONE) { *out=0; return 0; }
    if(ce_cs_integer(v)) {
        ce_tr_big integer={0}; int rc=ce_tr_decode(&c->integer_view,v,&integer);
        if(!rc) *out=integer.sign!=0; ce_tr_big_clear(&integer); return rc;
    }
    if(v->kind==CE_TR_FLOAT) { *out=v->number!=0.; return 0; }
    *out=!(c->request->flags&CS_DURATION_FALSE); return 0;
}
static int ce_cs_parse_at(ce_cs_context *c,const ce_traj_value *v,double *number,
                           ce_traj_result *out,int stage,int64_t index) {
    int rc=ce_cs_double(c,v,number);
    if(rc>0) ce_cs_error(c,out,rc,stage,v->selector,index);
    return rc;
}
