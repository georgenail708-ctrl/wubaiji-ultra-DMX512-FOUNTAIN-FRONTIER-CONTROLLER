static uint64_t r1_block_base(uint64_t streams)
{
    uint64_t bytes=sizeof(r1_block)+streams*sizeof(uint32_t);
    return (bytes+7)&~UINT64_C(7);
}

static r1_block* r1_new_block(r1_job* job, const r1_step* request)
{
    uint64_t base=r1_block_base(job->streams_n);
    uint64_t per=sizeof(double)+job->streams_n*(512*12+2);
    uint64_t frames=job->total_frames-job->cursor, bytes;
    uint64_t state_bytes=job->state ? job->state->allocation_bytes : 0;
    uint8_t* cursor;
    r1_block* block;
    if (frames>request->max_frames) frames=request->max_frames;
    if (request->max_block_bytes<base+state_bytes+per+7) return NULL;
    if (frames>(request->max_block_bytes-base-state_bytes-7)/per) frames=(request->max_block_bytes-base-state_bytes-7)/per;
    bytes=((base+frames*per+7)&~UINT64_C(7))+state_bytes;
    block=r1_allocate(job->ledger,bytes);
    if (!block) return NULL;
    block->magic=R1_MAGIC; block->allocation_bytes=bytes; block->ledger=job->ledger;
    job->ledger->refs++; block->abi=R1_ABI; block->struct_size=sizeof(*block);
    block->first_index=job->cursor; block->frame_count=frames; block->stream_count=job->streams_n;
    block->source_token=job->source_token; block->pending_state=R1_STATE_PENDING;
    block->universes=(uint32_t*)(block+1); cursor=(uint8_t*)block+base;
    block->positions=(double*)cursor; cursor+=frames*sizeof(double);
    block->scores=(double*)cursor; cursor+=frames*job->streams_n*512*sizeof(double);
    block->main_data=cursor; cursor+=frames*job->streams_n*512;
    block->main_active=cursor; cursor+=frames*job->streams_n*512;
    block->record_data=cursor; cursor+=frames*job->streams_n*512;
    block->record_active=cursor; cursor+=frames*job->streams_n*512;
    block->main_present=cursor; cursor+=frames*job->streams_n;
    block->record_present=cursor;
    cursor+=frames*job->streams_n;
    if (state_bytes) block->state=(r1_state*)(((uintptr_t)cursor+7)&~(uintptr_t)7);
    for (uint64_t slot=0;slot<job->streams_n;slot++) block->universes[slot]=job->streams[slot].universe;
    return block;
}

static void r1_empty_scores(r1_block* block)
{
    uint64_t n=block->frame_count*block->stream_count*512;
    for (uint64_t i=0;i<n;i++) block->scores[i]=-INFINITY;
}
