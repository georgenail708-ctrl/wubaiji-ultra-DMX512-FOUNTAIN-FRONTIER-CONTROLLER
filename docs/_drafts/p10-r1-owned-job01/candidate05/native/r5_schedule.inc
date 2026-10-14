static int r5_hold_sort_before(const r3_clip* left,const r3_clip* right)
{
    double le=left->source->start+left->source->duration,re=right->source->start+right->source->duration;
    return le<re || (le==re && left->source->track_order<right->source->track_order);
}

static int r5_body_sort_before(const r3_clip* left,const r3_clip* right)
{
    double ls=left->source->start,rs=right->source->start;
    return ls<rs || (ls==rs && ls+left->source->duration<rs+right->source->duration);
}

static int r5_body_schedule(r5_job* job,double position)
{
    r3_job* scene=job->base->scene;int flags[16]={0};double values[24]={0},out[24]={0};
    flags[0]=isfinite(scene->clock.last_time);values[0]=job->timer_ms;values[1]=position;values[2]=scene->clock.last_time;
    if (ce_source_motion_clock(15,flags,values,NULL,out,NULL)) return R1_KERNEL;
    job->active_n=0;
    for (uint64_t cursor=0;cursor<scene->clips_n;cursor++) {
        uint64_t i=(uint64_t)job->body_order[cursor];
        r3_clip* source=scene->clips+i;r5_clip* clip=job->clips+i;clip->eligible=0;clip->held=0;clip->interval=out[0];
        if (!source->allowed || !(source->source->duration>0.) || position<source->source->start || !(position<source->source->start+source->source->duration)) continue;
        clip->eligible=1;int status=r5_source_clock(job,i,position,0);if (status) return status;
        if (clip->hold!=R5_HOLD_NONE && !clip->hold_extension) {
            memset(clip->hold_mask,0,(size_t)source->source->streams_n*512);
            memset(clip->hold_seen,0,(size_t)source->source->streams_n);clip->hold_outer_present=0;
        }
        if (clip->eligible) job->active_order[job->active_n++]=(int64_t)i;
    }
    return R1_OK;
}

static int r5_useful_hold(r5_job* job,uint64_t index)
{
    r3_job* scene=job->base->scene;r3_clip* source=scene->clips+index;int useful=0;
    for (uint64_t s=0;s<source->source->streams_n;s++) {
        r1_stream* stream=source->source->streams+s;r4_slot* mask=job->base->clips[index].slots+s;
        uint64_t u=scene->universe_map[stream->universe]-1;
        for (int c=0;c<512;c++) if (stream->base[c] && (!mask->rule.present || mask->rule.mask[c])) {
            useful|=!job->covered[u*512+c];job->covered[u*512+c]=1;
        }
    }
    return useful;
}

static int r5_hold_schedule(r5_job* job,double position)
{
    r3_job* scene=job->base->scene;job->hold_n=0;memset(job->covered,0,(size_t)job->coverage_bytes);
    for (uint64_t cursor=scene->clips_n;cursor>0;cursor--) {
        uint64_t index=(uint64_t)job->hold_order[cursor-1];r3_clip* source=scene->clips+index;r5_clip* clip=job->clips+index;
        if (!source->allowed || !(source->source->duration>0.) || clip->hold==R5_HOLD_NONE || position<source->source->start+source->source->duration) continue;
        if (!r5_useful_hold(job,index)) continue;
        clip->eligible=1;int status=r5_source_clock(job,index,position,1);if (status) return status;
        /* Original selected list is reversed before joining authored items. */
        job->active_order[scene->clips_n-1-job->hold_n++]=(int64_t)index;
    }
    for (uint64_t i=job->hold_n;i>0;i--) {
        int64_t selected=job->active_order[scene->clips_n-i];
        job->active_order[job->active_n++]=selected;
    }
    return R1_OK;
}

static void r5_priority_sort(r5_job* job)
{
    r3_job* scene=job->base->scene;
    for (uint64_t i=1;i<job->active_n;i++) {
        int64_t key=job->active_order[i];uint64_t j=i;r1_job* right=scene->clips[key].source;
        while (j) {
            r1_job* left=scene->clips[job->active_order[j-1]].source;
            if (!(right->start>left->start || (right->start==left->start && right->track_order>left->track_order))) break;
            job->active_order[j]=job->active_order[j-1];j--;
        }
        job->active_order[j]=key;
    }
    memcpy(scene->order,job->active_order,(size_t)job->active_n*sizeof(int64_t));scene->active_count=job->active_n;
}
