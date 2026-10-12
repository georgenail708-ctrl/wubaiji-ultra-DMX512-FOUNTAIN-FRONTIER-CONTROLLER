/* Four original ordered typed layers. NaN is a value, never absence. */
static int ce_pr_add_numbers(ce_pr_context *c,const ce_pr_number *a,
                            const ce_pr_number *b,ce_pr_number *out,int sign) {
    memset(out,0,sizeof(*out));out->selector=-1;
    if(a->kind!=CE_TR_FLOAT && b->kind!=CE_TR_FLOAT) {
        out->kind=CE_TR_INT;
        int rc=ce_tr_add(&a->integer,&b->integer,sign,&out->integer);
        if(!rc) ++c->r->integer_arithmetic;return rc;
    }
    double x,y;int rc=ce_pr_num_double(c,a,&x,2);if(rc) return rc;
    rc=ce_pr_num_double(c,b,&y,2);if(rc) return rc;
    out->kind=CE_TR_FLOAT;out->number=sign>0?x+y:x-y;return 0;
}
static int ce_pr_product_divide(ce_pr_context *c,const ce_pr_number *a,
                              const ce_pr_number *b,ce_pr_number *out) {
    double value;int rc;
    if(a->kind!=CE_TR_FLOAT && b->kind!=CE_TR_FLOAT) {
        ce_tr_big product={0};rc=ce_pr_multiply(&a->integer,&b->integer,&product);
        if(!rc) { ++c->r->integer_arithmetic;rc=ce_pr_true_divide(&product,255,&value); }
        ce_tr_big_clear(&product);if(rc) return ce_pr_fail(c,rc,3,-1);
    } else {
        double x,y;rc=ce_pr_num_double(c,a,&x,2);if(rc) return rc;
        rc=ce_pr_num_double(c,b,&y,2);if(rc) return rc;
        double product=x*y;value=product/255.;
    }
    return ce_pr_num_integral(c,value,out,4);
}
static void ce_pr_move(ce_pr_number *target,ce_pr_number *source) {
    ce_pr_num_clear(target);*target=*source;memset(source,0,sizeof(*source));
}
static int ce_pr_mix_layer(ce_pr_context *c,ce_pr_number *result,
                           const ce_traj_value *value,int mode) {
    if(mode==4) return 0;
    ce_pr_number right={0},next={0},cap={0};
    int rc=ce_pr_num_get(c,value,&right,1),comparison;if(rc) goto done;
    if(mode==0) { ce_pr_move(result,&right);goto done; }
    if(mode==3) {
        rc=ce_pr_num_compare(c,&right,result,&comparison);
        if(!rc && comparison==1) ce_pr_move(result,&right);goto done;
    }
    if(mode==2) rc=ce_pr_product_divide(c,result,&right,&next);
    else {
        rc=ce_pr_add_numbers(c,result,&right,&next,1);if(rc) goto done;
        rc=ce_pr_num_small(&cap,255);if(rc) goto done;
        rc=ce_pr_num_compare(c,&next,&cap,&comparison);if(rc) goto done;
        if(comparison!=-1) ce_pr_move(&next,&cap);
    }
    if(!rc) ce_pr_move(result,&next);
done:
    ce_pr_num_clear(&right);ce_pr_num_clear(&next);ce_pr_num_clear(&cap);return rc;
}
static int ce_pr_mix(ce_pr_context *c,const ce_pr_item *p) {
    ce_pr_number result={0};int rc=ce_pr_num_small(&result,0);
    for(int i=0;!rc && i<4;++i) if(p->present&(1u<<i))
        rc=ce_pr_mix_layer(c,&result,c->q->values+p->value_offset+i,p->layers[i]);
    if(!rc) { rc=ce_pr_value_out(c,&result);if(!rc) ++c->r->row; }
    ce_pr_num_clear(&result);return rc;
}
