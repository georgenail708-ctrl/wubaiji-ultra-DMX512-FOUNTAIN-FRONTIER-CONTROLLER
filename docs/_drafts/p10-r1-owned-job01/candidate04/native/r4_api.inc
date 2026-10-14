CE_API int ce_owned_scene_abi_r4(void) {return R4_ABI;}
CE_API int ce_owned_scene_size_r4(int selector)
{
    switch(selector) {
    case 0:return sizeof(r4_channel_raw);case 1:return sizeof(r4_domain_raw);case 2:return sizeof(r4_event_raw);
    case 3:return sizeof(r4_channels_raw);case 4:return sizeof(r4_filter_raw);case 5:return sizeof(r4_clip_raw);
    case 6:return sizeof(r4_patch_fixture);case 7:return sizeof(r4_patch_definition);case 8:return sizeof(r4_channel_name);
    case 9:return sizeof(r4_render_request);case 10:return sizeof(r4_input);default:return -1;
    }
}

static void r4_report_current(r4_job* job,r3_report* report,int status)
{
    r3_report_current(job->scene,report,status);report->abi=R4_ABI;report->compile_phase=job->phase;
    report->compile_clip=job->row;report->publishable=0;
}

CE_API void ce_owned_scene_destroy_r4(r4_job* job)
{
    if (!job) return;r3_job* scene=job->scene;r1_ledger* ledger=scene->clock.ledger;
    if (job->clips) for (uint64_t i=0;i<scene->clips_n;i++) {
        r4_clip* extra=job->clips+i;r3_clip* clip=scene->clips+i;
        if (clip->source && clip->lt_slots && extra->slots) for (uint64_t s=0;s<clip->source->streams_n;s++) {
            r1_free(ledger,clip->lt_slots[s].fx_rows,extra->slots[s].lt_fx_bytes);clip->lt_slots[s].fx_rows=NULL;
        }
        r4_recorded_destroy(&extra->events);r1_free(ledger,extra->slots,extra->slots_bytes);
    }
    r4_tracks_destroy(&job->tracks);r1_free(ledger,job->defaults_seen,job->defaults_seen_bytes);
    r1_free(ledger,job->clips,job->clip_bytes);r1_free(ledger,job,sizeof(*job));ce_owned_scene_destroy_r3(scene);
}

CE_API int ce_owned_scene_release_r4(r3_block* block) {return ce_owned_scene_release_r3(block);}

CE_API int ce_owned_scene_cancel_r4(r4_job* job,r3_report* report)
{
    if (!job || !report) return R1_INVALID;
    job->cancelled=1;job->scene->clock.cancelled=1;r4_report_current(job,report,R1_CANCELLED);return R1_CANCELLED;
}

CE_API int ce_owned_scene_step_r4(r4_job* job,const r1_step* request,r3_block** output,r3_report* report)
{
    int status=R1_OK;r3_block* block;r3_job* scene;
    if (!job || !request || !output || !report) return R1_INVALID;*output=NULL;scene=job->scene;
    if (request->struct_size!=sizeof(*request) || !request->max_frames || request->max_frames>256) return R1_INVALID;
    if (job->status) status=job->status;
    else if (job->cancelled) status=R1_CANCELLED;
    else if (job->phase!=9) status=R1_BUILDING;
    else if (scene->clock.cursor==scene->clock.total_frames) status=R1_DONE;
    if (status) {r4_report_current(job,report,status);return status;}
    block=r3_new_block(scene,request);if (!block) {r4_report_current(job,report,R1_BUDGET);return R1_BUDGET;}
    r1_empty_scores(block->frames);
    for (uint64_t f=0;f<block->frames->frame_count;f++) {status=r4_render_frame(job,block->frames,f);if (status) break;}
    if (status) {
        if (scene->clock.cursor!=block->frames->first_index) job->status=scene->clock.terminal_error=status;
        ce_owned_scene_release_r3(block);
    } else {
        block->frames->next_index=scene->clock.cursor;block->frames->last_time=scene->clock.last_time;
        r3_capture_state(scene,block->frames);r3_history_refresh(scene,block->history);*output=block;
    }
    r4_report_current(job,report,status);return status;
}
