static int r3_compile_finalize(r3_job* job)
{
    int status=r3_model_universes(job);
    if (!status) status=r3_idle_model(job);
    if (!status) status=r3_lt_arrays(job);
    if (!status) status=r3_work_allocate(job);
    return status;
}

CE_API int ce_owned_scene_compile_r3(r3_job* job,uint32_t units,r3_report* report)
{
    int status=R1_BUILDING;
    if (!job || !report || !units || units>65536) return R1_INVALID;
    if (job->clock.terminal_error) status=job->clock.terminal_error;
    else if (job->clock.cancelled) status=R1_CANCELLED;
    else if (job->compile_phase==3) status=R1_OK;
    else if (job->compile_phase==0) {
        r1_job* source=job->clips[job->compile_clip].source;
        status=r1_compile_advance(source,units);job->pending_features|=source->pending_features;
        if (!status) {job->compile_clip++;status=R1_BUILDING;}
        if (job->compile_clip==job->clips_n) {job->compile_phase=1;job->compile_clip=0;}
    } else if (job->compile_phase==1) {
        status=r3_compile_finalize(job);
        if (!status) {job->compile_phase=2;status=R1_BUILDING;}
    } else if (job->compile_phase==2) {
        status=r3_clip_masks(job,job->clips+job->compile_clip);
        if (!status) {
            job->compile_clip++;
            if (job->compile_clip==job->clips_n) {job->compile_phase=3;status=R1_OK;}
            else status=R1_BUILDING;
        }
    }
    if (status<0) job->clock.terminal_error=status;
    r3_report_current(job,report,status);return status;
}
