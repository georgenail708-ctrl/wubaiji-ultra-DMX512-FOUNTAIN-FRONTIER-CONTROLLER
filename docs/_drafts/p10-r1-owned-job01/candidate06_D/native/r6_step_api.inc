/* Detached output/precharge and chronology use the actual shared R5/R3/R1
 * ledger. Context/FI compilation must really reach R6_READY before any block;
 * current missing context gate prevents incomplete producer publication. */
CE_API int ce_owned_scene_step_r6(r6_job* job,const r1_step* request,r5_block** output,r6_report* report)
{
    int status=R1_OK;r5_block* block;r3_job* scene;
    if (!job || !request || !output || !report) return R1_INVALID;
    *output=NULL;scene=job->timeline->base->scene;
    if (request->struct_size!=sizeof(*request) || !request->max_frames || request->max_frames>256) return R1_INVALID;
    if (job->status) status=job->status;
    else if (job->cancelled || scene->clock.cancelled) status=R1_CANCELLED;
    else if (job->phase!=R6_READY) status=R1_BUILDING;
    else if (scene->clock.cursor==scene->clock.total_frames) status=R1_DONE;
    else status=r6_source_current(job);
    if (status) {r6_report_current(job,report,status);return status;}
    block=r5_block_create(job->timeline,request);
    if (!block) {r6_report_current(job,report,R1_BUDGET);return R1_BUDGET;}
    r1_empty_scores(block->frames->frames);
    for (uint64_t f=0;f<block->frames->frames->frame_count;f++) {
        status=r6_render_frame(job,block->frames->frames,f);if (status) break;
    }
    if (status) {
        /* Source/gain/LT/hold/consumption may have changed before cursor0.
         * Every failed block seals the owning job and cannot resume/drop it. */
        job->status=scene->clock.terminal_error=status;ce_owned_scene_release_r6(block);
    } else {
        r1_block* frames=block->frames->frames;
        frames->next_index=scene->clock.cursor;frames->last_time=scene->clock.last_time;
        r3_capture_state(scene,frames);r3_history_refresh(scene,block->frames->history);
        r5_history_refresh(job->timeline,block->history);
        if (frames->state) {frames->state->domains[0].kind=7;frames->state->domains[1].kind=7;}
        *output=block;
    }
    r6_report_current(job,report,status);return status;
}
