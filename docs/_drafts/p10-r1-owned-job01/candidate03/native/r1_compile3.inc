/* Finite timestamps: stable ordering equals original list.sort. Work units
 * bound source-row and merge-row traversals; nonfinite admission stays pending. */
static int r1_compile_storage(r1_job* job, const r1_input* input)
{
    job->records_n=input->frame_count; job->channels_n=input->channel_count;
    job->raw_bytes=job->records_n*sizeof(r1_raw_frame);
    job->channels_bytes=job->channels_n*sizeof(r1_channel);
    job->map_bytes=R1_UNIVERSE_LIMIT*sizeof(uint32_t);
    job->sort_bytes=job->records_n*sizeof(r1_sort_row);
    job->raw_owned=r1_allocate(job->ledger,job->raw_bytes);
    job->compile_map=r1_allocate(job->ledger,job->map_bytes);
    if (job->channels_n) job->channels_owned=r1_allocate(job->ledger,job->channels_bytes);
    if (!job->raw_owned || !job->compile_map || (job->channels_n && !job->channels_owned)) return R1_BUDGET;
    return R1_OK;
}

static int r1_compile_ingest(r1_job* job,const r1_input* input)
{
    int status=r1_compile_storage(job,input);
    if (status) return status;
    /* Retained synchronous R1 facade: not the new large-source scene route. */
    memcpy(job->raw_owned,input->frames,(size_t)job->raw_bytes);
    if (job->channels_n) memcpy(job->channels_owned,input->channels,(size_t)job->channels_bytes);
    return R1_OK;
}

static int r1_discover_one(r1_job* job)
{
    r1_raw_frame* raw=job->raw_owned+job->compile_cursor++;
    double value;
    if (job->scatter) {
        const r3_frame_raw* source=job->scatter+job->compile_cursor-1;
        raw->universe=source->universe;raw->payload_size=source->payload_size;raw->timestamp=source->timestamp;
        if (raw->payload_size!=512 || !source->payload) return R1_PENDING_PAYLOAD;
        memcpy(raw->data,source->payload,512); /* one bounded owning copy */
    }
    if (!raw->universe || raw->universe>=R1_UNIVERSE_LIMIT) return R1_PENDING_UNIVERSE;
    if (raw->payload_size!=512) return R1_PENDING_PAYLOAD;
    if (r1_atom_number(raw->timestamp,0.,&value)) return R1_PENDING_ATOM;
    if (!job->compile_map[raw->universe]) job->compile_map[raw->universe]=(uint32_t)++job->streams_n;
    return 0;
}

static int r1_stream_allocate(r1_job* job)
{
    job->streams_bytes=job->streams_n*sizeof(r1_stream);
    job->streams=r1_allocate(job->ledger,job->streams_bytes);
    job->sort_rows=r1_allocate(job->ledger,job->sort_bytes);
    job->sort_scratch=r1_allocate(job->ledger,job->sort_bytes);
    return job->streams && job->sort_rows && job->sort_scratch ? R1_OK : R1_BUDGET;
}

static void r1_channel_one(r1_job* job)
{
    uint64_t index=job->compile_cursor++;
    if (job->channels_borrowed) job->channels_owned[index]=job->channels_borrowed[index];
    const r1_channel* ch=job->channels_owned+index;
    uint32_t slot;
    if (!ch->universe || ch->universe>=R1_UNIVERSE_LIMIT || ch->channel<1 || ch->channel>512) return;
    slot=job->compile_map[ch->universe];
    if (slot) {job->streams[slot-1].base[ch->channel-1]=1;job->streams[slot-1].explicit_count++;}
}

static void r1_source_row_one(r1_job* job)
{
    uint64_t index=job->compile_cursor++, slot=job->compile_map[job->raw_owned[index].universe]-1;
    r1_raw_frame* raw=job->raw_owned+index;
    r1_stream* stream=job->streams+slot;
    r1_sort_row* row=job->sort_rows+index;
    stream->universe=raw->universe;stream->count++; r1_infer_frame(stream,raw->data);
    row->source=index;row->slot=slot;r1_atom_number(raw->timestamp,0.,&row->time);
    if (row->time==0.) row->time=0.;
    if (row->time>=0.) {
        if (row->time<job->cycle_first) job->cycle_first=row->time;
        if (row->time>job->cycle_last) job->cycle_last=row->time;
    }
}

static int r1_source_allocate(r1_job* job)
{
    uint64_t begin=0;
    for (uint64_t slot=0;slot<job->streams_n;slot++) {
        int any=0;
        r1_stream* stream=job->streams+slot;
        stream->begin=begin;begin+=stream->count;
        for (int c=0;c<512;c++) any|=stream->base[c];
        if (!any) memset(stream->base,1,512);
    }
    job->times_bytes=job->records_n*sizeof(double);job->data_order_bytes=job->records_n*sizeof(uint32_t);
    job->times=r1_allocate(job->ledger,job->times_bytes);job->data_order=r1_allocate(job->ledger,job->data_order_bytes);
    if (!job->times || !job->data_order) return R1_BUDGET;
    return r1_sparse_allocate(job);
}

static void r1_merge_start(r1_job* job)
{
    uint64_t middle=job->merge_begin+job->merge_width;
    job->merge_left=job->merge_begin;job->merge_right=middle;
    if (job->merge_right>job->records_n) job->merge_right=job->records_n;
    job->merge_out=job->merge_begin;
}

static void r1_merge_one(r1_job* job)
{
    uint64_t middle=job->merge_begin+job->merge_width, end=middle+job->merge_width, chosen;
    if (middle>job->records_n) middle=job->records_n;
    if (end>job->records_n) end=job->records_n;
    if (job->merge_left<middle && job->merge_right<end)
        chosen=r1_sort_before(job->sort_rows[job->merge_left],job->sort_rows[job->merge_right]) ? job->merge_left++ : job->merge_right++;
    else chosen=job->merge_left<middle ? job->merge_left++ : job->merge_right++;
    job->sort_scratch[job->merge_out++]=job->sort_rows[chosen];
    if (job->merge_out==end) {
        job->merge_begin=end;
        if (end==job->records_n) {
            r1_sort_row* old=job->sort_rows;job->sort_rows=job->sort_scratch;job->sort_scratch=old;
            job->merge_width*=2;job->merge_begin=0;
        }
        if (job->merge_width<job->records_n) r1_merge_start(job);
    }
}

static void r1_sorted_one(r1_job* job)
{
    uint64_t index=job->compile_cursor++;
    r1_sort_row* row=job->sort_rows+index;
    r1_stream* stream=job->streams+row->slot;
    job->times[index]=row->time;
    job->data_order[index]=(uint32_t)row->source;
    for (int c=0;c<512;c++) {
        uint8_t value=job->raw_owned[row->source].data[c];
        if (index==stream->begin || value<stream->minimum[c]) stream->minimum[c]=value;
        if (value>stream->maximum[c]) stream->maximum[c]=value;
    }
    r1_sparse_store(job,stream,index-stream->begin);
}

static void r1_compile_release(r1_job* job)
{
    job->scatter=NULL;job->channels_borrowed=NULL; /* source owners can unpin */
    /* Preserve raw explicit channel atoms for later C LT ownership compile. */
    r1_free(job->ledger,job->sort_rows,job->sort_bytes);job->sort_rows=NULL;
    r1_free(job->ledger,job->sort_scratch,job->sort_bytes);job->sort_scratch=NULL;
    r1_free(job->ledger,job->compile_map,job->map_bytes);job->compile_map=NULL;
}

static void r1_compile_cycle(r1_job* job)
{
    int flags[16]={0}; double values[24]={0}, out[24]={0};
    job->cycle_period=job->cycle_last-job->cycle_first;
    job->cycle_valid=job->records_n>1 && isfinite(job->cycle_period) && job->cycle_period>1e-6;
    flags[0]=job->cycle_valid;values[0]=job->duration;values[1]=job->rate;
    values[2]=job->declared_source;values[3]=job->cycle_last;
    ce_source_motion_clock(6,flags,values,NULL,out,NULL);job->extends_source=out[2]!=0.;
}
