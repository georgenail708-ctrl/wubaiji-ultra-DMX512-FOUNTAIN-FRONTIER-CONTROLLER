static int r4_filter_domain_one(r4_job* job)
{
    const r4_clip_raw* raw=job->borrowed->clip_masks+job->row;
    uint32_t universe=job->scene->clips[job->row].source->streams[job->slot].universe;
    if (job->scan<raw->filter_domains_n) {
        const r4_filter_raw* filter=raw->filter_domains+job->scan++;
        if (filter->reserved || filter->key_kind>2 || (filter->count && !filter->channels)) return R1_PENDING;
        uint64_t raw_bytes=0;if (!r3_add_bytes(&raw_bytes,filter->count,sizeof(r4_channel_raw))
                || raw_bytes>job->scene->external_bytes) return R1_BUDGET;
        if (filter->present && (filter->key_kind==2 || (filter->key_kind==0 && filter->universe==universe))) {
            if (!job->selected_filter || filter->key_kind==0) job->selected_filter=filter;
        }
        return R1_OK;
    }
    r4_slot* slot=job->clips[job->row].slots+job->slot;
    int status=r4_rule_create(&slot->rule,job->selected_filter ? job->selected_filter->channels : NULL,
        job->selected_filter ? job->selected_filter->count : 0,job->selected_filter!=NULL);
    job->subphase=5;return status<0 ? status : R1_OK;
}

static int r4_masks_slot_one(r4_job* job)
{
    r4_clip* extra=job->clips+job->row;r4_slot* slot=extra->slots+job->slot;int status=R1_OK;
    switch(job->subphase) {
    case 1:return r4_mask_domain_one(job);
    case 2:return r4_explicit_active_one(job);
    case 3:
        status=r4_base_step(&job->base,1);if (status==R1_BUILDING) return R1_OK;
        return status ? status : r4_mask_finish_base(job);
    case 4:return r4_filter_domain_one(job);
    case 5:
        status=r4_rule_step(&slot->rule,1);if (status==R1_BUILDING) return R1_OK;
        if (status) return status;
        memset(&job->pairs,0,sizeof(job->pairs));job->pairs.fixtures=job->borrowed->patch_fixtures;
        job->pairs.fixtures_n=job->borrowed->patch_fixtures_n;job->pairs.definitions=job->borrowed->patch_definitions;
        job->pairs.definitions_n=job->borrowed->patch_definitions_n;
        job->pairs.universe=job->scene->clips[job->row].source->streams[job->slot].universe;
        memcpy(job->pairs.mask,slot->rule.mask,512);job->subphase=slot->rule.present ? 6 : 7;return R1_OK;
    case 6:
        status=r4_pair_step(&job->pairs,1);if (status==R1_BUILDING) return R1_OK;
        if (status) return status;memcpy(slot->rule.mask,job->pairs.mask,512);job->subphase=7;return R1_OK;
    case 7:
        job->scan=0;job->subphase=9;
        return R1_OK;
    case 9:case 10:case 11:return r4_owned_mask_one(job);
    default:return R1_INVALID;
    }
}

static int r4_masks_one(r4_job* job)
{
    r4_clip* extra=job->clips+job->row;r1_job* source=job->scene->clips[job->row].source;int status;
    if (job->subphase==0) {
        status=r4_masks_create_clip(job);if (status) return status;job->subphase=8;return R1_OK;
    }
    if (job->subphase==8) {
        status=r4_recorded_step(&extra->events,1);if (status==R1_BUILDING) return R1_OK;
        if (status) return status;job->subphase=1;job->slot=0;return R1_OK;
    }
    if (job->slot<source->streams_n) return r4_masks_slot_one(job);
    job->row++;job->slot=job->subphase=0;
    if (job->row==job->scene->clips_n) {job->row=0;job->phase=5;}
    return R1_OK;
}
