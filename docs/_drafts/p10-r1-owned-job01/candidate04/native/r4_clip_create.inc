/* Exact retained clip setup except the already bounded C track selector. */
static int r4_clip_create_bound(r3_job* job,const r3_clip_raw* raw,const r1_atom* timer,uint64_t track)
{
    r3_clip* clip=job->clips+job->compile_clip;r1_input input=raw->recorded;
    double values[R1_ATOMS];r1_report report;int status;
    if (track>=job->tracks_n || input.state || (raw->channel_presence_count && !raw->channel_presence)) return R1_PENDING;
    clip->track_index=(uint64_t)track;input.byte_budget=job->clock.ledger->limit;input.atoms[R1_TIMER_MS]=*timer;
    input.frames=(const r1_raw_frame*)raw->scatter; /* shape-only, r1_gate does not traverse */
    status=r1_gate(&input,values,&report);if (status) {job->pending_features|=report.pending_features;return status;}
    status=r3_extra(clip,raw);
    if (status) {job->pending_features|=clip->fi>0. ? R3_PENDING_FADEIN : R3_PENDING_MODEL;return status;}
    if (clip->lt && (job->lt_empty_mask!=7 || (clip->white && job->nm_present))) {
        job->pending_features|=R3_PENDING_LTHISTORY;return R1_PENDING;
    }
    clip->presence_n=raw->channel_presence_count;
    if (clip->presence_n>SIZE_MAX/sizeof(uint32_t)) return R1_BUDGET;
    clip->presence_bytes=clip->presence_n*sizeof(uint32_t);
    if (clip->presence_n) {
        clip->channel_presence=r1_allocate(job->clock.ledger,clip->presence_bytes);
        if (!clip->channel_presence) return R1_BUDGET;
        memcpy(clip->channel_presence,raw->channel_presence,(size_t)clip->presence_bytes);
    }
    clip->source=r1_allocate(job->clock.ledger,sizeof(r1_job));
    if (!clip->source) return R1_BUDGET;
    job->clock.ledger->refs++;clip->source->ledger=job->clock.ledger;clip->source->source_token=input.source_token;
    status=r1_clock_plan(clip->source,values);
    if (!status) {
        status=r1_compile_storage(clip->source,&input);clip->source->scatter=raw->scatter;
        clip->source->channels_borrowed=input.channels;
    }
    if (status) return status;
    if (clip->source->end>job->clock.end) job->clock.end=clip->source->end;
    job->compile_clip++;return R1_OK;
}
