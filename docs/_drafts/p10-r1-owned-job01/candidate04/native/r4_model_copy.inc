static int r4_model_one(r4_job* job)
{
    const r3_input* raw=&job->borrowed->raw;r3_job* scene=job->scene;
    if (job->subphase==0) {
        if (job->row<scene->tracks_n) {uint64_t index=job->row++;scene->tracks[index]=raw->tracks[index];}
        else {job->row=0;job->subphase=1;}
    } else if (job->subphase==1) {
        if (job->row<scene->fixtures_n) {uint64_t index=job->row++;scene->fixtures[index]=raw->fixtures[index];}
        else {job->row=0;job->subphase=2;}
    } else if (job->subphase==2) {
        if (job->row<scene->defaults_n) {uint64_t index=job->row++;scene->defaults[index]=raw->defaults[index];}
        else {job->row=0;job->subphase=3;}
    } else if (job->subphase==3) {
        if (job->row==scene->definitions_n) {job->row=0;job->subphase=0;job->phase=1;return R1_OK;}
        r3_definition_raw* def=scene->definitions+job->row;const r3_definition_raw* original=raw->definitions+job->row;
        *def=*original;def->channel_kinds=NULL;
        if (def->reserved || def->channel_count>512 || (def->channel_count && !original->channel_kinds)) return R1_PENDING;
        if (def->channel_count) {
            def->channel_kinds=r1_allocate(scene->clock.ledger,def->channel_count*sizeof(uint32_t));
            if (!def->channel_kinds) return R1_BUDGET;
        }
        job->channel=0;job->subphase=4;
    } else {
        r3_definition_raw* def=scene->definitions+job->row;
        if (job->channel<def->channel_count) {
            ((uint32_t*)def->channel_kinds)[job->channel]=raw->definitions[job->row].channel_kinds[job->channel];job->channel++;
        } else {job->row++;job->subphase=3;}
    }
    return R1_OK;
}

static int r4_clip_create_one(r4_job* job)
{
    r3_job* scene=job->scene;const r3_input* raw=&job->borrowed->raw;
    const r3_clip_raw* source=raw->clips+job->row;
    if (job->scan<scene->tracks_n) {
        if (scene->tracks[job->scan].token==source->track_token) {
            if (job->selected!=UINT64_MAX) {scene->pending_features|=R4_PENDING_ALIAS;return R1_PENDING;}
            job->selected=job->scan;
        }
        job->scan++;return R1_OK;
    }
    if (job->selected==UINT64_MAX || source->recorded.channel_count) return R1_PENDING;
    if (!r3_add_bytes(&job->external_min,source->recorded.frame_count,512+sizeof(r3_frame_raw))) return R1_BUDGET;
    if (job->external_min>raw->external_bytes) return R1_BUDGET;
    scene->compile_clip=job->row;
    int status=r4_clip_create_bound(scene,source,&raw->timer_ms,job->selected);if (status) return status;
    r3_clip* clip=scene->clips+job->row;
    clip->allowed=job->tracks.allowed[job->selected];clip->chain_token=scene->tracks[job->selected].parent;
    clip->source->track_order=(double)job->tracks.rank[job->selected];
    job->row++;job->scan=0;job->selected=UINT64_MAX;
    if (job->row==scene->clips_n) {job->row=0;job->phase=3;}
    return R1_OK;
}

/* Source sorting is split before sparse compilation, so C raw masks can
 * establish explicit-zero ownership before first-change timestamps exist. */
static int r4_source_sort_one(r4_job* job)
{
    r1_job* source=job->scene->clips[job->row].source;
    if (source->compile_phase==3 && r1_compile_finished(source)) {
        job->row++;if (job->row==job->scene->clips_n) {job->row=0;job->phase=4;}
        return R1_OK;
    }
    if (r1_compile_finished(source)) return r1_compile_transition(source);
    return r1_compile_work(source);
}
