static int ce_tr_emit(const ce_traj_request *q,ce_traj_report *r,
                      const ce_traj_offset *offset,const ce_tr_big *value) {
    int64_t index=r->event_count;
    /* Last write wins; preserve first insertion order within a clip. */
    for(int64_t i=0;i<r->event_count;++i)
        if(q->event_out[i].universe==offset->universe && q->event_out[i].offset==offset->offset) { index=i; break; }
    if(index>=q->event_capacity) return -2;
    ce_traj_event *event=q->event_out+index;
    int rc=ce_tr_encode(value,q,r,&event->value); if(rc) return rc;
    event->universe=offset->universe; event->offset=offset->offset;
    if(index==r->event_count) ++r->event_count;
    ++r->values; return 0;
}
static int ce_tr_clamped(const ce_traj_request *q,const ce_traj_value *v,
                         ce_traj_report *r,double *out) {
    double value; int rc=ce_tr_value_double(q,v,r,&value); if(rc) return rc;
    rc=ce_tr_round(value,out); if(rc) return rc;
    if(*out<0.) *out=0.; if(*out>255.) *out=255.; return 0;
}
static int ce_tr_apply_one(const ce_traj_request *q,const ce_traj_applier *a,
                          ce_traj_report *r,int64_t row) {
    double base,target; int rc; ce_tr_big number={0};
    ++r->applier;
    rc=ce_tr_clamped(q,&a->value,r,&base);
    if(!rc && a->kind==1) rc=ce_tr_clamped(q,&a->target,r,&target);
    else target=base;
    if(rc<0) return rc;
    if(rc) return ce_tr_semantic(r,rc,row);
    for(int64_t i=0;i<a->count;++i) {
        const ce_traj_offset *offset=q->offsets+a->offset+i;
        double value=base;
        if(a->kind==1 && a->mode==2) value=target;
        else if(a->kind==1) {
            double frac=a->count>1?(double)offset->fixture/(double)(a->count-1):1.;
            if(a->mode==1) frac=1.-fabs(1.-2.*frac);
            value=base+(target-base)*frac;
            rc=ce_tr_round(value,&value); if(rc) return ce_tr_semantic(r,rc,row);
        }
        rc=ce_tr_from_double(value,&number); if(rc) return rc;
        rc=ce_tr_emit(q,r,offset,&number); ce_tr_big_clear(&number); if(rc) return rc;
    }
    return 0;
}
static int ce_tr_appliers(const ce_traj_request *q,ce_traj_report *r) {
    for(int64_t i=0;i<q->applier_count;++i) {
        const ce_traj_applier *a=q->appliers+i;
        if(!ce_tr_bounds(a->offset,a->count,q->offset_count) || a->kind<0 || a->kind>1) return -1;
        int rc=ce_tr_apply_one(q,a,r,i); if(rc) return rc;
    }
    return 0;
}
