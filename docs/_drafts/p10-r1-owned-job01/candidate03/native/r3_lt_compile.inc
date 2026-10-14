static int r3_lt_arrays(r3_job* job)
{
    uint64_t n=job->fixtures_n, bytes=n*(8*sizeof(double)+4*3+3+1);
    uint8_t* cursor;
    if (!n) return R1_OK;
    cursor=r1_allocate(job->clock.ledger,bytes);job->lt_bytes=bytes;
    if (!cursor) return R1_BUDGET;
    job->lt_state=(double*)cursor;cursor+=n*4*sizeof(double);
    job->lt_time=(double*)cursor;cursor+=n*4*sizeof(double);
    job->lt_exists=cursor;cursor+=n*4;
    job->lt_touched=cursor;cursor+=n*4;
    job->lt_color=cursor;cursor+=n*3;
    job->lt_color_valid=cursor;cursor+=n;
    job->lt_touched_color=cursor;return R1_OK;
}

static int r3_has_presence(const r3_clip* clip,uint32_t u)
{
    for (uint64_t i=0;i<clip->presence_n;i++) if (clip->channel_presence[i]==u) return 1;
    return 0;
}

static void r3_lt_owned(const r3_clip* clip,uint64_t slot,r3_lt_slot* out)
{
    const r1_stream* stream=clip->source->streams+slot;
    /* Recorded masks events are a feature gate. Explicit empty ownership does
     * not fall through to render's all-channel fallback. */
    if (r3_has_presence(clip,stream->universe)) {
        for (uint64_t i=0;i<clip->source->channels_n;i++) {
            const r1_channel* ch=clip->source->channels_owned+i;
            if (ch->universe==stream->universe && ch->channel>=1 && ch->channel<=512) out->owned[ch->channel-1]=1;
        }
    } else for (int c=0;c<512;c++) out->owned[c]=stream->maximum[c]>0;
}

static int r3_lt_classify(const r3_definition_raw* def,int start,int source,int32_t* row)
{
    for (uint32_t k=0;k<def->channel_count;k++) {
        int c=start+(int)k,kind=(int)def->channel_kinds[k],count=-1,offset=-1;
        if (c<0 || c>=512) continue;
        if (kind==source) {count=4;offset=11;}
        else if (kind>=R3_R && kind<=R3_W) {count=5+kind-R3_R;offset=27+16*(kind-R3_R);}
        if (count>=0) {
            if (row[count]==16) return R1_PENDING;
            row[offset+row[count]++]=c;
        }
    }
    return R1_OK;
}

static int r3_lt_fixture(r3_job* job,r3_clip* clip,r3_lt_slot* slot,uint64_t index)
{
    const r3_fixture_raw* fixture=job->fixtures+index;
    const r3_definition_raw* def=r3_definition(job,fixture->definition);
    double first;int32_t row[91]={0};int member=0;
    if (!def) return R1_OK;
    if (fixture->package_token) {job->pending_features|=R3_PENDING_PACKAGE;return R1_PENDING;}
    if (r3_integer(fixture->start_channel,1.,&first)) return R1_PENDING;
    if (first<INT_MIN+1. || first>INT_MAX) return R1_PENDING;
    int start=(int)first-1;
    if (r3_lt_classify(def,start,clip->source_kind,row)) return R1_PENDING;
    for (uint32_t k=0;k<def->channel_count;k++) {
        int c=start+(int)k;
        if (c>=0 && c<512) member|=slot->owned[c];
    }
    if (!member || !row[4] || !(row[5] || row[6] || row[7] || row[8])) return R1_OK;
    row[1]=(int32_t)(index*4+(clip->source_kind==R3_VALVE ? 2 : 0));row[2]=row[1]+1;row[3]=(int32_t)index;
    for (int group=0;group<4;group++) for (int k=0;k<row[5+group];k++) slot->light[row[27+group*16+k]]=1;
    memcpy(slot->fx_rows+(size_t)slot->scope.n_fx*91,row,sizeof(row));slot->scope.n_fx++;
    return R1_OK;
}

static int r3_lt_scope(r3_job* job,r3_clip* clip,uint64_t index)
{
    r3_lt_slot* slot=clip->lt_slots+index;_lt_scope* scope=&slot->scope;
    uint32_t u=clip->source->streams[index].universe;
    uint64_t bytes=job->fixtures_n*91*sizeof(int32_t);
    r3_lt_owned(clip,index,slot);
    if (bytes) {slot->fx_rows=r1_allocate(job->clock.ledger,bytes);if (!slot->fx_rows) return R1_BUDGET;}
    scope->threshold=clip->threshold;scope->attack=clip->attack;scope->release=clip->release;
    scope->color_is_white=clip->white;scope->cr=clip->red;scope->cg=clip->green;scope->cb=clip->blue;
    scope->max_brightness=clip->brightness;scope->light_mask=slot->light;
    scope->src_active_mask=clip->source->streams[index].base;scope->fx=slot->fx_rows;
    for (uint64_t i=0;i<job->fixtures_n;i++) if (job->fixtures[i].universe==u) {
        int status=r3_lt_fixture(job,clip,slot,i);if (status) return status;
    }
    return R1_OK;
}

static int r3_clip_masks(r3_job* job,r3_clip* clip)
{
    uint64_t n=clip->source->streams_n;
    clip->consumed_bytes=n*513;
    clip->consumed=r1_allocate(job->clock.ledger,clip->consumed_bytes);
    if (!clip->consumed) return R1_BUDGET;
    clip->consumed_seen=clip->consumed+n*512;
    if (job->clock.state) {
        const r1_taken* taken=(const r1_taken*)((uint8_t*)job->clock.state+job->clock.state->taken_offset);
        for (uint64_t i=0;i<job->clock.state->taken_count;i++) for (uint64_t s=0;s<n;s++) {
            if (taken[i].source_token==clip->source->source_token && taken[i].universe==clip->source->streams[s].universe) {
                memcpy(clip->consumed+s*512,taken[i].mask,512);clip->consumed_seen[s]=1;
            }
        }
    }
    if (!clip->lt) return R1_OK;
    clip->lt_bytes=n*sizeof(r3_lt_slot);clip->lt_slots=r1_allocate(job->clock.ledger,clip->lt_bytes);
    if (!clip->lt_slots) return R1_BUDGET;
    for (uint64_t s=0;s<n;s++) {int status=r3_lt_scope(job,clip,s);if (status) return status;}
    return R1_OK;
}
