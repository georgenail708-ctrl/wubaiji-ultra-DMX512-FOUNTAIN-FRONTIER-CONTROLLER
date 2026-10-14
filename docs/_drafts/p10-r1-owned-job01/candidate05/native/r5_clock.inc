static double r5_auto_hold(const r1_job* source,double local)
{
    local=_ce_rt_nonnegative(local);
    if (!source->cycle_valid || local<=source->cycle_last) return local;
    return source->cycle_first+_ce_clock_mod(local-source->cycle_first,source->cycle_period);
}

static int r5_source_clock(r5_job* job,uint64_t index,double position,int held)
{
    r3_clip* original=job->base->scene->clips+index;r1_job* source=original->source;r5_clip* clip=job->clips+index;
    int flags[16]={0};double values[24]={0},out[24]={0};
    clip->held=held!=0;clip->hold_extension=0;clip->in_pause=0;
    if (held) {
        flags[0]=clip->hold==R5_HOLD_CYCLE;values[0]=position;values[1]=source->start;
        values[2]=source->duration;values[3]=source->source_duration;values[4]=source->rate;
        _ce_rt_held_source(flags,values,out);clip->local=out[2];clip->source=flags[0] ? r5_auto_hold(source,out[1]) : out[1];
        clip->hold_extension=1;clip->gain=1.;return R1_OK;
    }
    flags[0]=clip->hold!=R5_HOLD_NONE;flags[1]=clip->hold_latch;
    values[0]=position;values[1]=source->start;values[2]=source->duration;values[3]=clip->interval;
    _ce_rt_local(flags,values,out);clip->local=out[0];clip->hold_extension=out[1]!=0.;
    if (out[2]>=0.) {clip->hold_latch=out[2]>0.;clip->hold_latch_present=out[2]>0.;}
    memset(flags,0,sizeof(flags));memset(values,0,sizeof(values));memset(out,0,sizeof(out));
    flags[0]=clip->sequential;flags[1]=(int)clip->batches;flags[2]=(int)clip->pause_count;
    flags[3]=clip->wing;flags[4]=source->resize;flags[5]=clip->motor_no_delay;flags[6]=clip->cyclic;
    values[0]=clip->local;values[1]=source->source_duration;values[2]=source->rate;values[3]=clip->single;values[4]=clip->step;
    values[5]=_ce_rt_nonnegative(source->source_duration-1e-4);
    if (source->cycle_valid) values[5]=_ce_rt_min_left(values[5],source->cycle_last);
    if (ce_source_motion_clock(3,flags,values,clip->pauses,out,NULL)) return R1_KERNEL;
    clip->in_pause=out[1]!=0.;double sequential=out[0];
    memset(flags,0,sizeof(flags));memset(values,0,sizeof(values));memset(out,0,sizeof(out));
    flags[0]=(int)clip->run;flags[1]=source->resize && source->cycle_valid;
    flags[2]=clip->run==R5_RUN_NORMAL ? source->extends_source : source->cycle_valid;
    values[0]=sequential;values[1]=source->source_duration;
    values[2]=_ce_rt_min_left(_ce_rt_nonnegative(source->cycle_last),source->source_duration);
    values[3]=source->cycle_first;values[4]=source->cycle_period;values[5]=source->cycle_last;
    if (ce_source_motion_clock(4,flags,values,NULL,out,NULL)) return R1_KERNEL;
    if (!out[1] && clip->hold==R5_HOLD_NONE) {clip->eligible=0;return R1_OK;}
    clip->source=clip->hold_extension ? _ce_rt_nonnegative(source->source_duration-1e-4)
        : clip->hold==R5_HOLD_CYCLE ? r5_auto_hold(source,out[0]) : _ce_rt_min_left(out[0],_ce_rt_nonnegative(source->source_duration-1e-4));
    clip->gain=clip->hold_extension ? 1. : ce_source_motion_fade(source->duration,original->fi,original->fo,
        clip->local,original->enabled,original->curve_in,original->curve_out,original->strategy_in,original->strategy_out);
    return R1_OK;
}
