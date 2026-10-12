static int ce_tr_read_output(const ce_traj_request *q,const ce_traj_value *value,
                             ce_tr_big *out) {
    ce_traj_request local=*q; local.integers=q->bytes_out; local.integer_size=q->byte_capacity;
    return ce_tr_decode(&local,value,out);
}
static int ce_tr_add_one(const ce_traj_request *q,const ce_traj_event *event,
                         ce_traj_report *r) {
    ce_tr_big old={0},input={0},sum={0},limit={0}; int rc; int64_t found=-1;
    for(int64_t i=0;i<r->event_count;++i)
        if(q->event_out[i].universe==event->universe && q->event_out[i].offset==event->offset) { found=i; break; }
    rc=ce_tr_decode(q,&event->value,&input); if(rc) goto done;
    if(found>=0) rc=ce_tr_read_output(q,&q->event_out[found].value,&old);
    else rc=ce_tr_small(&old,0);
    if(rc) goto done;
    rc=ce_tr_add(&old,&input,1,&sum); if(rc) goto done;
    ++r->add; rc=ce_tr_small(&limit,255); if(rc) goto done;
    if(sum.sign>0 && ce_tr_compare_abs(&sum,&limit)>0) {
        ce_tr_big_clear(&sum); rc=ce_tr_small(&sum,255); if(rc) goto done;
    }
    ce_traj_offset offset={event->universe,event->offset,0};
    rc=ce_tr_emit(q,r,&offset,&sum);
done:
    ce_tr_big_clear(&old); ce_tr_big_clear(&input); ce_tr_big_clear(&sum); ce_tr_big_clear(&limit); return rc;
}
static int ce_tr_add_events(const ce_traj_request *q,ce_traj_report *r) {
    for(int64_t i=0;i<q->event_count;++i) { int rc=ce_tr_add_one(q,q->events+i,r); if(rc) return rc; }
    return 0;
}
static int ce_tr_merge(const ce_traj_request *q,ce_traj_report *r) {
    int32_t *indices=NULL; uint8_t *values=NULL; int rc=0;
    uint8_t ce_tr_touch[512];
    if(!q->event_count) return 0;
    if((uint64_t)q->event_count>SIZE_MAX/sizeof(int32_t)) return -1;
    if(q->event_count) {
        indices=(int32_t*)malloc((size_t)q->event_count*sizeof(int32_t));
        values=(uint8_t*)malloc((size_t)q->event_count);
        if(!indices || !values) { rc=-2; goto done; }
    }
    ++r->merge;
    for(int64_t i=0;i<q->event_count;++i) {
        ce_tr_big value={0}; int byte;
        rc=ce_tr_decode(q,&q->events[i].value,&value);
        if(!rc && !ce_tr_byte_value(&value,&byte)) { ce_tr_big_clear(&value); rc=ce_tr_semantic(r,CE_TR_BYTE,i); goto done; }
        ce_tr_big_clear(&value); if(rc) goto done;
        indices[i]=q->events[i].offset; values[i]=(uint8_t)byte;
    }
    ce_layer_sparse_write(indices,values,q->event_count,q->frame,q->mask,0,ce_tr_touch,NULL);
    r->values+=(uint64_t)q->event_count;
done:
    free(indices); free(values); return rc;
}
