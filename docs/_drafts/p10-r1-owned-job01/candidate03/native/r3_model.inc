static int64_t r3_track_index(const r3_job* job,uint64_t token)
{
    for (uint64_t i=0;i<job->tracks_n;i++) if (job->tracks[i].token==token) return (int64_t)i;
    return -1;
}

static const r3_definition_raw* r3_definition(const r3_job* job,uint64_t token)
{
    /* Original dict keeps last duplicate definition id. */
    const r3_definition_raw* result=NULL;
    for (uint64_t i=0;i<job->definitions_n;i++) if (job->definitions[i].token==token) result=job->definitions+i;
    return result;
}

static int r3_track_allowed(const r3_job* job,uint64_t index,int any_solo)
{
    int solo=0;
    for (uint64_t step=0;step<=job->tracks_n;step++) {
        const r3_track_raw* track=job->tracks+index;
        if (track->muted) return 0;
        solo|=track->solo!=0;
        if (!track->parent) return !any_solo || solo;
        int64_t parent=r3_track_index(job,track->parent);
        if (parent<0) return !any_solo || solo;
        index=(uint64_t)parent;
    }
    return 0; /* corrupt parent cycle does not enter normal scope */
}

static int r3_flat_tracks(r3_job* job)
{
    int any_solo=0;
    for (uint64_t i=0;i<job->tracks_n;i++) {
        if (!job->tracks[i].token || job->tracks[i].reserved || job->tracks[i].parent || job->tracks[i].folder) return R1_PENDING;
        if (!job->tracks[i].muted && job->tracks[i].solo) any_solo=1;
    }
    for (uint64_t i=0;i<job->clips_n;i++) {
        r3_clip* clip=job->clips+i;
        if (clip->track_index>=job->tracks_n) return R1_INVALID;
        clip->allowed=r3_track_allowed(job,clip->track_index,any_solo);
        clip->chain_token=0;clip->source->track_order=(double)clip->track_index;
    }
    return R1_OK;
}

static int r3_copy_model(r3_job* job,const r3_input* input)
{
    job->tracks_bytes=job->tracks_n*sizeof(r3_track_raw);job->fixtures_bytes=job->fixtures_n*sizeof(r3_fixture_raw);
    job->definitions_bytes=job->definitions_n*sizeof(r3_definition_raw);job->defaults_bytes=job->defaults_n*sizeof(r3_default_raw);
    job->tracks=r1_allocate(job->clock.ledger,job->tracks_bytes);
    if (job->fixtures_n) job->fixtures=r1_allocate(job->clock.ledger,job->fixtures_bytes);
    if (job->definitions_n) job->definitions=r1_allocate(job->clock.ledger,job->definitions_bytes);
    if (job->defaults_n) job->defaults=r1_allocate(job->clock.ledger,job->defaults_bytes);
    if (!job->tracks || (job->fixtures_n && !job->fixtures) || (job->definitions_n && !job->definitions)
            || (job->defaults_n && !job->defaults)) return R1_BUDGET;
    memcpy(job->tracks,input->tracks,(size_t)job->tracks_bytes);
    if (job->fixtures_n) memcpy(job->fixtures,input->fixtures,(size_t)job->fixtures_bytes);
    if (job->definitions_n) memcpy(job->definitions,input->definitions,(size_t)job->definitions_bytes);
    if (job->defaults_n) memcpy(job->defaults,input->defaults,(size_t)job->defaults_bytes);
    for (uint64_t i=0;i<job->definitions_n;i++) job->definitions[i].channel_kinds=NULL;
    for (uint64_t i=0;i<job->definitions_n;i++) {
        r3_definition_raw* def=job->definitions+i;
        uint64_t bytes=def->channel_count*sizeof(uint32_t);const uint32_t* raw=input->definitions[i].channel_kinds;
        if (def->reserved || def->channel_count>512 || (bytes && !raw)) return R1_PENDING;
        if (bytes) {
            uint32_t* owned=r1_allocate(job->clock.ledger,bytes);
            if (!owned) return R1_BUDGET;
            memcpy(owned,raw,(size_t)bytes);def->channel_kinds=owned;
        }
    }
    return R1_OK;
}

static int r3_model_universes(r3_job* job)
{
    uint64_t streams=0;
    for (uint64_t i=0;i<job->clips_n;i++) {
        r1_job* source=job->clips[i].source;
        job->slots_n+=source->streams_n;
        for (uint64_t s=0;s<source->streams_n;s++) {
            uint32_t u=source->streams[s].universe;
            if (!job->universe_map[u]) job->universe_map[u]=(uint32_t)++streams;
        }
    }
    job->clock.streams_n=streams;job->clock.streams_bytes=streams*sizeof(r1_stream);
    job->clock.streams=r1_allocate(job->clock.ledger,job->clock.streams_bytes);
    job->idle_bytes=streams*512*sizeof(float);job->idle=r1_allocate(job->clock.ledger,job->idle_bytes);
    if (!job->clock.streams || !job->idle) return R1_BUDGET;
    for (uint32_t u=1;u<R1_UNIVERSE_LIMIT;u++) {
        uint32_t slot=job->universe_map[u];
        if (slot) job->clock.streams[slot-1].universe=u;
    }
    return R1_OK;
}

static int r3_idle_model(r3_job* job)
{
    uint8_t* defaults=r1_allocate(job->clock.ledger,job->clock.streams_n*512);
    int status=R1_OK;
    if (!defaults) return R1_BUDGET;
    for (uint64_t i=0;i<job->defaults_n;i++) {
        const r3_default_raw* raw=job->defaults+i;
        if (!raw->universe || raw->universe>=R1_UNIVERSE_LIMIT || raw->reserved || raw->data_size>512 || raw->active_size>512) {status=R1_PENDING;break;}
        uint32_t slot=job->universe_map[raw->universe];
        if (!slot) continue;
        for (uint32_t c=0;c<raw->active_size;c++) if (raw->active[c]) {
            defaults[(slot-1)*512+c]=1;job->idle[(slot-1)*512+c]=c<raw->data_size ? (float)raw->data[c] : 0.f;
        }
    }
    for (uint64_t i=0;i<job->fixtures_n && !status;i++) {
        const r3_fixture_raw* fixture=job->fixtures+i;double first;
        const r3_definition_raw* def=r3_definition(job,fixture->definition);
        if (!fixture->universe || fixture->universe>=R1_UNIVERSE_LIMIT || fixture->reserved) {status=R1_PENDING;break;}
        uint32_t slot=job->universe_map[fixture->universe];
        if (!slot || !def) continue;
        if (r3_integer(fixture->start_channel,1.,&first)) {status=R1_PENDING;break;}
        int start=(int)first-1;
        if (start<0 || start>=512) continue;
        for (uint32_t k=0;k<def->channel_count && start+(int)k<512;k++) {
            int c=start+(int)k;
            if (def->channel_kinds[k]==R3_MOTOR && !defaults[(slot-1)*512+c]) job->idle[(slot-1)*512+c]=128.f;
        }
    }
    r1_free(job->clock.ledger,defaults,job->clock.streams_n*512);return status;
}
