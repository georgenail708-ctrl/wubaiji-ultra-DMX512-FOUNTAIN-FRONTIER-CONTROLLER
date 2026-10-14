static void r5_report_current(r5_job* job,r3_report* report,int status)
{
    r3_report_current(job->base->scene,report,status);report->abi=R5_ABI;report->compile_phase=job->phase;
    report->compile_clip=job->row;report->publishable=0;
}

CE_API int ce_owned_scene_abi_r5(void) {return R5_ABI;}
CE_API int ce_owned_scene_size_r5(int selector)
{
    switch(selector) {
    case 0:return sizeof(r5_text);case 1:return sizeof(r5_clip_raw);case 2:return sizeof(r5_fixture_geometry_raw);
    case 3:return sizeof(r5_input);case 4:return sizeof(r5_hold_state);case 5:return sizeof(r5_latch_state);
    case 6:return sizeof(r5_history);case 7:return sizeof(r5_block);default:return -1;
    }
}

CE_API void ce_owned_scene_destroy_r5(r5_job* job)
{
    if (!job) return;r1_ledger* ledger=job->base->scene->clock.ledger;
    if (job->clips) for (uint64_t i=0;i<job->base->scene->clips_n;i++) {
        r5_clip* clip=job->clips+i;r1_free(ledger,clip->pauses,clip->pause_bytes);
        r1_free(ledger,clip->hold_mask,clip->bytes);r1_free(ledger,clip->sampled,clip->sampled_bytes);
    }
    r1_free(ledger,job->covered,job->coverage_bytes);r1_free(ledger,job->active_order,job->order_bytes);
    r1_free(ledger,job->hold_order,job->order_bytes);r1_free(ledger,job->body_order,job->order_bytes);
    r1_free(ledger,job->clips,job->clip_bytes);
    r4_job* base=job->base;r1_free(ledger,job,sizeof(*job));ce_owned_scene_destroy_r4(base);
}

CE_API int ce_owned_scene_release_r5(r5_block* block)
{
    if (!block || block->magic!=R1_MAGIC) return R1_INVALID;r1_ledger* ledger=block->ledger;block->magic=0;
    if (block->history) r1_free(ledger,block->history,block->history->bytes);
    if (block->frames) ce_owned_scene_release_r3(block->frames);
    r1_free(ledger,block,block->bytes);r1_ledger_release(ledger);return R1_OK;
}

CE_API int ce_owned_scene_cancel_r5(r5_job* job,r3_report* report)
{
    if (!job || !report) return R1_INVALID;job->cancelled=job->base->cancelled=1;
    job->base->scene->clock.cancelled=1;r5_report_current(job,report,R1_CANCELLED);return R1_CANCELLED;
}

CE_API int ce_owned_scene_step_r5(r5_job* job,const r1_step* request,r5_block** output,r3_report* report)
{
    int status=R1_OK;r5_block* block;r3_job* scene;
    if (!job || !request || !output || !report) return R1_INVALID;*output=NULL;scene=job->base->scene;
    if (request->struct_size!=sizeof(*request) || !request->max_frames || request->max_frames>256) return R1_INVALID;
    if (job->status) status=job->status;
    else if (job->cancelled) status=R1_CANCELLED;
    else if (job->phase!=6) status=R1_BUILDING;
    else if (scene->clock.cursor==scene->clock.total_frames) status=R1_DONE;
    if (status) {r5_report_current(job,report,status);return status;}
    block=r5_block_create(job,request);if (!block) {r5_report_current(job,report,R1_BUDGET);return R1_BUDGET;}
    r1_empty_scores(block->frames->frames);
    for (uint64_t f=0;f<block->frames->frames->frame_count;f++) {status=r5_render_frame(job,block->frames->frames,f);if (status) break;}
    if (status) {
        /* A frame may have already mutated LT/latches/consumption. Even failure
         * before first cursor advance seals the job: no resume can drop work. */
        job->status=scene->clock.terminal_error=status;ce_owned_scene_release_r5(block);
    } else {
        r1_block* frames=block->frames->frames;frames->next_index=scene->clock.cursor;frames->last_time=scene->clock.last_time;
        r3_capture_state(scene,frames);r3_history_refresh(scene,block->frames->history);r5_history_refresh(job,block->history);
        if (frames->state) {frames->state->domains[0].kind=7;frames->state->domains[1].kind=7;}
        *output=block;
    }
    r5_report_current(job,report,status);return status;
}
