static int r1_plan_buffers(r1_job* job)
{
    uint64_t n=job->records_n;
    job->streams_bytes=job->streams_n*sizeof(r1_stream);
    job->times_bytes=n*sizeof(double); job->data_bytes=n*512;
    job->changed_bytes=n*512; job->change_bytes=n*512*sizeof(float);
    job->streams=r1_allocate(job->ledger,job->streams_bytes);
    job->times=r1_allocate(job->ledger,job->times_bytes);
    job->data=r1_allocate(job->ledger,job->data_bytes);
    job->changed=r1_allocate(job->ledger,job->changed_bytes);
    job->last_change=r1_allocate(job->ledger,job->change_bytes);
    return job->streams && job->times && job->data && job->changed && job->last_change ? R1_OK : R1_BUDGET;
}

static void r1_store_sorted(const r1_input* input, r1_job* job, const r1_sort_row* rows)
{
    uint64_t begin=0;
    for (uint64_t slot=0;slot<job->streams_n;slot++) {
        r1_stream* stream=job->streams+slot;
        int touched=0;
        stream->begin=begin; begin+=stream->count;
        for (int c=0;c<512;c++) touched|=stream->base[c];
        if (!touched) memset(stream->base,1,512);
    }
    for (uint64_t i=0;i<job->records_n;i++) {
        job->times[i]=rows[i].time;
        memcpy(job->data+i*512,input->frames[rows[i].source].data,512);
    }
}

static int r1_change_tables(r1_job* job)
{
    for (uint64_t slot=0;slot<job->streams_n;slot++) {
        r1_stream* stream=job->streams+slot;
        uint64_t start=stream->begin, total=stream->count*512;
        int status=ce_cold_clip_change_times_v1(job->data+start*512,job->times+start,
            stream->base,(int64_t)stream->count,(int64_t)total,(int64_t)stream->count,512,
            job->changed+start*512,(int64_t)total,job->last_change+start*512,(int64_t)total);
        if (status<0) return R1_KERNEL;
        if (status>0) return R1_PENDING; /* warning/geterr publication not yet ported */
    }
    return R1_OK;
}

static int r1_compile_rows(const r1_input* input, r1_job* job, uint32_t* map)
{
    uint64_t bytes=input->frame_count*sizeof(r1_sort_row);
    r1_sort_row* rows=r1_allocate(job->ledger,bytes);
    r1_sort_row* scratch=r1_allocate(job->ledger,bytes);
    int status=R1_BUDGET;
    if (rows && scratch) {
        r1_raw_rows(input,job,map,rows); r1_sort(rows,scratch,input->frame_count);
        r1_store_sorted(input,job,rows); status=r1_change_tables(job);
    }
    r1_free(job->ledger,rows,bytes); r1_free(job->ledger,scratch,bytes);
    return status;
}

static int r1_compile(const r1_input* input, r1_job* job)
{
    uint64_t bytes=R1_UNIVERSE_LIMIT*sizeof(uint32_t);
    uint32_t* map=r1_allocate(job->ledger,bytes);
    int status=R1_BUDGET;
    if (map) {
        job->records_n=input->frame_count; job->streams_n=r1_map_streams(input,map);
        status=r1_plan_buffers(job);
        if (!status) status=r1_compile_rows(input,job,map);
    }
    r1_free(job->ledger,map,bytes);
    return status;
}

static void r1_compile_cycle(r1_job* job)
{
    int flags[16]={0};
    double values[24]={0}, out[24]={0};
    job->cycle_period=job->cycle_last-job->cycle_first;
    job->cycle_valid=job->records_n>1 && isfinite(job->cycle_period) && job->cycle_period>1e-6;
    flags[0]=job->cycle_valid; values[0]=job->duration; values[1]=job->rate;
    values[2]=job->declared_source; values[3]=job->cycle_last;
    ce_source_motion_clock(6,flags,values,NULL,out,NULL);
    job->extends_source=out[2]!=0.;
}
