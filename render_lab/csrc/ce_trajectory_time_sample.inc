static int ce_tr_times(const ce_traj_request *q,ce_traj_report *r) {
    for(int64_t i=0;i<q->time_count;++i) {
        const ce_traj_time *t=q->times+i; ce_traj_time_out *o=q->time_out+i;
        if(t->stages&1) o->active=t->duration>0. && t->start<=t->position
                                   && t->position<t->start+t->duration;
        if(t->stages&2) {
            double rate=t->rate>.001?t->rate:.001;
            double duration=t->source_duration>.05?t->source_duration:.05;
            o->source=_ce_mod_div((t->position-t->start)*rate,duration);
        }
        ++r->source;
    }
    return 0;
}
static int ce_tr_numeric(const ce_traj_value *v) {
    return v->kind==CE_TR_FLOAT || v->kind==CE_TR_INT || v->kind==CE_TR_BOOL;
}
static int ce_tr_interpolate(const ce_traj_request *q,const ce_traj_value *a,
                            const ce_traj_value *b,double fraction,
                            ce_traj_report *r,double *out) {
    double left,right,delta; int rc; ce_tr_big x={0},y={0},difference={0};
    if(a->kind==CE_TR_INT && (b->kind==CE_TR_INT || b->kind==CE_TR_BOOL)) {
        rc=ce_tr_decode(q,a,&x); if(rc) goto done;
        rc=ce_tr_decode(q,b,&y); if(rc) goto done;
        rc=ce_tr_add(&y,&x,-1,&difference); if(rc) goto done;
        ++r->integer_subtract;
        rc=ce_tr_to_double(&difference,&delta); if(rc) goto done;
        ++r->integer_conversions; delta=delta*fraction;
        rc=ce_tr_to_double(&x,&left); if(rc) goto done;
        ++r->integer_conversions; *out=left+delta;
    } else {
        /* Python computes right-left before multiplying and adding left. */
        rc=ce_tr_value_double(q,b,r,&right); if(rc) goto done;
        rc=ce_tr_value_double(q,a,r,&left); if(rc) goto done;
        delta=right-left; delta=delta*fraction; *out=left+delta;
    }
    rc=0;
done:
    ce_tr_big_clear(&x); ce_tr_big_clear(&y); ce_tr_big_clear(&difference); return rc;
}
static int ce_tr_sample_one(const ce_traj_request *q,const ce_traj_sample *s,
                            ce_traj_report *r,ce_traj_result *o) {
    int64_t chosen=s->offset; double t=s->position; int rc=0;
    if(!s->count) return 0;
    ++r->sample;
    if(t<=q->timestamps[chosen]) goto selected;
    chosen=s->offset+s->count-1;
    if(t>=q->timestamps[chosen]) goto selected;
    for(int64_t i=s->offset;i<s->offset+s->count-1;++i) {
        double t0=q->timestamps[i],t1=q->timestamps[i+1];
        if(!(t0<=t && t<=t1)) continue;
        chosen=i+1; if(t1<=t0) goto selected;
        const ce_traj_value *a=q->points+i,*b=a+1;
        if(ce_tr_numeric(a) && ce_tr_numeric(b) && a->kind!=CE_TR_BOOL) {
            double numerator=t-t0,denominator=t1-t0;
            if(denominator==0.) rc=CE_TR_ZERO;
            else rc=ce_tr_interpolate(q,a,b,numerator/denominator,r,&o->number);
            if(rc<0) return rc;
            if(rc) { o->kind=CE_TR_ERROR; o->semantic=rc; ce_tr_semantic(r,rc,i); }
            else { o->kind=CE_TR_NUMBER; ++r->values; }
            return 0;
        }
        chosen=(t-t0)<=(t1-t)?i:i+1; goto selected;
    }
selected:
    o->kind=CE_TR_SELECTED; o->selector=q->points[chosen].selector; ++r->values; return 0;
}
static int ce_tr_samples(const ce_traj_request *q,ce_traj_report *r) {
    for(int64_t i=0;i<q->sample_count;++i) {
        const ce_traj_sample *s=q->samples+i;
        if(!ce_tr_bounds(s->offset,s->count,q->point_count)) return -1;
        int rc=ce_tr_sample_one(q,s,r,q->results+i); if(rc) return rc;
    }
    return 0;
}
