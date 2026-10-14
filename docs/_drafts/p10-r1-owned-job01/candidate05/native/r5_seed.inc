/* Typed seed is original-container enumeration, never a Python mask reduction.
 * One row/clip/stream comparison per compile unit; raw owning arrays stay pinned. */
static int r5_hold_seed_one(r5_job* job)
{
    const r5_input* raw=job->borrowed;r3_job* scene=job->base->scene;
    if (job->seed_cursor==raw->hold_n) {job->phase=5;job->seed_cursor=job->seed_clip=job->seed_slot=0;return R1_OK;}
    const r5_hold_state* row=raw->hold+job->seed_cursor;
    if (row->mask_present>1 || row->outer_present>1 || row->reserved || (row->mask_present && !row->outer_present)) return R1_PENDING;
    if (!job->subphase) {
        if (job->scan<job->seed_cursor) {
            const r5_hold_state* previous=raw->hold+job->scan++;
            if (previous->source_token==row->source_token && (previous->universe==row->universe
                    || previous->outer_present!=row->outer_present)) return R1_PENDING;
            return R1_OK;
        }
        job->subphase=1;job->scan=0;return R1_OK;
    }
    if (job->seed_clip==scene->clips_n) return R1_PENDING;
    r3_clip* source=scene->clips+job->seed_clip;r5_clip* clip=job->clips+job->seed_clip;
    if (source->source->source_token!=row->source_token) {job->seed_clip++;job->seed_slot=0;return R1_OK;}
    if (!row->universe) {
        if (row->mask_present || !row->outer_present) return R1_PENDING;
        clip->hold_outer_present=1;
    } else {
        if (job->seed_slot==source->source->streams_n) return R1_PENDING;
        if (source->source->streams[job->seed_slot].universe!=row->universe) {job->seed_slot++;return R1_OK;}
        clip->hold_outer_present=row->outer_present;clip->hold_seen[job->seed_slot]=row->mask_present;
        memcpy(clip->hold_mask+job->seed_slot*512,row->mask,512);
    }
    job->seed_cursor++;job->seed_clip=job->seed_slot=job->scan=job->subphase=0;return R1_OK;
}

static int r5_latch_seed_one(r5_job* job)
{
    const r5_input* raw=job->borrowed;r3_job* scene=job->base->scene;
    if (job->seed_cursor==raw->latch_n) {
        if (job->external_min>scene->external_bytes) return R1_BUDGET;
        job->borrowed=NULL;job->phase=6;return R1_OK;
    }
    const r5_latch_state* row=raw->latch+job->seed_cursor;
    if (row->present>1 || row->value>1 || job->seed_clip==scene->clips_n) return R1_PENDING;
    if (!job->subphase) {
        if (job->scan<job->seed_cursor) {
            const r5_latch_state* previous=raw->latch+job->scan++;
            if (previous->source_token==row->source_token && previous->track_order==row->track_order) return R1_PENDING;
            return R1_OK;
        }
        job->subphase=1;job->scan=0;return R1_OK;
    }
    r3_clip* source=scene->clips+job->seed_clip;r5_clip* clip=job->clips+job->seed_clip;
    if (source->source->source_token!=row->source_token || (double)row->track_order!=source->source->track_order) {
        job->seed_clip++;return R1_OK;
    }
    clip->hold_latch_present=row->present;clip->hold_latch=(uint8_t)row->value;
    job->seed_cursor++;job->seed_clip=job->scan=job->subphase=0;return R1_OK;
}
