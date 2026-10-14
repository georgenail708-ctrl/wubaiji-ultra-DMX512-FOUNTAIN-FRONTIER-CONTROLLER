CE_API int ce_owned_scene_begin_r4(const r4_input* input,r4_job** output,r3_report* report)
{
    r3_job* scene;r4_job* job;int status;
    if (!output || !report) return R1_INVALID;*output=NULL;memset(report,0,sizeof(*report));
    report->abi=R4_ABI;report->struct_size=sizeof(*report);status=r4_shape(input);
    if (status) {report->status=status;return status;}
    scene=r3_allocate_job(&input->raw);if (!scene) {report->status=R1_BUDGET;return R1_BUDGET;}
    job=r1_allocate(scene->clock.ledger,sizeof(*job));
    if (!job) {ce_owned_scene_destroy_r3(scene);report->status=R1_BUDGET;return R1_BUDGET;}
    job->scene=scene;job->borrowed=input;job->selected=UINT64_MAX;
    r4_external_metadata(input,&job->external_min);
    job->clip_bytes=scene->clips_n*sizeof(r4_clip);job->clips=r1_allocate(scene->clock.ledger,job->clip_bytes);
    status=job->clips ? R1_OK : R1_BUDGET;
    if (!status) status=r3_scene_clock(scene,&input->raw);
    if (!status && (input->request.mode || input->request.reserved)) {
        /* G0: explicit range/FPS/audio-end contracts are not guessed from a
         * playback timer. They remain distinct pending original-oracle ports. */
        scene->pending_features|=R4_PENDING_GRID;status=R1_PENDING;
    }
    if (!status) status=r4_model_allocate(job,&input->raw);
    r4_report_current(job,report,status ? status : R1_BUILDING);
    if (status) {ce_owned_scene_destroy_r4(job);return status;}
    *output=job;return R1_BUILDING;
}

static int r4_compile_one(r4_job* job)
{
    switch(job->phase) {
    case 0:return r4_model_one(job);
    case 1:{
        int status=r4_tracks_step(&job->tracks,1);
        if (status==R1_BUILDING) return R1_OK;
        if (!status && job->tracks.out!=job->tracks.count) {job->scene->pending_features|=R4_PENDING_ALIAS;return R1_PENDING;}
        if (!status) {job->phase=2;job->row=job->scan=0;job->selected=UINT64_MAX;}
        return status;
    }
    case 2:return r4_clip_create_one(job);
    case 3:return r4_source_sort_one(job);
    case 4:return r4_masks_one(job);
    case 5:return r4_source_finish_one(job);
    case 6:return r4_universe_one(job);
    case 7:return r4_clip_state_one(job);
    case 8:{
        r3_job* scene=job->scene;double count=ceil(scene->clock.end/scene->clock.interval);
        if (!isfinite(count) || count>INT64_MAX-2.) return R1_PENDING;
        scene->clock.total_frames=(uint64_t)count+1;scene->compile_phase=3;
        job->borrowed=NULL;job->selected_active=job->selected_owner=NULL;job->selected_filter=NULL;
        memset(&job->active,0,sizeof(job->active));memset(&job->base,0,sizeof(job->base));memset(&job->pairs,0,sizeof(job->pairs));
        job->phase=9;return R1_OK;
    }
    default:return R1_INVALID;
    }
}

CE_API int ce_owned_scene_compile_r4(r4_job* job,uint32_t units,r3_report* report)
{
    int status=R1_OK;
    if (!job || !report || !units || units>65536) return R1_INVALID;
    if (job->cancelled) status=R1_CANCELLED;
    else if (job->status) status=job->status;
    else while (units-- && job->phase!=9 && !status) status=r4_compile_one(job);
    if (status<0) {
        job->status=job->scene->clock.terminal_error=status;
        if (status==R1_PENDING && !job->scene->pending_features) job->scene->pending_features=R3_PENDING_MODEL;
    }
    if (!status && job->phase!=9) status=R1_BUILDING;
    r4_report_current(job,report,status);return status;
}
