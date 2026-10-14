static int r4_clip_state_allocate(r4_job* job)
{
    r3_clip* clip=job->scene->clips+job->row;uint64_t bytes=0,n=clip->source->streams_n;
    if (!r3_add_bytes(&bytes,n,513+(clip->lt ? sizeof(r3_lt_slot) : 0))) return R1_BUDGET;
    if (bytes>job->scene->clock.ledger->limit-job->scene->clock.ledger->used) return R1_BUDGET;
    clip->consumed_bytes=n*513;clip->consumed=r1_allocate(job->scene->clock.ledger,clip->consumed_bytes);
    if (!clip->consumed) return R1_BUDGET;clip->consumed_seen=clip->consumed+n*512;
    if (clip->lt) {
        clip->lt_bytes=n*sizeof(r3_lt_slot);clip->lt_slots=r1_allocate(job->scene->clock.ledger,clip->lt_bytes);
        if (!clip->lt_slots) return R1_BUDGET;
    }
    job->seed_row=job->seed_slot=0;job->subphase=1;return R1_OK;
}

static int r4_clip_seed_one(r4_job* job)
{
    r3_clip* clip=job->scene->clips+job->row;const r1_state* state=job->scene->clock.state;
    if (!state || job->seed_row==state->taken_count) {job->slot=0;job->subphase=2;return R1_OK;}
    const r1_taken* raw=(const r1_taken*)((const uint8_t*)state+state->taken_offset)+job->seed_row;
    uint64_t s=job->seed_slot++;
    if (raw->source_token==clip->source->source_token && raw->universe==clip->source->streams[s].universe) {
        memcpy(clip->consumed+s*512,raw->mask,512);clip->consumed_seen[s]=1;
    }
    if (job->seed_slot==clip->source->streams_n) {job->seed_slot=0;job->seed_row++;}
    return R1_OK;
}

static int r4_lt_slot_init(r4_job* job)
{
    r3_clip* clip=job->scene->clips+job->row;
    if (!clip->lt || job->slot==clip->source->streams_n) {
        job->row++;job->slot=job->subphase=0;
        if (job->row==job->scene->clips_n) {job->phase=8;job->row=0;}return R1_OK;
    }
    r3_lt_slot* slot=clip->lt_slots+job->slot;_lt_scope* scope=&slot->scope;
    r4_slot* mask=job->clips[job->row].slots+job->slot;r1_stream* stream=clip->source->streams+job->slot;
    memcpy(slot->owned,mask->owned,512);
    scope->threshold=clip->threshold;scope->attack=clip->attack;scope->release=clip->release;
    scope->color_is_white=clip->white;scope->cr=clip->red;scope->cg=clip->green;scope->cb=clip->blue;
    scope->max_brightness=clip->brightness;scope->light_mask=slot->light;scope->src_active_mask=stream->base;
    job->fixture=job->scan=job->lt_count=job->lt_fill=0;job->selected=UINT64_MAX;job->subphase=3;return R1_OK;
}

static int r4_lt_fixture_row(r4_job* job,int32_t* row)
{
    r3_clip* clip=job->scene->clips+job->row;
    const r3_fixture_raw* fixture=job->scene->fixtures+job->fixture;
    const r3_definition_raw* def=job->selected==UINT64_MAX ? NULL : job->scene->definitions+job->selected;
    int start,member=0;
    if (!def) return 0;
    if (fixture->package_token) {job->scene->pending_features|=R3_PENDING_PACKAGE;return R1_PENDING;}
    int status=r4_lt_address(fixture->start_channel,&start);if (status<=0) return status;
    memset(row,0,91*sizeof(int32_t));
    if (r3_lt_classify(def,start,clip->source_kind,row)) return R1_PENDING;
    r3_lt_slot* slot=clip->lt_slots+job->slot;
    for (uint32_t k=0;k<def->channel_count;k++) if (start+(int)k<512) member|=slot->owned[start+(int)k];
    if (!member || !row[4] || !(row[5] || row[6] || row[7] || row[8])) return 0;
    row[1]=(int32_t)(job->fixture*4+(clip->source_kind==R3_VALVE ? 2 : 0));row[2]=row[1]+1;row[3]=(int32_t)job->fixture;
    return 1;
}

static int r4_lt_member_one(r4_job* job)
{
    r3_job* scene=job->scene;r3_clip* clip=scene->clips+job->row;r3_lt_slot* slot=clip->lt_slots+job->slot;
    if (job->fixture==scene->fixtures_n) {
        r4_slot* extra=job->clips[job->row].slots+job->slot;
        if (job->subphase==3) {
            uint64_t bytes=0;if (!r3_add_bytes(&bytes,job->lt_count,91*sizeof(int32_t))) return R1_BUDGET;
            extra->lt_rows=job->lt_count;extra->lt_fx_bytes=bytes;
            if (bytes) {slot->fx_rows=r1_allocate(scene->clock.ledger,bytes);if (!slot->fx_rows) return R1_BUDGET;}
            slot->scope.fx=slot->fx_rows;job->fixture=0;job->subphase=4;return R1_OK;
        }
        if (job->lt_fill!=extra->lt_rows) return R1_INVALID;
        slot->scope.n_fx=(int)job->lt_fill;job->slot++;job->subphase=2;return R1_OK;
    }
    const r3_fixture_raw* fixture=scene->fixtures+job->fixture;
    if (fixture->universe!=clip->source->streams[job->slot].universe) {job->fixture++;return R1_OK;}
    if (job->scan<scene->definitions_n) {
        uint64_t index=job->scan++;if (scene->definitions[index].token==fixture->definition) job->selected=index;return R1_OK;
    }
    int32_t row[91];int found=r4_lt_fixture_row(job,row);if (found<0) return found;
    if (found) {
        if (job->subphase==3) job->lt_count++;
        else {
            if (job->lt_fill==job->lt_count) return R1_INVALID;
            memcpy(slot->fx_rows+job->lt_fill++*91,row,sizeof(row));
            for (int g=0;g<4;g++) for (int k=0;k<row[5+g];k++) slot->light[row[27+g*16+k]]=1;
        }
    }
    job->fixture++;job->scan=0;job->selected=UINT64_MAX;return R1_OK;
}

static int r4_clip_state_one(r4_job* job)
{
    switch(job->subphase) {
    case 0:return r4_clip_state_allocate(job);
    case 1:return r4_clip_seed_one(job);
    case 2:return r4_lt_slot_init(job);
    case 3:case 4:return r4_lt_member_one(job);
    default:return R1_INVALID;
    }
}
