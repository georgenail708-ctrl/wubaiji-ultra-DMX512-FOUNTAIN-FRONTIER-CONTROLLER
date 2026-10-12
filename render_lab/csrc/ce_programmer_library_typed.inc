/* Lossless field descriptors; conversion occurs at the original C math barrier. */
static ce_traj_value ce_pl_column(const ce_pl_request *q,int column,int64_t i) {
    const ce_traj_value *typed=column?q->typed_b:q->typed_a;
    const double *dense=column?q->b:q->a;
    return typed?typed[i]:ce_pl_float(dense[i]);
}
static ce_traj_value ce_pl_parameter(const ce_pl_request *q,int i) {
    if(q->typed_p) return q->typed_p[i];
    const double values[4]={q->p0,q->p1,q->p2,q->p3};return ce_pl_float(values[i]);
}
static int ce_pl_to_double(ce_pl_context *c,const ce_traj_value *v,double *out) {
    int rc=ce_cs_double(&c->scalar,v,out);
    if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,-1);c->r->result.selector=v->selector;return 0; }
    return rc;
}
static int ce_pl_parameter_double(ce_pl_context *c,int i,double *out) {
    ce_traj_value value=ce_pl_parameter(c->q,i);return ce_pl_to_double(c,&value,out);
}
static int ce_pl_column_double(ce_pl_context *c,int column,int64_t i,double *out) {
    ce_traj_value value=ce_pl_column(c->q,column,i);return ce_pl_to_double(c,&value,out);
}
static int ce_pl_difference(ce_pl_context *c,const ce_traj_value *a,
                             const ce_traj_value *b,double *out) {
    int selector=-1,rc=ce_cs_difference(&c->scalar,a,b,out,&selector);
    if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,-1);c->r->result.selector=selector;return 0; }
    return rc;
}
static int ce_pl_edge_delta(ce_pl_context *c,int64_t i,double *out) {
    ce_traj_value a=ce_pl_column(c->q,0,i+1),b=ce_pl_column(c->q,0,i);
    return ce_pl_difference(c,&a,&b,out);
}
static ce_traj_value ce_pl_pause_value(const ce_pl_request *q,int64_t i) {
    return i<q->group_n?ce_pl_column(q,0,i):ce_pl_parameter(q,2);
}
static void ce_pl_row_context(ce_pl_context *c,ce_pr_request *q,ce_pr_report *r,
                               ce_pr_context *row) {
    memset(q,0,sizeof(*q));memset(r,0,sizeof(*r));memset(row,0,sizeof(*row));
    q->integers=c->q->scalar->integers;q->integer_size=c->q->scalar->integer_size;
    q->value_out=c->q->out_value;q->value_capacity=1;
    q->bytes_out=c->q->out_bytes;q->byte_capacity=c->q->byte_capacity;
    row->q=q;row->r=r;row->integers.integers=q->integers;
    row->integers.integer_size=q->integer_size;
}
static int ce_pl_row_result(ce_pl_context *c,const ce_pr_report *r,int rc) {
    c->r->scalar.integer_conversions+=r->integer_conversions;
    c->r->written_bytes=r->written_bytes;
    if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,-1);
               c->r->result.selector=r->reserved?(int)r->reserved-1:-1;return 0; }
    return rc;
}
static int ce_pl_pause_sum(ce_pl_context *c,int64_t bc,ce_pr_context *row,
                            ce_pr_number *out) {
    int64_t count=bc>0?bc-1:0;
    ce_traj_value *values=count?malloc((size_t)count*sizeof(*values)):NULL;
    if(count && !values) return -2;
    for(int64_t i=0;i<count;++i) values[i]=ce_pl_pause_value(c->q,i);
    int rc=ce_pr_sum_values(row,values,count,out);free(values);return rc;
}
static int ce_pl_span(ce_pl_context *c,int64_t bc) {
    ce_pr_request q;ce_pr_report report;ce_pr_context row;
    ce_pl_row_context(c,&q,&report,&row);
    ce_pr_number extra={0},per={0},count={0},product={0},result={0};
    int rc=ce_pl_pause_sum(c,bc,&row,&extra);
    ce_traj_value value=ce_pl_parameter(c->q,0);
    if(!rc) rc=ce_pr_num_get(&row,&value,&per,1);
    if(!rc) rc=ce_pr_from_i64(&count,bc);
    if(!rc && per.kind!=CE_TR_FLOAT) {
        product.kind=CE_TR_INT;product.selector=-1;
        rc=ce_pr_multiply(&per.integer,&count.integer,&product.integer);
    } else if(!rc) { product.kind=CE_TR_FLOAT;product.selector=-1;
                    product.number=per.number*(double)bc; }
    if(!rc) rc=ce_pr_add_numbers(&row,&product,&extra,&result,1);
    if(!rc) rc=ce_pr_value_out(&row,&result);
    ce_pr_num_clear(&extra);ce_pr_num_clear(&per);ce_pr_num_clear(&count);
    ce_pr_num_clear(&product);ce_pr_num_clear(&result);
    return ce_pl_row_result(c,&report,rc);
}
static int ce_pl_pause_sum_double(ce_pl_context *c,int64_t bc,double *out) {
    ce_pr_request q;ce_pr_report report;ce_pr_context row;
    ce_pl_row_context(c,&q,&report,&row);ce_pr_number sum={0};
    int rc=ce_pl_pause_sum(c,bc,&row,&sum);
    if(!rc) rc=ce_pr_num_double(&row,&sum,out,2);
    ce_pr_num_clear(&sum);return ce_pl_row_result(c,&report,rc);
}
