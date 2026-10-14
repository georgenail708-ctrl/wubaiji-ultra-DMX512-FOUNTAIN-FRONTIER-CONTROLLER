static int r4_masks_create_clip(r4_job* job)
{
    r3_clip* clip=job->scene->clips+job->row;r4_clip* extra=job->clips+job->row;
    const r4_clip_raw* raw=job->borrowed->clip_masks+job->row;
    uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,clip->source->streams_n,sizeof(r4_slot))) return R1_BUDGET;
    if ((raw->active_domains_n && !raw->active_domains) || (raw->base_ids_n && !raw->base_ids)
            || (raw->filter_domains_n && !raw->filter_domains)) return R1_INVALID;
    if (!r3_add_bytes(&job->external_min,raw->active_domains_n,sizeof(r4_channels_raw))
            || !r3_add_bytes(&job->external_min,raw->filter_domains_n,sizeof(r4_filter_raw))
            || !r3_add_bytes(&job->external_min,raw->mask_domains_n,sizeof(r4_domain_raw))
            || !r3_add_bytes(&job->external_min,raw->mask_events_n,sizeof(r4_event_raw))
            || !r3_add_bytes(&job->external_min,raw->base_ids_n,sizeof(uint64_t))) return R1_BUDGET;
    if (job->external_min>job->scene->external_bytes) return R1_BUDGET;
    extra->slots_bytes=bytes;extra->slots=r1_allocate(job->scene->clock.ledger,bytes);
    if (!extra->slots) return R1_BUDGET;
    int status=r4_recorded_create(&extra->events,job->scene->clock.ledger,raw->mask_domains,raw->mask_domains_n,raw->mask_events_n);
    return status==R1_BUILDING ? R1_OK : status;
}

static int r4_mask_domain_one(r4_job* job)
{
    const r4_clip_raw* raw=job->borrowed->clip_masks+job->row;
    uint32_t u=job->scene->clips[job->row].source->streams[job->slot].universe;
    if (job->scan<raw->active_domains_n) {
        const r4_channels_raw* domain=raw->active_domains+job->scan++;
        if (domain->key_kind>1 || (domain->count && !domain->channels)) return R1_PENDING;
        uint64_t raw_bytes=0;if (!r3_add_bytes(&raw_bytes,domain->count,sizeof(r4_channel_raw))
                || raw_bytes>job->scene->external_bytes) return R1_BUDGET;
        if (domain->universe==u && (!job->selected_owner || domain->key_kind==0)) job->selected_owner=domain;
        if (domain->universe==u && domain->count && (!job->selected_active || domain->key_kind==0)) job->selected_active=domain;
        return R1_OK;
    }
    if (job->selected_active) {
        int status=r4_rule_create(&job->active,job->selected_active->channels,job->selected_active->count,1);
        if (status<0) return status;
    } else memset(&job->active,0,sizeof(job->active));
    memset(&job->base,0,sizeof(job->base));job->base.fixtures_n=job->borrowed->patch_fixtures_n;
    job->base.definitions_n=job->borrowed->patch_definitions_n;job->base.base_n=raw->base_ids_n;job->base.universe=u;
    int status=r4_base_create(&job->base,job->borrowed->patch_fixtures,job->borrowed->patch_definitions,raw->base_ids);
    job->scan=0;job->subphase=2;return status;
}

static int r4_explicit_active_one(r4_job* job)
{
    r4_rule* active=&job->active;
    if (!active->borrowed || active->cursor==active->count) {job->subphase=3;return R1_OK;}
    const r4_channel_raw* raw=active->borrowed+active->cursor++;int channel,equal,status;
    if (raw->kind>1 || raw->reserved) return R1_PENDING;
    status=raw->kind==1 ? r4_text_channel(raw,&channel) : r4_numeric_channel(raw,&channel,&equal);
    if (status==R1_PENDING || status==R1_INVALID) return status;
    if (status==1) active->mask[channel]=1;
    else if (status==0) {
        /* Original explicit-active loop catches one conversion error around
         * the whole list; valid out-of-range values merely continue. */
        double number;
        int invalid=raw->kind==1 || raw->number.kind==R1_NONE || r1_atom_number(raw->number,0.,&number);
        if (invalid) {
            /* The helper itself preserves partial fallback, but the actual
             * compiled-universe cache key int(vv) raises before this helper.
             * Do not publish a successful whole-job result for that domain. */
            job->scene->pending_features|=R4_PENDING_CACHE_SIGNATURE;return R1_PENDING;
        }
    }
    return R1_OK;
}

static int r4_mask_finish_base(r4_job* job)
{
    r1_stream* stream=job->scene->clips[job->row].source->streams+job->slot;
    r4_slot* slot=job->clips[job->row].slots+job->slot;int explicit_any=0,any=0;
    memcpy(slot->owned,stream->base,512); /* authored nonzero/change union, before fallback/intersection */
    for (int c=0;c<512;c++) explicit_any|=job->active.mask[c];
    if (explicit_any && !slot->active_error) memcpy(stream->base,job->active.mask,512);
    else {
        for (int c=0;c<512;c++) {
            stream->base[c]|=job->active.mask[c];
            if (job->base.base_present) stream->base[c]&=job->base.base[c];
            any|=stream->base[c];
        }
        if (!any) {if (job->base.base_present) memcpy(stream->base,job->base.base,512);else memset(stream->base,1,512);}
    }
    slot->base_present=job->base.base_present;memcpy(slot->base_order,job->base.base,512);
    slot->explicit_present=explicit_any && !slot->active_error;
    job->subphase=4;job->scan=0;return R1_OK;
}
