static int r3_work_allocate(r3_job* job)
{
    uint64_t n=job->slots_n,u=job->clock.streams_n,bytes=0;
    uint8_t* cursor;
    if (n>INT_MAX/2 || u>INT_MAX/1024) return R1_BUDGET;
    if (!r3_add_bytes(&bytes,n,sizeof(ce_prepared_task)+2*sizeof(uint64_t)+37*512+4*sizeof(int))
            || !r3_add_bytes(&bytes,2*u+1,sizeof(int))
            || !r3_add_bytes(&bytes,2*u*512,sizeof(int))) return R1_BUDGET;
    job->work_bytes=bytes;job->work=r1_allocate(job->clock.ledger,bytes);
    if (!job->work) return R1_BUDGET;
    cursor=(uint8_t*)job->work;
#define R3_VIEW(field,type,count) do {job->field=(type*)cursor;cursor+=(count)*sizeof(type);} while(0)
    R3_VIEW(tasks,ce_prepared_task,n);
    R3_VIEW(row_clip,uint64_t,n);R3_VIEW(row_stream,uint64_t,n);
    R3_VIEW(scores,double,n*512);
    R3_VIEW(candidate_float,float,n*512);R3_VIEW(anchors,float,n*512);
    R3_VIEW(gains,float,n*512);R3_VIEW(changes,float,n*512);
    R3_VIEW(flat,int,n*2);R3_VIEW(counts,int,n*2);
    R3_VIEW(offsets,int,2*u+1);R3_VIEW(winner,int,2*u*512);
    R3_VIEW(candidate_data,uint8_t,n*512);R3_VIEW(candidate_active,uint8_t,n*512);
    R3_VIEW(lt_raw,uint8_t,n*512);R3_VIEW(effective,uint8_t,n*1024);
    R3_VIEW(data_eff,uint8_t,n*1024);R3_VIEW(mix_scratch,uint8_t,n*3072);
#undef R3_VIEW
    job->order_bytes=job->clips_n*sizeof(int64_t);job->order=r1_allocate(job->clock.ledger,job->order_bytes);
    return job->order ? R1_OK : R1_BUDGET;
}

static uint64_t r3_frame_order(r3_job* job,double position)
{
    uint64_t n=0;
    for (uint64_t i=0;i<job->clips_n;i++) {
        r3_clip* clip=job->clips+i;r1_job* source=clip->source;
        if (clip->allowed && source->start<=position && position<source->start+source->duration) job->order[n++]=(int64_t)i;
    }
    /* Native stable insertion on active clip count, never source-record count.
     * Exact _ClipSortKey: latest start then track order, stable equality. */
    for (uint64_t i=1;i<n;i++) {
        int64_t key=job->order[i];uint64_t j=i;r1_job* right=job->clips[key].source;
        while (j>0) {
            r1_job* left=job->clips[job->order[j-1]].source;
            if (!(right->start>left->start || (right->start==left->start && right->track_order>left->track_order))) break;
            job->order[j]=job->order[j-1];j--;
        }
        job->order[j]=key;
    }
    job->active_count=n;return n;
}

static void r3_lt_gap(r3_job* job)
{
    for (uint64_t i=0;i<job->active_count;i++) if (job->clips[job->order[i]].lt) return;
    if (job->fixtures_n) {
        memset(job->lt_exists,0,(size_t)job->fixtures_n*4);
        memset(job->lt_state,0,(size_t)job->fixtures_n*4*sizeof(double));
        memset(job->lt_time,0,(size_t)job->fixtures_n*4*sizeof(double));
    }
    r1_state_before_frame(&job->clock); /* colors and other seven domains remain */
}

static void r3_score_task(const r3_job* job,const r3_clip* clip,uint64_t priority,ce_prepared_task* task)
{
    int flags[16]={0,1};double values[24]={0},out[24]={0};
    values[0]=clip->source->rate;values[1]=clip->source->start;values[2]=clip->source->duration;
    values[3]=(double)priority;values[4]=clip->source->track_order;values[5]=(double)job->active_count;
    ce_source_motion_clock(14,flags,values,NULL,out,NULL);
    task->start=clip->source->start;task->rate=out[0];task->hold_cap=out[1];task->tie=out[2];task->preboost=out[3];
}

static void r3_fade_task(const r3_job* job,const r3_clip* clip,double local,ce_prepared_task* task)
{
    int flags[16]={0,0,0,0,1,clip->lt};double values[24]={0},windows[24]={0};
    values[0]=local;values[1]=clip->fi;values[2]=clip->fo;values[3]=clip->source->duration;values[8]=(double)job->active_count;
    ce_render_timing_eval(3,flags,values,windows);
    task->mode=windows[7]!=0.;task->ramp=0;task->boost=windows[13];
}
