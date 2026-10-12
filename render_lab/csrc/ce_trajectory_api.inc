static int ce_tr_count(int64_t count,size_t width) {
    return count>=0 && (uint64_t)count<=SIZE_MAX/width;
}
static int ce_tr_validate(const ce_traj_request *q,int op) {
    if(!q || q->abi!=CE_TR_ABI || q->struct_size!=sizeof(*q) || op<0 || op>4) return -1;
    if(!ce_tr_count(q->time_count,sizeof(ce_traj_time))
       || !ce_tr_count(q->sample_count,sizeof(ce_traj_sample))
       || !ce_tr_count(q->point_count,sizeof(ce_traj_value))
       || !ce_tr_count(q->applier_count,sizeof(ce_traj_applier))
       || !ce_tr_count(q->offset_count,sizeof(ce_traj_offset))
       || !ce_tr_count(q->event_count,sizeof(ce_traj_event))
       || !ce_tr_count(q->event_capacity,sizeof(ce_traj_event))) return -1;
    if(q->integer_size<0 || q->byte_capacity<0 || q->event_capacity<0) return -1;
    if(q->integer_size && !q->integers) return -1;
    if(q->byte_capacity && !q->bytes_out) return -1;
    if(op==0 && (q->time_count<0 || (q->time_count && (!q->times || !q->time_out)))) return -1;
    if(op==1 && (q->sample_count<0 || q->point_count<0 || (q->sample_count && (!q->samples || !q->results))
                || (q->point_count && (!q->points || !q->timestamps)))) return -1;
    if(op==2 && (q->applier_count<0 || q->offset_count<0 || (q->applier_count && !q->appliers)
                || (q->offset_count && !q->offsets))) return -1;
    if((op==2 || op==3) && q->event_capacity && !q->event_out) return -1;
    if((op==3 || op==4) && (q->event_count<0 || (q->event_count && !q->events))) return -1;
    if(op==4 && (!q->frame || !q->mask)) return -1;
    if(op==2) for(int64_t i=0;i<q->offset_count;++i) if(q->offsets[i].offset<0 || q->offsets[i].offset>=512) return -1;
    if(op==3 || op==4) for(int64_t i=0;i<q->event_count;++i) if(q->events[i].offset<0 || q->events[i].offset>=512) return -1;
    return 0;
}
CE_API int ce_trajectory_eval(int operation,const ce_traj_request *q,ce_traj_report *r) {
    int rc;
    if(!r) return -1;
    memset(r,0,sizeof(*r)); r->index=-1;
    rc=ce_tr_validate(q,operation); if(rc) { r->status=rc; return rc; }
    if((operation==2 || operation==3) && q->event_capacity)
        memset(q->event_out,0,(size_t)q->event_capacity*sizeof(*q->event_out));
    if((operation==2 || operation==3) && q->byte_capacity)
        memset(q->bytes_out,0,(size_t)q->byte_capacity);
    if(operation==0) {
        if(q->time_count) memset(q->time_out,0,(size_t)q->time_count*sizeof(*q->time_out));
        rc=ce_tr_times(q,r);
    } else if(operation==1) {
        if(q->sample_count) memset(q->results,0,(size_t)q->sample_count*sizeof(*q->results));
        rc=ce_tr_samples(q,r);
    } else if(operation==2) rc=ce_tr_appliers(q,r);
    else if(operation==3) rc=ce_tr_add_events(q,r);
    else rc=ce_tr_merge(q,r);
    r->status=rc; return rc;
}
