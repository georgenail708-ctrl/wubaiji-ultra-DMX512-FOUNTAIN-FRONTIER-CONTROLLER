static void* r1_allocate(r1_ledger* ledger, uint64_t bytes)
{
    void* value;
    if (!bytes || bytes > SIZE_MAX || bytes > ledger->limit-ledger->used) return NULL;
    value=calloc(1,(size_t)bytes);
    if (!value) return NULL;
    ledger->used+=bytes;
    if (ledger->used>ledger->peak) ledger->peak=ledger->used;
    return value;
}

static void r1_free(r1_ledger* ledger, void* value, uint64_t bytes)
{
    if (!value) return;
    free(value); ledger->used-=bytes;
}

static void r1_ledger_release(r1_ledger* ledger)
{
    if (--ledger->refs==0) free(ledger);
}

CE_API void ce_owned_job_destroy_r1(r1_job* job)
{
    r1_ledger* ledger;
    if (!job) return;
    ledger=job->ledger;
    if (job->state) r1_free(ledger,job->state,job->state->allocation_bytes);
    r1_free(ledger,job->streams,job->streams_bytes);
    r1_free(ledger,job->times,job->times_bytes);
    r1_free(ledger,job->data,job->data_bytes);
    r1_free(ledger,job->data_order,job->data_order_bytes);
    r1_free(ledger,job->change_words,job->sparse_word_bytes);
    r1_free(ledger,job->change_before,job->sparse_before_bytes);
    r1_free(ledger,job->narrow_times,job->narrow_bytes);
    r1_free(ledger,job->raw_owned,job->raw_bytes);
    r1_free(ledger,job->channels_owned,job->channels_bytes);
    r1_free(ledger,job->sort_rows,job->sort_bytes);
    r1_free(ledger,job->sort_scratch,job->sort_bytes);
    r1_free(ledger,job->compile_map,job->map_bytes);
    r1_free(ledger,job,sizeof(*job));
    r1_ledger_release(ledger);
}

CE_API int ce_owned_job_release_block_r1(r1_block* block)
{
    r1_ledger* ledger;
    uint64_t bytes;
    if (!block || block->magic!=R1_MAGIC) return R1_INVALID;
    ledger=block->ledger; bytes=block->allocation_bytes; block->magic=0;
    r1_free(ledger,block,bytes); r1_ledger_release(ledger);
    return R1_OK;
}

static void r1_report_current(r1_job* job, r1_report* report, int status)
{
    memset(report,0,sizeof(*report));
    report->abi=R1_ABI; report->struct_size=sizeof(*report); report->status=status;
    report->pending_state=R1_STATE_PENDING; report->source_token=job->source_token;
    if (job->state) report->pending_state=0;
    report->pending_features=job->pending_features;
    report->cursor=job->cursor; report->total_frames=job->total_frames;
    report->streams=job->streams_n; report->used_bytes=job->ledger->used;
    report->peak_bytes=job->ledger->peak; report->interval=job->interval;
    report->kernel_reserved_bytes=R1_KERNEL_RESERVE;
    report->end=job->end; report->last_time=job->last_time;
}
