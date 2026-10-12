static int ce_ps_coordinates(ce_ps_context *c,double *xs,double *ys) {
    const ce_ps_request *q=c->q;
    if(!(q->flags&CE_PS_TYPED)) {
        memcpy(xs,q->xs,(size_t)q->n*sizeof(double));
        memcpy(ys,q->ys,(size_t)q->n*sizeof(double)); return 0;
    }
    for(int64_t i=0;i<q->n*2;++i) {
        const ce_traj_value *value=q->coordinates+i;
        if(!ce_cs_value_valid(q->scalar,value)) return -1;
        double number; int rc=ce_cs_double(&c->scalar,value,&number);
        if(rc<0) return rc;
        if(rc) return ce_ps_fail(c,rc,i,value->selector);
        if(i&1) ys[i/2]=number; else xs[i/2]=number;
    }
    return 0;
}
static void ce_ps_pair_key(ce_ps_context *c,ce_ps_key *key,int64_t i,
                           const double *xs,const double *ys) {
    int front=c->q->direction==2;
    key->first=front?ys[i]:-ys[i]; key->second=xs[i]; key->pair=1;
    key->first_owner=front?c->q->y_tokens[i]:0;
    key->second_owner=c->q->x_tokens[i];
}
static int ce_ps_build_keys(ce_ps_context *c,ce_ps_key *keys,double *coordinates) {
    const ce_ps_request *q=c->q; double *xs=coordinates,*ys=coordinates+q->n;
    int rc=ce_ps_coordinates(c,xs,ys); if(rc || c->r->semantic) return rc;
    double cx=0.,cy=0.;
    /* The original sum loops use a Windows Py_ssize_t-sized row count. */
    if(q->direction>=4) cx=ce_ps_sum(xs,q->n)/(double)q->n;
    if(q->direction>=6) cy=ce_ps_sum(ys,q->n)/(double)q->n;
    for(int64_t i=0;i<q->n;++i) {
        memset(keys+i,0,sizeof(*keys));
        if(q->direction<4) ce_ps_pair_key(c,keys+i,i,xs,ys);
        else if(q->direction<6) {
            keys[i].first=fabs(xs[i]-cx);
            if(q->direction==5) keys[i].first=-keys[i].first;
        } else keys[i].first=_ce_mod_div(atan2(ys[i]-cy,xs[i]-cx),6.283185307179586);
        ++c->r->keys;
    }
    return 0;
}
