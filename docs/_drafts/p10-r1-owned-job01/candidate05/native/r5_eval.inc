static int r5_task_row(r5_job* owner,uint64_t row,uint64_t index,uint64_t slot)
{
    r3_job* scene=owner->base->scene;r3_clip* original=scene->clips+index;r5_clip* clip=owner->clips+index;
    r1_job* source=original->source;r1_stream* stream=source->streams+slot;r4_clip* extra=owner->base->clips+index;
    r4_slot* mask=extra->slots+slot;ce_prepared_task* task=scene->tasks+row;r4_mask_selection selected;int count=0;
    double time=clip->source;int frame=ce_frame_index(source->times+stream->begin,(int)stream->count,time);
    uint8_t *raw=scene->lt_raw+row*512,*active=scene->candidate_active+row*512;
    const uint8_t* recorded=r1_source_row(source,stream->begin+(uint64_t)frame);
    r3_lt_call call={slot,clip->local,recorded,raw};memcpy(raw,recorded,512);r3_lt_apply(scene,original,&call);
    r4_cached_select(&extra->events,mask->event_domain,time,&selected);
    if (clip->sequential) {
        for (int c=0;c<512;c++) active[c]=(stream->base[c] || raw[c]) && (!mask->base_present || mask->base_order[c]);
    } else if (selected.kind!=R4_MASK_MISSING) r4_recorded_active(&selected,stream->base,active);
    else if (clip->hold_extension) memcpy(active,stream->base,512);
    else r1_active(source,stream,time,raw,active);
    if (original->lt) for (int c=0;c<512;c++) active[c]|=original->lt_slots[slot].light[c];
    r4_rule_active(&selected,mask->rule.present ? mask->rule.mask : NULL,active);
    clip->real_hold=clip->hold_extension && clip->local>=source->duration-1e-9;
    if (clip->real_hold) ce_domain_mask(3,512,active,clip->hold_mask+slot*512,active,&count);
    ce_domain_mask(3,512,active,original->consumed+slot*512,active,&count);if (!count) return 1;
    if (!stream->anchor_ready) {
        for (int c=0;c<512;c++) stream->anchor[c]=active[c] ? ((float)stream->minimum[c]+(float)stream->maximum[c])/2.f : 0.f;
        stream->anchor_ready=1;
    }
    float* gain=scene->gains+row*512;memcpy(scene->anchors+row*512,stream->anchor,512*sizeof(float));
    memset(task,0,sizeof(*task));task->raw=raw;task->active=active;task->gains=gain;task->changes=scene->changes+row*512;
    r1_sparse_lookup(source,stream,(uint64_t)frame,scene->changes+row*512);
    for (int c=0;c<512;c++) gain[c]=(float)clip->gain;
    uint32_t u=scene->universe_map[stream->universe]-1;task->base=scene->idle+(size_t)u*512;task->idle=task->base;
    scene->row_clip[row]=index;scene->row_stream[row]=slot;return R1_OK;
}

static void r5_task_timing(r5_job* owner,uint64_t index,uint64_t priority,ce_prepared_task* task)
{
    r3_job* scene=owner->base->scene;r3_clip* original=scene->clips+index;r5_clip* clip=owner->clips+index;
    int flags[16]={clip->hold_extension!=0,1};double values[24]={0},out[24]={0};
    values[0]=original->source->rate;values[1]=original->source->start;values[2]=original->source->duration;
    values[3]=(double)priority;values[4]=original->source->track_order;values[5]=(double)scene->active_count;
    ce_source_motion_clock(14,flags,values,NULL,out,NULL);
    task->start=values[1];task->rate=out[0];task->hold_cap=out[1];task->tie=out[2];task->preboost=out[3];
    memset(flags,0,sizeof(flags));memset(values,0,sizeof(values));
    flags[0]=clip->hold_extension;flags[1]=clip->sequential;flags[2]=clip->dir_in!=0;flags[3]=clip->dir_out!=0;flags[4]=1;flags[5]=original->lt;
    values[0]=clip->local;values[1]=original->fi;values[2]=original->fo;values[3]=original->source->duration;
    values[4]=clip->delay_in;values[5]=clip->delay_out;values[6]=clip->step*(clip->batches-1)+clip->single;
    for (uint64_t p=0;p<clip->pause_count;p++) values[6]+=clip->pauses[p];
    values[8]=(double)scene->active_count;ce_render_timing_eval(3,flags,values,out);
    task->mode=out[7]!=0.;task->ramp=0;task->boost=out[13];
}

static int r5_prepare_rows(r5_job* owner,uint64_t* output)
{
    r3_job* scene=owner->base->scene;uint64_t row=0;
    for (uint64_t rank=0;rank<scene->active_count;rank++) {
        uint64_t index=(uint64_t)scene->order[rank];r3_clip* clip=scene->clips+index;
        for (uint64_t s=0;s<clip->source->streams_n;s++) {
            int status=r5_task_row(owner,row,index,s);if (status<0) return status;
            if (!status) {r5_task_timing(owner,index,scene->active_count-rank,scene->tasks+row);row++;}
        }
    }
    *output=row;return R1_OK;
}
