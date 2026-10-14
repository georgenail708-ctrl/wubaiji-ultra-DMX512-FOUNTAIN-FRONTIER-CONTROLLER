static r3_job* r3_allocate_job(const r3_input* input)
{
    r1_ledger* ledger=(r1_ledger*)calloc(1,sizeof(r1_ledger));r3_job* job;
    if (!ledger) return NULL;
    ledger->limit=input->byte_budget-R1_KERNEL_RESERVE;
    ledger->used=ledger->peak=sizeof(*ledger)+input->external_bytes;ledger->refs=1;
    job=r1_allocate(ledger,sizeof(*job));
    if (!job) {free(ledger);return NULL;}
    job->clock.ledger=ledger;job->clock.source_token=input->source_token;job->clock.last_time=NAN;
    job->external_bytes=input->external_bytes;
    job->clips_n=input->clip_count;job->tracks_n=input->track_count;
    job->fixtures_n=input->fixture_count;job->definitions_n=input->definition_count;job->defaults_n=input->default_count;
    job->lt_empty_mask=input->lt_empty_mask;job->nm_present=input->nm_present;
    job->clips_bytes=job->clips_n*sizeof(r3_clip);job->clips=r1_allocate(ledger,job->clips_bytes);
    job->map_bytes=R1_UNIVERSE_LIMIT*sizeof(uint32_t);job->universe_map=r1_allocate(ledger,job->map_bytes);
    if (!job->clips || !job->universe_map) {ce_owned_scene_destroy_r3(job);return NULL;}
    return job;
}

static int r3_scene_clock(r3_job* job,const r3_input* input)
{
    double timer;
    if (input->timer_ms.kind==R1_NONE || r1_atom_number(input->timer_ms,23.,&timer)
            || r1_atom_number(input->modulation_gain,1.,&job->modulation_gain)) return R1_PENDING;
    job->clock.interval=_ce_rt_min_left(_ce_rt_max_left(_ce_rt_max_left(.005,timer/1000.),.015),.05);
    return r1_state_create(&job->clock,input->state);
}

static int r3_clip_create(r3_job* job,const r3_clip_raw* raw,const r1_atom* timer)
{
    r3_clip* clip=job->clips+job->compile_clip;r1_input input=raw->recorded;
    double values[R1_ATOMS];r1_report report;int status;
    int64_t track=r3_track_index(job,raw->track_token);
    if (track<0 || input.state || (raw->channel_presence_count && !raw->channel_presence)) return R1_PENDING;
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

CE_API int ce_owned_scene_begin_r3(const r3_input* input,r3_job** output,r3_report* report)
{
    r3_job* job;int status;
    if (!output || !report) return R1_INVALID;
    *output=NULL;memset(report,0,sizeof(*report));report->abi=R3_ABI;report->struct_size=sizeof(*report);
    status=r3_shape(input);if (status) {report->status=status;return status;}
    job=r3_allocate_job(input);if (!job) {report->status=R1_BUDGET;return R1_BUDGET;}
    status=r3_scene_clock(job,input);if (!status) status=r3_copy_model(job,input);
    for (uint64_t i=0;i<job->clips_n && !status;i++) status=r3_clip_create(job,input->clips+i,&input->timer_ms);
    if (!status) status=r3_flat_tracks(job);
    double total=ceil(job->clock.end/job->clock.interval);
    if (!isfinite(total) || total>INT64_MAX-2.) status=R1_PENDING;
    if (!status) {job->clock.total_frames=(uint64_t)total+1;job->compile_clip=0;}
    if (status==R1_PENDING && !job->pending_features) job->pending_features=R3_PENDING_MODEL;
    r3_report_current(job,report,status);
    if (status) {ce_owned_scene_destroy_r3(job);return status;}
    *output=job;report->status=R1_BUILDING;return R1_BUILDING;
}
