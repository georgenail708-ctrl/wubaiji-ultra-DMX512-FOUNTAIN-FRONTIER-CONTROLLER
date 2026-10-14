/* Select from the cached domain: no scan of raw maps on every frame. */
static void r4_cached_select(const r4_recorded* rows,const r4_domain* domain,
                              double source,r4_mask_selection* out)
{
    memset(out,0,sizeof(*out));out->event=-1;if (!domain) return;
    uint64_t left=0,right=domain->count;double needle=_ce_rt_nonnegative(source)+1e-9;
    while (left<right) {
        uint64_t middle=left+(right-left)/2;
        if (needle<rows->events[domain->begin+middle].timestamp) right=middle;else left=middle+1;
    }
    if (!left) {out->kind=R4_MASK_BEFORE_FIRST;return;}
    out->kind=R4_MASK_SELECTED;out->event=(int64_t)(left-1);out->selected=rows->events+domain->begin+left-1;
}

static int r4_event_domain_one(r4_job* job)
{
    r4_clip* extra=job->clips+job->row;r4_slot* slot=extra->slots+job->slot;
    uint32_t u=job->scene->clips[job->row].source->streams[job->slot].universe;
    if (job->scan<extra->events.domains_n) {
        const r4_domain* domain=extra->events.domains+job->scan++;
        if (domain->universe==u) {if (domain->key_kind==0) slot->integer_events=domain;else slot->text_events=domain;}
        return R1_OK;
    }
    slot->event_domain=slot->integer_events && slot->integer_events->count ? slot->integer_events
        : slot->text_events && slot->text_events->count ? slot->text_events : NULL;
    const r4_domain* owner_events=slot->integer_events ? slot->integer_events : slot->text_events;
    if (job->selected_owner || (owner_events && owner_events->count)) memset(slot->owned,0,512);
    job->channel=0;job->subphase=job->selected_owner ? 10 : 11;slot->owned_explicit=job->selected_owner!=NULL;return R1_OK;
}

static void r4_owned_finish(r4_job* job)
{
    job->slot++;job->scan=0;job->selected_active=job->selected_owner=NULL;
    job->selected_filter=NULL;job->subphase=1;
}

static int r4_owner_explicit_one(r4_job* job)
{
    r4_slot* slot=job->clips[job->row].slots+job->slot;
    if (job->channel==job->selected_owner->count) {r4_owned_finish(job);return R1_OK;}
    const r4_channel_raw* raw=job->selected_owner->channels+job->channel++;int channel,equal,status;
    if (raw->kind>1 || raw->reserved) return R1_PENDING;
    status=raw->kind==1 ? r4_text_channel(raw,&channel) : r4_numeric_channel(raw,&channel,&equal);
    if (status<0) return status;
    if (status==1) slot->owned[channel]=1;
    else if (status==0) {
        double number;
        if (raw->kind==1 || raw->number.kind==R1_NONE || r1_atom_number(raw->number,0.,&number)) return R1_PENDING;
    }
    return R1_OK;
}

static int r4_owner_events_one(r4_job* job)
{
    r4_clip* extra=job->clips+job->row;r4_slot* slot=extra->slots+job->slot;
    const r4_domain* domain=slot->integer_events ? slot->integer_events : slot->text_events;
    if (!domain || !domain->count) {
        r4_owned_finish(job);return R1_OK;
    }
    if (job->channel==domain->count) {r4_owned_finish(job);return R1_OK;}
    double stamp=extra->events.events[domain->begin+job->channel++].timestamp;r4_mask_selection selected;
    r4_cached_select(&extra->events,slot->event_domain,stamp,&selected);
    if (selected.kind==R4_MASK_SELECTED) for (int c=0;c<512;c++) slot->owned[c]|=(selected.selected->bits[c>>3]>>(7-(c&7)))&1;
    return R1_OK;
}

static int r4_owned_mask_one(r4_job* job)
{
    if (job->subphase==9) return r4_event_domain_one(job);
    if (job->subphase==10) return r4_owner_explicit_one(job);
    return r4_owner_events_one(job);
}
