/* Original joystick curve: raw/255.0, strategy, then repeated lo/hi casts.
 * No custom LUT/model reinterpretation. Float kernels do not use FMA. */
static int ce_pr_step(ce_pr_context *c,double t,const ce_traj_value *steps,double *out) {
    ce_pr_number n={0},two={0},denominator={0},one={0},numerator={0};int rc,comparison;
    rc=ce_pr_num_get(c,steps,&n,5);if(rc) goto done;
    rc=ce_pr_num_small(&two,2);if(rc) goto done;
    rc=ce_pr_num_compare(c,&n,&two,&comparison);if(rc) goto done;
    if(comparison!=1) ce_pr_move(&n,&two);
    double count;rc=ce_pr_num_double(c,&n,&count,5);if(rc) goto done;
    double product=t*count;
    if(isnan(product) || isinf(product)) {
        rc=ce_pr_fail(c,isnan(product)?PR_FLOOR_NAN:PR_FLOOR_INF,5,steps->selector);goto done;
    }
    rc=ce_pr_num_integral(c,floor(product),&numerator,5);if(rc) goto done;
    rc=ce_pr_num_small(&one,1);if(rc) goto done;
    rc=ce_pr_add_numbers(c,&n,&one,&denominator,-1);if(rc) goto done;
    if(denominator.kind==CE_TR_FLOAT) {
        double value;rc=ce_pr_num_double(c,&numerator,&value,5);if(rc) goto done;
        if(denominator.number==0.) { rc=ce_pr_fail(c,CE_TR_ZERO,5,-1);goto done; }
        *out=value/denominator.number;
    } else {
        rc=ce_pr_big_divide(&numerator.integer,&denominator.integer,out);
        if(rc>0) rc=ce_pr_fail(c,rc,5,-1);
    }
    if(!rc) *out=ce_pr_min_first(*out,1.);
done:
    ce_pr_num_clear(&n);ce_pr_num_clear(&two);ce_pr_num_clear(&one);
    ce_pr_num_clear(&denominator);ce_pr_num_clear(&numerator);return rc;
}
static int ce_pr_gamma(ce_pr_context *c,double t,const ce_traj_value *gamma,double *out) {
    ce_pr_number exponent={0},minimum={0};int comparison;
    int rc=ce_pr_num_get(c,gamma,&exponent,4);if(rc) goto done;
    minimum.kind=CE_TR_FLOAT;minimum.selector=-1;minimum.number=.01;
    rc=ce_pr_num_compare(c,&exponent,&minimum,&comparison);if(rc) goto done;
    if(comparison!=1) ce_pr_move(&exponent,&minimum);
    double power;rc=ce_pr_num_double(c,&exponent,&power,4);if(rc) goto done;
    *out=pow(t,power);
done:
    ce_pr_num_clear(&exponent);ce_pr_num_clear(&minimum);return rc;
}
static int ce_pr_curve(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;double raw;
    /* Division by float255.0 requires the ordinary int->double first. */
    int rc=ce_pr_double(c,v,&raw,1);if(rc) return rc;
    double t=ce_pr_min_first(raw/255.,1.);
    if(p->mode==1) { rc=ce_pr_gamma(c,t,v+3,&t);if(rc) return rc; }
    else if(p->mode==2) t=t*t*(3.-2.*t);
    else if(p->mode==3) t=1.-t;
    else if(p->mode==4) { rc=ce_pr_step(c,t,v+4,&t);if(rc) return rc; }
    double first,high,second;
    rc=ce_pr_double(c,v+1,&first,2);if(rc) return rc;
    rc=ce_pr_double(c,v+2,&high,3);if(rc) return rc;
    rc=ce_pr_double(c,v+1,&second,2);if(rc) return rc;
    double delta=high-second;double product=t*delta;
    double value=ce_pr_min_first(first+product,255.);
    ce_pr_number_out(c,value);++c->r->curve;
    ce_pr_number output={0};
    if(p->flags&1u) rc=ce_pr_num_integral(c,value,&output,6);
    else { output.kind=CE_TR_FLOAT;output.selector=-1;output.number=value; }
    if(!rc) rc=ce_pr_value_out(c,&output);
    ce_pr_num_clear(&output);return rc;
}
