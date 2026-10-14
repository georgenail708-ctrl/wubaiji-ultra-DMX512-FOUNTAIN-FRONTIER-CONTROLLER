static uint64_t r3_history_bytes(const r3_job* job)
{
    return sizeof(r3_history)+job->slots_n*sizeof(r1_taken)+job->fixtures_n*(12+8*sizeof(double)+8)+7;
}

static void r3_history_refresh(const r3_job* job,r3_history* history)
{
    uint64_t row=0,n=job->fixtures_n;
    for (uint64_t i=0;i<job->clips_n;i++) {
        const r3_clip* clip=job->clips+i;
        for (uint64_t s=0;s<clip->source->streams_n;s++) {
            r1_taken* taken=history->consumed+row++;
            taken->source_token=clip->source->source_token;taken->universe=clip->source->streams[s].universe;
            taken->payload_size=clip->consumed_seen[s] ? 512 : 0;memcpy(taken->mask,clip->consumed+s*512,512);
        }
    }
    if (n) {
        memcpy(history->lt_state,job->lt_state,(size_t)n*4*sizeof(double));
        memcpy(history->lt_time,job->lt_time,(size_t)n*4*sizeof(double));
        memcpy(history->lt_exists,job->lt_exists,(size_t)n*4);
        memcpy(history->lt_color,job->lt_color,(size_t)n*3);memcpy(history->lt_color_valid,job->lt_color_valid,(size_t)n);
    }
}

static r3_history* r3_history_copy(r3_job* job)
{
    uint64_t bytes=r3_history_bytes(job),n=job->fixtures_n,row=0;
    r3_history* history=r1_allocate(job->clock.ledger,bytes);
    uint8_t* cursor;
    if (!history) return NULL;
    history->abi=R3_ABI;history->struct_size=sizeof(*history);history->allocation_bytes=bytes;
    history->clip_slots=job->slots_n;history->fixtures=n;history->complete_mask=job->clock.state ? R1_STATE_PENDING : 0;
    cursor=(uint8_t*)(history+1);history->consumed=(r1_taken*)cursor;cursor+=job->slots_n*sizeof(r1_taken);
    history->fixture_tokens=(uint64_t*)cursor;cursor+=n*sizeof(uint64_t);
    history->fixture_universes=(uint32_t*)cursor;cursor+=n*sizeof(uint32_t);
    cursor=(uint8_t*)(((uintptr_t)cursor+7)&~(uintptr_t)7);
    history->lt_state=(double*)cursor;cursor+=n*4*sizeof(double);
    history->lt_time=(double*)cursor;cursor+=n*4*sizeof(double);
    history->lt_exists=cursor;cursor+=n*4;
    history->lt_color=cursor;cursor+=n*3;history->lt_color_valid=cursor;
    for (uint64_t i=0;i<job->clips_n;i++) {
        const r3_clip* clip=job->clips+i;
        for (uint64_t s=0;s<clip->source->streams_n;s++) {
            r1_taken* taken=history->consumed+row++;
            taken->source_token=clip->source->source_token;taken->universe=clip->source->streams[s].universe;
            taken->payload_size=clip->consumed_seen[s] ? 512 : 0;memcpy(taken->mask,clip->consumed+s*512,512);
        }
    }
    for (uint64_t i=0;i<n;i++) {history->fixture_tokens[i]=job->fixtures[i].token;history->fixture_universes[i]=job->fixtures[i].universe;}
    if (n) {
        memcpy(history->lt_state,job->lt_state,(size_t)n*4*sizeof(double));
        memcpy(history->lt_time,job->lt_time,(size_t)n*4*sizeof(double));
        memcpy(history->lt_exists,job->lt_exists,(size_t)n*4);
        memcpy(history->lt_color,job->lt_color,(size_t)n*3);memcpy(history->lt_color_valid,job->lt_color_valid,(size_t)n);
    }
    return history;
}

static void r3_capture_state(r3_job* job,r1_block* frames)
{
    if (!job->clock.state) return;
    memcpy(frames->state,job->clock.state,(size_t)job->clock.state->allocation_bytes);frames->pending_state=0;
    /* kind5=typed takeover overlay in R3 envelope; kind6=typed LT exit tables.
     * Preserved original opaque dictionaries remain in the state payload.
     * R3 reader must reconstruct/merge exact original keys; R1 reader alone
     * is explicitly not an acceptable cache consumer for this block. */
    frames->state->domains[2].kind=5;
    if (job->lt_used) for (int d=4;d<=6;d++) frames->state->domains[d].kind=6;
}
