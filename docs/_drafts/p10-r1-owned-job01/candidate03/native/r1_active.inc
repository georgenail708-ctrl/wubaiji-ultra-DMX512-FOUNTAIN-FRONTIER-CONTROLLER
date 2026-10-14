static int r1_upper_frame(const double* times, int count, double source)
{
    int left=0,right=count;
    while (left<right) {
        int middle=left+(right-left)/2;
        if (source<times[middle]) right=middle;
        else left=middle+1;
    }
    return left>0 ? left-1 : 0;
}

static int r1_active(const r1_job* job, const r1_stream* stream,
                      double source, const uint8_t* raw, uint8_t* active)
{
    int count=0, index=r1_upper_frame(job->times+stream->begin,(int)stream->count,source+1e-9);
    if (stream->count==1) {
        memcpy(active,stream->base,512);
    } else {
        for (int c=0;c<512;c++) {
            int touched=raw[c]>0;
            if (index>0) touched|=raw[c]!=r1_source_row(job,stream->begin+(uint64_t)(index-1))[c];
            if ((uint64_t)(index+1)<stream->count) touched|=raw[c]!=r1_source_row(job,stream->begin+(uint64_t)(index+1))[c];
            active[c]=(uint8_t)(stream->base[c] && touched);
        }
    }
    for (int c=0;c<512;c++) count+=active[c]!=0;
    return count;
}

static double r1_source_time(const r1_job* job, double position)
{
    int flags[16]={0,1,0};
    double values[24]={0}, out[24]={0};
    values[0]=_ce_rt_nonnegative(position-job->start)*job->rate;
    flags[1]=job->resize && job->cycle_valid; flags[2]=job->extends_source;
    values[1]=job->source_duration; values[3]=job->cycle_first;
    values[4]=job->cycle_period; values[5]=job->cycle_last;
    ce_source_motion_clock(4,flags,values,NULL,out,NULL);
    values[0]=out[0]; memset(out,0,sizeof(out));
    flags[1]=1; flags[2]=0;
    values[1]=job->source_duration; values[2]=position-job->start; values[3]=job->duration;
    _ce_rt_active_source(flags,values,out);
    return out[0];
}

static double r1_position(const r1_job* job, uint64_t index)
{
    int flags[16]={0};
    double values[24]={0}, out[24]={0};
    values[0]=(double)index; values[1]=job->interval; values[2]=job->end;
    ce_source_motion_clock(26,flags,values,NULL,out,NULL);
    return out[0];
}

static void r1_score_scalars(const r1_job* job, ce_prepared_task* task)
{
    int flags[16]={0,1};
    double values[24]={0}, out[24]={0};
    values[0]=job->rate; values[1]=job->start; values[2]=job->duration;
    values[3]=1.; values[4]=job->track_order; values[5]=1.;
    ce_source_motion_clock(14,flags,values,NULL,out,NULL);
    task->start=job->start; task->rate=out[0]; task->hold_cap=out[1];
    task->tie=out[2]; task->preboost=out[3];
}
