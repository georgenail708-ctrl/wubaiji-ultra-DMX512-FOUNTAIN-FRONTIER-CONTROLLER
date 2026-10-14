/* Actual bounded R6 producer compile entry. No numerical host work, separate
 * G0 DLL or whole-row call is hidden inside a nominal compile unit. */
static int r6_compile_one(r6_job* job)
{
    int status;
    switch(job->phase) {
    case R6_COMPILE_GRID:
        status=r6_main_grid_compile_one(job);
        if (!status && job->grid_ready) job->phase=R6_COMPILE_TIMELINE;
        return status;
    case R6_COMPILE_TIMELINE:
        status=r6_timeline_compile_one(job);
        if (!status && job->timeline->phase==6) {job->phase=R6_COMPILE_GEOMETRY;job->row=0;}
        return status;
    case R6_COMPILE_GEOMETRY:
        if (job->row==job->geometry.clips_n) {job->phase=R6_COMPILE_CONTEXT;return R1_OK;}
        status=r6_scene_plan_one(job,job->row);
        if (!status && job->geometry.clips[job->row].phase==11) job->row++;
        return status;
    case R6_COMPILE_CONTEXT:
        /* The raw header is not the owning context compiler/source permit or
         * genuine FI getter/block export. No input flag closes these domains. */
        job->timeline->base->scene->pending_features|=R6_PENDING_CONTEXT|R6_PENDING_TRANSITION|R3_PENDING_FADEIN;
        return R1_PENDING;
    default:return R1_INVALID;
    }
}

CE_API int ce_owned_scene_abi_r6(void) {return R6_ABI;}

CE_API int ce_owned_scene_size_r6(int selector)
{
    switch(selector) {
    case 0:return sizeof(r6_input);case 1:return sizeof(r6_report);
    case 2:return sizeof(r6_clip_geometry_raw);case 3:return sizeof(r6_context_raw);
    case 4:return sizeof(G0CompileState);default:return -1;
    }
}

CE_API int ce_owned_scene_cancel_r6(r6_job* job,r6_report* report)
{
    if (!job || !report) return R1_INVALID;
    job->cancelled=job->timeline->cancelled=job->timeline->base->cancelled=1;
    job->timeline->base->scene->clock.cancelled=1;
    r6_report_current(job,report,R1_CANCELLED);return R1_CANCELLED;
}

CE_API int ce_owned_scene_compile_r6(r6_job* job,uint32_t units,r6_report* report)
{
    int status=R1_OK;
    if (!job || !report || units>G0_UNIT_CAP) return R1_INVALID;
    if (job->cancelled || job->timeline->base->scene->clock.cancelled) status=R1_CANCELLED;
    else if (job->status) status=job->status;
    else if (units) {
        status=r6_source_current(job);
        while (units-- && job->phase!=R6_READY && !status) {
            if (job->cancelled || job->timeline->base->scene->clock.cancelled) status=R1_CANCELLED;
            else status=r6_compile_one(job);
        }
    }
    if (status<0) {
        job->status=job->timeline->base->scene->clock.terminal_error=status;
        if (status==R1_PENDING && !job->timeline->base->scene->pending_features)
            job->timeline->base->scene->pending_features|=R6_PENDING_CONTEXT;
    }
    if (!status && job->phase!=R6_READY) status=R1_BUILDING;
    r6_report_current(job,report,status);return status;
}
