static void r6_report_current(r6_job* job,r6_report* report,int status)
{
    memset(report,0,sizeof(*report));r3_report_current(job->timeline->base->scene,&report->base,status);report->base.abi=R6_ABI;
    report->base.struct_size=sizeof(*report);report->base.compile_phase=job->phase;
    report->base.compile_clip=job->row;report->base.publishable=0;report->semantic=job->semantic;
    report->grid_ready=job->grid_ready;report->grid_units=job->grid_state ? job->grid_state->completed_units : 0;
    report->base.pending_state|=UINT64_C(256); /* actual causal context/block still unfinished */
}

static int r6_input_shape(const r6_input* input)
{
    if (!input || input->abi!=R6_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    int status=r5_shape(&input->raw);if (status) return status;
    return (input->raw.raw.raw.clip_count && !input->geometry) || input->main_timer_present>1 ? R1_INVALID : R1_OK;
}

static int r6_timeline_create(r4_job* base,const r5_input* input,r5_job** output)
{
    r1_ledger* ledger=base->scene->clock.ledger;uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,base->scene->clips_n,sizeof(r5_clip))) return R1_BUDGET;
    r5_job* job=r1_allocate(ledger,sizeof(*job));if (!job) return R1_BUDGET;*output=job;
    job->base=base;job->borrowed=input;job->clip_bytes=bytes;job->clips=r1_allocate(ledger,bytes);
    if (!job->clips) return R1_BUDGET;return r1_atom_number(input->raw.raw.timer_ms,23.,&job->timer_ms);
}

CE_API int ce_owned_scene_begin_r6(const r6_input* input,r6_job** output,r6_report* report)
{
    r4_job* base=NULL;r5_job* timeline=NULL;r6_job* job=NULL;uint64_t bytes=0;int status;
    if (!output || !report) return R1_INVALID;*output=NULL;memset(report,0,sizeof(*report));
    report->base.abi=R6_ABI;report->base.struct_size=sizeof(*report);status=r6_input_shape(input);
    if (status) {
        report->base.status=status;report->base.pending_state=R1_STATE_PENDING;
        if (status==R1_PENDING) report->base.pending_features=R5_PENDING_STATE|R6_PENDING_CONTEXT;
        return status;
    }
    status=ce_owned_scene_begin_r4(&input->raw.raw,&base,&report->base);
    if (status!=R1_BUILDING) {report->base.abi=R6_ABI;report->base.struct_size=sizeof(*report);return status;}
    r1_ledger* ledger=base->scene->clock.ledger;
    status=r6_timeline_create(base,&input->raw,&timeline);
    if (status) {
        if (timeline) ce_owned_scene_destroy_r5(timeline);else ce_owned_scene_destroy_r4(base);
        report->base.abi=R6_ABI;report->base.struct_size=sizeof(*report);report->base.status=status;return status;
    }
    job=r1_allocate(ledger,sizeof(*job));
    if (!job) {ce_owned_scene_destroy_r5(timeline);report->base.abi=R6_ABI;report->base.struct_size=sizeof(*report);report->base.status=R1_BUDGET;return R1_BUDGET;}
    job->timeline=timeline;job->borrowed=input;job->external_min=base->external_min;
    job->authored_rows=input->raw.raw.raw.clips;job->authored_n=input->raw.raw.raw.clip_count;
    job->source_epoch=input->raw.raw.raw.source_token;job->model_epoch=input->model_epoch;job->fixture_epoch=input->fixture_epoch;
    job->transition=(r6_transition_owner){timeline,&job->geometry,input->raw.raw.raw.source_token};
    if (!r3_add_bytes(&bytes,base->scene->clips_n,sizeof(r6_clip_plan))) status=R1_BUDGET;
    job->geometry.bytes=bytes;job->geometry.clips_n=base->scene->clips_n;
    if (!status) {job->geometry.clips=r1_allocate(ledger,bytes);if (!job->geometry.clips) status=R1_BUDGET;}
    if (!status && (!r3_add_bytes(&job->external_min,base->scene->clips_n,sizeof(r6_clip_geometry_raw))
            || job->external_min>base->scene->external_bytes)) status=R1_BUDGET;
    if (status) {r6_report_current(job,report,status);ce_owned_scene_destroy_r6(job);return status;}
    *output=job;r6_report_current(job,report,R1_BUILDING);return R1_BUILDING;
}

static int r6_base_compile_one(r6_job* job)
{
    r4_job* base=job->timeline->base;
    if (base->phase==8) {
        int status=r6_main_grid_adopt(job);if (status) return status;
        base->scene->compile_phase=3;base->borrowed=NULL;
        base->selected_active=base->selected_owner=NULL;base->selected_filter=NULL;
        memset(&base->active,0,sizeof(base->active));memset(&base->base,0,sizeof(base->base));
        memset(&base->pairs,0,sizeof(base->pairs));base->phase=9;return R1_OK;
    }
    return base->phase==2 ? r6_clip_create_one(base) : r4_compile_one(base);
}
