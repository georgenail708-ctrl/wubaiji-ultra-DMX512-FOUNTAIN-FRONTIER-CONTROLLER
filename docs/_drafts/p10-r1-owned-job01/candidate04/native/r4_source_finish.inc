static int r4_source_dimensions_one(r4_job* job)
{
    r1_job* source=job->scene->clips[job->row].source;
    r4_clip* extra=job->clips+job->row;
    if (job->slot<source->streams_n) {
        r1_stream* stream=source->streams+job->slot++;
        stream->begin=extra->source_begin;extra->source_begin+=stream->count;
        stream->sparse_begin=extra->sparse_begin;extra->sparse_begin+=(stream->count+63)/64;return R1_OK;
    }
    uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,source->records_n,8+4+4)
            || !r3_add_bytes(&bytes,extra->sparse_begin,512*(sizeof(uint64_t)+sizeof(int32_t)))) return R1_BUDGET;
    r1_free(source->ledger,source->sort_scratch,source->sort_bytes);source->sort_scratch=NULL;
    if (bytes>source->ledger->limit-source->ledger->used) return R1_BUDGET;
    source->times_bytes=source->records_n*sizeof(double);source->data_order_bytes=source->records_n*sizeof(uint32_t);
    source->narrow_bytes=source->records_n*sizeof(float);source->sparse_blocks=extra->sparse_begin;
    source->sparse_word_bytes=extra->sparse_begin*512*sizeof(uint64_t);
    source->sparse_before_bytes=extra->sparse_begin*512*sizeof(int32_t);
    source->times=r1_allocate(source->ledger,source->times_bytes);source->data_order=r1_allocate(source->ledger,source->data_order_bytes);
    source->narrow_times=r1_allocate(source->ledger,source->narrow_bytes);source->change_words=r1_allocate(source->ledger,source->sparse_word_bytes);
    source->change_before=r1_allocate(source->ledger,source->sparse_before_bytes);
    if (!source->times || !source->data_order || !source->narrow_times || !source->change_words || !source->change_before) return R1_BUDGET;
    source->compile_phase=4;source->compile_cursor=0;extra->source_phase=1;return R1_OK;
}

static int r4_source_finish_one(r4_job* job)
{
    r1_job* source=job->scene->clips[job->row].source;r4_clip* extra=job->clips+job->row;
    if (!extra->source_phase) return r4_source_dimensions_one(job);
    if (!r1_compile_finished(source)) return r1_compile_work(source);
    int status=r1_compile_transition(source);if (status) return status;
    job->row++;job->slot=0;
    if (job->row==job->scene->clips_n) {job->row=0;job->phase=6;job->subphase=0;}
    return R1_OK;
}
