static int r5_pause_one(r5_clip* clip,const r5_clip_raw* raw)
{
    double value=clip->pause_fallback;
    uint64_t index=clip->pause_cursor++;
    if (index<raw->pauses_n) {
        int status=r5_number(raw->pauses[index],0.,&value);
        if (status) value=clip->pause_fallback;
        else value=_ce_rt_nonnegative(value);
    }
    clip->pauses[index]=value;return R1_OK;
}

static int r5_sequence_prepare(r5_job* job,r5_clip* clip,const r5_clip_raw* raw)
{
    r1_ledger* ledger=job->base->scene->clock.ledger;double value;
    int status=r5_positive_integer(raw->values[R5_GROUPS],1.,&clip->groups);if (status) return status;
    if (r5_number(raw->values[R5_BATCHES],0.,&value) || !isfinite(value)) value=0.;
    value=trunc(value);clip->batches=value<=0. ? clip->groups : value>clip->groups ? clip->groups : (uint32_t)value;
    if (!clip->batches) clip->batches=1;
    if (r5_number(raw->values[R5_PAUSE],0.,&value)) return R1_PENDING;clip->pause_fallback=_ce_rt_nonnegative(value);
    if (r5_number(raw->values[R5_SINGLE],0.,&value)) return R1_PENDING;clip->single=_ce_rt_nonnegative(value);
    if (r5_number(raw->values[R5_DELAY_IN],0.,&value)) return R1_PENDING;clip->delay_in=_ce_rt_nonnegative(value);
    if (r5_number(raw->values[R5_DELAY_OUT],0.,&value)) return R1_PENDING;clip->delay_out=_ce_rt_nonnegative(value);
    if (raw->pauses_n && !raw->pauses) return R1_INVALID;
    clip->pause_count=clip->batches-1;clip->pause_bytes=clip->pause_count*sizeof(double);
    if (clip->pause_count) {clip->pauses=r1_allocate(ledger,clip->pause_bytes);if (!clip->pauses) return R1_BUDGET;}
    clip->compile_stage=1;return R1_OK;
}

static int r5_sequence_window(r5_job* job,r5_clip* clip)
{
    r3_clip* source=job->base->scene->clips+job->row;
    int wing_set=clip->single>1e-4 || clip->delay_in>1e-4 || (source->fi>1e-4 && clip->raw_dir_in_set);
    clip->sequential=clip->sequential && clip->groups>1 && wing_set;
    clip->wing=clip->delay_in>1e-4 || (source->fi>1e-4 && clip->raw_dir_in_set);
    clip->step=clip->single;
    if (clip->sequential) {
        int flags[16]={(int)clip->batches,(int)clip->pause_count,(int)clip->wing};
        double values[24]={source->source->duration,clip->single,clip->delay_in,clip->raw_dir_in_set ? source->fi : 0.},out[24]={0};
        /* Input/output pauses may alias: kernel sums the complete source list
         * before touching output and then updates one corresponding entry. */
        if (ce_source_motion_clock(1,flags,values,clip->pauses,out,clip->pauses)) return R1_KERNEL;
        clip->single=out[0];clip->step=out[1]<0. ? clip->single : out[1];
    }
    if (!isfinite(clip->single) || !isfinite(clip->step)) return R1_PENDING;
    clip->compile_stage=2;return R1_OK;
}

static int r5_sample_extrema_one(r5_job* job,r5_clip* clip)
{
    r1_job* source=job->base->scene->clips[job->row].source;
    r1_stream* stream=source->streams+clip->primary_slot;
    uint64_t index=clip->sample_cursor++,local=index*clip->sample_step;
    if (local>=stream->count) local=stream->count-1;
    const uint8_t* data=r1_source_row(source,stream->begin+local);
    for (int c=0;c<512;c++) {
        clip->sampled[index*512+c]=(float)data[c];
        if (!index || data[c]<clip->min_value[c]) clip->min_value[c]=data[c];
        if (data[c]>clip->max_value[c]) clip->max_value[c]=data[c];
    }
    if (clip->sample_cursor==clip->sample_count) {clip->compile_stage=4;clip->cross_channel=0;}
    return R1_OK;
}

static int r5_cyclic_column_one(r5_clip* clip)
{
    int c=(int)clip->cross_channel++,moving=clip->min_value[c]!=clip->max_value[c];
    if (moving && clip->channel<48) {
        int mid=((int)clip->min_value[c]+(int)clip->max_value[c])/2,count=0;
        for (uint64_t r=1;r<clip->sample_count;r++) count+=(clip->sampled[r*512+c]>mid)!=(clip->sampled[(r-1)*512+c]>mid);
        clip->crossings[c]=(uint32_t)count;clip->channel++;clip->cross_sum+=count;
    }
    if (clip->cross_channel==512) {
        clip->cyclic=clip->channel && (double)clip->cross_sum/(double)clip->channel>2.5;clip->compile_stage=5;
    }
    return R1_OK;
}
