/* Actual existing CE direction descriptor assembled from the native compiled
 * plan. No per-fixture host call and no uncharged kernel malloc. */
static double r6_sequence_span(const r5_clip* clip)
{
    double span=clip->step*(clip->batches-1)+clip->single;
    for (uint64_t i=0;i<clip->pause_count;i++) span+=clip->pauses[i];return span;
}

static void r6_direction_descriptor(const r6_transition_owner* owner,uint64_t index,
                                   uint64_t stream,double local,ce_source_direction* out)
{
    const r3_clip* original=owner->timeline->base->scene->clips+index;
    const r5_clip* clip=owner->timeline->clips+index;r6_slot_plan* slot=owner->geometry->clips[index].slots+stream;
    memset(out,0,sizeof(*out));out->K=(int)slot->local_order.n;out->batch_count=(int)clip->batches;
    out->mode=clip->sequential ? 2 : 1;out->te=original->enabled;
    out->curve_in=original->curve_in;out->curve_out=original->curve_out;
    out->strategy_in=original->strategy_in;out->strategy_out=original->strategy_out;
    out->duration=original->source->duration;out->fade_in=_ce_rt_min_left(original->fi,out->duration);
    out->fade_out=_ce_rt_min_left(original->fo,out->duration);out->local_t=local;
    out->delay_in=clip->delay_in;out->delay_out=clip->delay_out;out->single=clip->single;out->step=clip->step;
    out->scale_in=out->scale_out=1.;out->motor_no_delay=clip->motor_no_delay;
    out->is_in=clip->dir_in && ((out->fade_in>1e-4 && local<out->fade_in)
        || (out->delay_in>1e-4 && local<out->delay_in+out->fade_in)
        || (clip->sequential && local<_ce_rt_max_left(1e-4,r6_sequence_span(clip))));
    out->is_out=clip->dir_out && ((out->fade_out>1e-4 && local>out->duration-out->fade_out)
        || (out->delay_out>1e-4 && local>out->duration-(out->delay_out+out->fade_out)));
    out->completed_enabled=clip->sequential;out->span_idx=slot->span_index;out->motor=slot->motor.mask;
    out->pauses=clip->pauses;out->gi_in=slot->in_groups.gi;out->gp_in=slot->in_groups.gp;out->gs_in=slot->in_groups.gs;
    out->gi_out=slot->out_groups.gi;out->gp_out=slot->out_groups.gp;out->gs_out=slot->out_groups.gs;
}

static int r6_frame_gains(const r6_transition_owner* owner,uint64_t clip,uint64_t stream,
                          double local,int held,float* out)
{
    r6_clip_plan* plan=owner->geometry->clips+clip;r6_slot_plan* slot=plan->slots+stream;
    const r3_clip* original=owner->timeline->base->scene->clips+clip;
    if (held || plan->scalar_only) {
        double value=held ? 1. : ce_source_motion_fade(original->source->duration,original->fi,original->fo,local,
            original->enabled,original->curve_in,original->curve_out,original->strategy_in,original->strategy_out);
        for (int c=0;c<512;c++) out[c]=(float)value;
        memset(slot->completed,0,512);for (int c=0;c<512;c++) slot->arrived[c]=out[c]>1e-6f;return R1_OK;
    }
    ce_source_direction request;r6_direction_descriptor(owner,clip,stream,local,&request);
    return r5_direction_kernel(&request,slot->direction_scratch,out,slot->completed,slot->arrived);
}

static void r6_main_windows(const r5_job* owner,uint64_t index,double* out)
{
    const r3_clip* original=owner->base->scene->clips+index;const r5_clip* clip=owner->clips+index;
    int flags[16]={clip->hold_extension,clip->sequential,clip->dir_in!=0,clip->dir_out!=0,1,original->lt};
    double values[24]={0};values[0]=clip->local;values[1]=original->fi;values[2]=original->fo;
    values[3]=original->source->duration;values[4]=clip->delay_in;values[5]=clip->delay_out;
    values[6]=r6_sequence_span(clip);values[8]=(double)owner->base->scene->active_count;
    ce_render_timing_eval(3,flags,values,out);
}
