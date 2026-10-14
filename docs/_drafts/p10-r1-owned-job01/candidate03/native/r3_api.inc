CE_API int ce_owned_scene_abi_r3(void) {return R3_ABI;}
CE_API int ce_owned_scene_size_r3(int selector)
{
    switch(selector) {
    case 0:return sizeof(r3_track_raw);case 1:return sizeof(r3_definition_raw);
    case 2:return sizeof(r3_fixture_raw);case 3:return sizeof(r3_default_raw);
    case 4:return sizeof(r3_clip_raw);case 5:return sizeof(r3_input);
    case 6:return sizeof(r3_report);case 7:return sizeof(r3_history);case 8:return sizeof(r3_block);
    case 9:return sizeof(r3_frame_raw);
    default:return -1;
    }
}

CE_API int ce_owned_scene_cancel_r3(r3_job* job,r3_report* report)
{
    if (!job || !report) return R1_INVALID;
    job->clock.cancelled=1;r3_report_current(job,report,R1_CANCELLED);return R1_CANCELLED;
}

static r3_block* r3_new_block(r3_job* job,const r1_step* request)
{
    r3_block* block=r1_allocate(job->clock.ledger,sizeof(r3_block));
    r1_step bounded=*request;uint64_t overhead=sizeof(r3_block)+r3_history_bytes(job);
    if (!block) return NULL;
    block->magic=R1_MAGIC;block->allocation_bytes=sizeof(*block);block->ledger=job->clock.ledger;block->ledger->refs++;
    if (bounded.max_block_bytes<=overhead) {ce_owned_scene_release_r3(block);return NULL;}
    bounded.max_block_bytes-=overhead;block->frames=r1_new_block(&job->clock,&bounded);
    /* Admit full detached history before advancing even a single frame. */
    if (block->frames) block->history=r3_history_copy(job);
    if (!block->frames || !block->history) {ce_owned_scene_release_r3(block);return NULL;}
    return block;
}

static int r3_block_render(r3_job* job,r3_block* block)
{
    int status=R1_OK;
    r1_empty_scores(block->frames);
    for (uint64_t frame=0;frame<block->frames->frame_count;frame++) {
        status=r3_render_frame(job,block->frames,frame);if (status) break;
    }
    if (status) {
        if (job->clock.cursor!=block->frames->first_index) job->clock.terminal_error=status;
        return status;
    }
    block->frames->next_index=job->clock.cursor;block->frames->last_time=job->clock.last_time;
    r3_capture_state(job,block->frames);
    /* Update in place: allocation has already been budget-admitted. */
    r3_history_refresh(job,block->history);return R1_OK;
}

CE_API int ce_owned_scene_step_r3(r3_job* job,const r1_step* request,r3_block** output,r3_report* report)
{
    r3_block* block;int status=R1_OK;
    if (!job || !request || !output || !report) return R1_INVALID;
    *output=NULL;
    if (request->struct_size!=sizeof(*request) || !request->max_frames || request->max_frames>256) return R1_INVALID;
    if (job->clock.terminal_error) status=job->clock.terminal_error;
    else if (job->clock.cancelled) status=R1_CANCELLED;
    else if (job->compile_phase!=3) status=R1_BUILDING;
    else if (job->clock.cursor==job->clock.total_frames) status=R1_DONE;
    if (status) {r3_report_current(job,report,status);return status;}
    block=r3_new_block(job,request);
    if (!block) {r3_report_current(job,report,R1_BUDGET);return R1_BUDGET;}
    status=r3_block_render(job,block);
    if (status) ce_owned_scene_release_r3(block);else *output=block;
    r3_report_current(job,report,status);return status;
}
