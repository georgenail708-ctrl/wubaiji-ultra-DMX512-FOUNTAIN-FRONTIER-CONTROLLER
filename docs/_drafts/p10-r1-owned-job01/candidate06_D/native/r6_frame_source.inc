/* Frozen recorded output basis is an identity in the ACTUAL current original
 * _prepare_compiled_clip_frame_for_output_basis. Force/ratio/ease custom raw
 * domains remain admitted separately; no host precomputed frame replaces it. */
static int r6_frame_source(r6_job* job,uint64_t index,uint64_t stream,uint8_t* out,
                           const uint8_t** endpoint,int* remapped)
{
    r3_clip* original=job->timeline->base->scene->clips+index;r1_job* source=original->source;
    r1_stream* rows=source->streams+stream;r5_clip* clip=job->timeline->clips+index;
    r6_clip_plan* plan=job->geometry.clips+index;r6_slot_plan* slot=plan->slots+stream;
    *remapped=0;int frame=ce_frame_index(source->times+rows->begin,(int)rows->count,clip->source);
    if (frame<0 || (uint64_t)frame>=rows->count) return R1_KERNEL;
    const uint8_t* raw=r1_source_row(source,rows->begin+(uint64_t)frame);memcpy(out,raw,512);
    double end=_ce_rt_nonnegative(source->source_duration-1e-4);
    int last=ce_frame_index(source->times+rows->begin,(int)rows->count,end);
    if (last<0 || (uint64_t)last>=rows->count) return R1_KERNEL;
    *endpoint=r1_source_row(source,rows->begin+(uint64_t)last);
    if (clip->held || plan->scalar_only || !clip->dir_in || !slot->copies.count
            || (!clip->sequential && clip->motor_no_delay)) return R1_OK;
    double windows[24]={0};r6_main_windows(job->timeline,index,windows);
    if (!(windows[8] || windows[10])) return R1_OK; /* original remap call event */
    ce_source_remap phase={0};phase.pauses=clip->pauses;phase.batch_count=(int)clip->batches;
    phase.seed=!clip->sequential;phase.cyclic=clip->cyclic;phase.motor_no_delay=clip->motor_no_delay;
    phase.parallel=!clip->sequential;phase.phase_enabled=!clip->motor_no_delay;
    phase.local_t=clip->local;phase.source_t=clip->source;phase.duration=source->source_duration;
    phase.single=clip->single;phase.step=clip->step;phase.delay_in=clip->delay_in;phase.fade_in=original->fi;
    r6_remap_call call={slot->copies.rows,slot->copies.count,&phase,raw};
    int status=r6_source_remap(source,&call,&slot->samples,out);if (!status) *remapped=1;return status;
}

static int r6_row_active(r5_job* owner,uint64_t index,uint64_t slot,const uint8_t* raw,uint8_t* active)
{
    r3_clip* original=owner->base->scene->clips+index;r5_clip* clip=owner->clips+index;
    r1_job* source=original->source;r1_stream* stream=source->streams+slot;
    r4_clip* extra=owner->base->clips+index;r4_slot* mask=extra->slots+slot;r4_mask_selection selected;
    r4_cached_select(&extra->events,mask->event_domain,clip->source,&selected);
    if (clip->sequential) {
        for (int c=0;c<512;c++) active[c]=(stream->base[c] || raw[c]) && (!mask->base_present || mask->base_order[c]);
    } else if (selected.kind!=R4_MASK_MISSING) r4_recorded_active(&selected,stream->base,active);
    else if (clip->hold_extension) memcpy(active,stream->base,512);
    else r1_active(source,stream,clip->source,raw,active);
    if (original->lt) for (int c=0;c<512;c++) active[c]|=original->lt_slots[slot].light[c];
    r4_rule_active(&selected,mask->rule.present ? mask->rule.mask : NULL,active);
    clip->real_hold=clip->hold_extension && clip->local>=source->duration-1e-9;int count=0;
    if (clip->real_hold) ce_domain_mask(3,512,active,clip->hold_mask+slot*512,active,&count);
    ce_domain_mask(3,512,active,original->consumed+slot*512,active,&count);return count;
}
