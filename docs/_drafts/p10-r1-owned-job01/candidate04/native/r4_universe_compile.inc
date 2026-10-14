static int r4_universe_discover_one(r4_job* job)
{
    r3_job* scene=job->scene;
    if (job->row==scene->clips_n) {job->subphase=1;job->row=0;return R1_OK;}
    r1_job* source=scene->clips[job->row].source;
    if (job->slot==source->streams_n) {job->row++;job->slot=0;return R1_OK;}
    uint32_t u=source->streams[job->slot++].universe;
    scene->slots_n++;
    if (!scene->universe_map[u]) scene->universe_map[u]=(uint32_t)++scene->clock.streams_n;
    return R1_OK;
}

static int r4_universe_allocate(r4_job* job)
{
    r3_job* scene=job->scene;uint64_t bytes=0,n=scene->clock.streams_n;
    if (!r3_add_bytes(&bytes,n,sizeof(r1_stream)+512*sizeof(float)+512)) return R1_BUDGET;
    if (bytes>scene->clock.ledger->limit-scene->clock.ledger->used) return R1_BUDGET;
    scene->clock.streams_bytes=n*sizeof(r1_stream);scene->idle_bytes=n*512*sizeof(float);job->defaults_seen_bytes=n*512;
    scene->clock.streams=r1_allocate(scene->clock.ledger,scene->clock.streams_bytes);
    scene->idle=r1_allocate(scene->clock.ledger,scene->idle_bytes);
    job->defaults_seen=r1_allocate(scene->clock.ledger,job->defaults_seen_bytes);
    if (!scene->clock.streams || !scene->idle || !job->defaults_seen) return R1_BUDGET;
    job->universe=1;job->subphase=2;return R1_OK;
}

static int r4_universe_sort_one(r4_job* job)
{
    r3_job* scene=job->scene;uint32_t u=(uint32_t)job->universe++;
    uint32_t slot=scene->universe_map[u];if (slot) scene->clock.streams[slot-1].universe=u;
    if (job->universe==R1_UNIVERSE_LIMIT) {job->subphase=3;job->row=0;}
    return R1_OK;
}

static int r4_default_one(r4_job* job)
{
    r3_job* scene=job->scene;
    if (job->row==scene->defaults_n) {job->subphase=4;job->row=0;job->scan=0;job->selected=UINT64_MAX;return R1_OK;}
    const r3_default_raw* raw=scene->defaults+job->row++;
    if (!raw->universe || raw->universe>=R1_UNIVERSE_LIMIT || raw->reserved || raw->data_size>512 || raw->active_size>512) return R1_PENDING;
    uint32_t slot=scene->universe_map[raw->universe];if (!slot) return R1_OK;
    for (uint32_t c=0;c<raw->active_size;c++) if (raw->active[c]) {
        job->defaults_seen[(slot-1)*512+c]=1;scene->idle[(slot-1)*512+c]=c<raw->data_size ? (float)raw->data[c] : 0.f;
    }
    return R1_OK;
}

static int r4_idle_fixture_one(r4_job* job)
{
    r3_job* scene=job->scene;
    if (job->row==scene->fixtures_n) {job->subphase=5;return R1_OK;}
    const r3_fixture_raw* fixture=scene->fixtures+job->row;
    if (!fixture->universe || fixture->universe>=R1_UNIVERSE_LIMIT || fixture->reserved) return R1_PENDING;
    if (job->scan<scene->definitions_n) {
        uint64_t index=job->scan++;if (scene->definitions[index].token==fixture->definition) job->selected=index;
        return R1_OK;
    }
    uint32_t slot=scene->universe_map[fixture->universe];int start;
    if (slot && job->selected!=UINT64_MAX) {
        int status=r4_motor_address(fixture->start_channel,&start);if (status<0) return status;
        const r3_definition_raw* def=scene->definitions+job->selected;
        if (status) for (uint32_t k=0;k<def->channel_count && start+(int)k<512;k++) {
            int c=start+(int)k;if (def->channel_kinds[k]==R3_MOTOR && !job->defaults_seen[(slot-1)*512+c]) scene->idle[(slot-1)*512+c]=128.f;
        }
    }
    job->row++;job->scan=0;job->selected=UINT64_MAX;return R1_OK;
}

static int r4_universe_one(r4_job* job)
{
    switch(job->subphase) {
    case 0:return r4_universe_discover_one(job);
    case 1:return r4_universe_allocate(job);
    case 2:return r4_universe_sort_one(job);
    case 3:return r4_default_one(job);
    case 4:return r4_idle_fixture_one(job);
    case 5:{
        int status=r3_lt_arrays(job->scene);if (!status) status=r3_work_allocate(job->scene);
        if (status) return status;
        r1_free(job->scene->clock.ledger,job->defaults_seen,job->defaults_seen_bytes);job->defaults_seen=NULL;
        job->row=job->slot=job->subphase=0;job->phase=7;return R1_OK;
    }
    default:return R1_INVALID;
    }
}
