CE_API int ce_owned_scene_begin_r5(const r5_input* input,r5_job** output,r3_report* report)
{
    r4_job* base=NULL;r5_job* job;int status;
    if (!output || !report) return R1_INVALID;*output=NULL;memset(report,0,sizeof(*report));
    report->abi=R5_ABI;report->struct_size=sizeof(*report);status=r5_shape(input);
    if (status) {report->status=status;report->pending_features=status==R1_PENDING ? R5_PENDING_STATE : 0;return status;}
    status=ce_owned_scene_begin_r4(&input->raw,&base,report);
    if (status!=R1_BUILDING) {report->abi=R5_ABI;return status;}
    r1_ledger* ledger=base->scene->clock.ledger;
    job=r1_allocate(ledger,sizeof(*job));if (!job) {ce_owned_scene_destroy_r4(base);report->status=R1_BUDGET;return R1_BUDGET;}
    job->base=base;job->borrowed=input;job->clip_bytes=base->scene->clips_n*sizeof(r5_clip);
    job->clips=r1_allocate(ledger,job->clip_bytes);
    status=job->clips ? r1_atom_number(input->raw.raw.timer_ms,23.,&job->timer_ms) : R1_BUDGET;
    if (status) {r5_report_current(job,report,status);ce_owned_scene_destroy_r5(job);return status;}
    *output=job;r5_report_current(job,report,R1_BUILDING);return R1_BUILDING;
}

static int r5_after_base(r5_job* job)
{
    const r5_input* input=job->borrowed;uint64_t bytes=job->base->external_min;
    if (!r3_add_bytes(&bytes,job->base->scene->clips_n,sizeof(r5_clip_raw))
            || !r3_add_bytes(&bytes,input->geometry_n,sizeof(r5_fixture_geometry_raw))
            || !r3_add_bytes(&bytes,input->hold_n,sizeof(r5_hold_state))
            || !r3_add_bytes(&bytes,input->latch_n,sizeof(r5_latch_state))) return R1_BUDGET;
    if (bytes>job->base->scene->external_bytes) return R1_BUDGET;
    job->external_min=bytes;job->phase=1;return R1_OK;
}

static int r5_compile_one(r5_job* job)
{
    switch(job->phase) {
    case 0:{
        int status=r4_compile_one(job->base);if (status) return status;
        return job->base->phase==9 ? r5_after_base(job) : R1_OK;
    }
    case 1:return r5_allocate_work(job);
    case 2:return r5_phase_one(job);
    case 3:return r5_sort_one(job);
    case 4:return r5_hold_seed_one(job);
    case 5:return r5_latch_seed_one(job);
    default:return R1_INVALID;
    }
}

CE_API int ce_owned_scene_compile_r5(r5_job* job,uint32_t units,r3_report* report)
{
    int status=R1_OK;
    if (!job || !report || !units || units>65536) return R1_INVALID;
    if (job->cancelled) status=R1_CANCELLED;
    else if (job->status) status=job->status;
    else while (units-- && job->phase!=6 && !status) status=r5_compile_one(job);
    if (status<0) {
        job->status=job->base->scene->clock.terminal_error=status;
        if (status==R1_PENDING && !job->base->scene->pending_features) job->base->scene->pending_features=R5_PENDING_STATE;
    }
    if (!status && job->phase!=6) status=R1_BUILDING;
    r5_report_current(job,report,status);return status;
}
