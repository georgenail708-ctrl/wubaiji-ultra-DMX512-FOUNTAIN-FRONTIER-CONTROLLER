static int r1_compile_transition(r1_job* job)
{
    int status=R1_OK;
    switch(job->compile_phase) {
    case 0:status=r1_stream_allocate(job);break;
    case 1:break;
    case 2:job->merge_width=1;job->merge_begin=0;r1_merge_start(job);break;
    case 3:
        r1_free(job->ledger,job->sort_scratch,job->sort_bytes);job->sort_scratch=NULL;
        status=r1_source_allocate(job);break;
    case 4:
        r1_compile_cycle(job);r1_compile_release(job);
        if (job->warning_flags) {job->pending_features|=R1_PENDING_WARNING;status=R1_PENDING;}
        break;
    default:return R1_INVALID;
    }
    if (!status) {job->compile_phase++;job->compile_cursor=0;}
    return status;
}

static int r1_compile_finished(const r1_job* job)
{
    switch(job->compile_phase) {
    case 0:case 2:case 4:return job->compile_cursor==job->records_n;
    case 1:return job->compile_cursor==job->channels_n;
    case 3:return job->merge_width>=job->records_n;
    default:return 1;
    }
}

static int r1_compile_work(r1_job* job)
{
    int pending=0;
    switch(job->compile_phase) {
    case 0:pending=r1_discover_one(job);break;
    case 1:r1_channel_one(job);break;
    case 2:r1_source_row_one(job);break;
    case 3:r1_merge_one(job);break;
    case 4:r1_sorted_one(job);break;
    default:return R1_INVALID;
    }
    if (pending) {job->pending_features|=(uint64_t)pending;return R1_PENDING;}
    return R1_OK;
}

/* Each unit bounds numeric row work; transitions visit at most 64000 streams.
 * Allocation/atomic ingest are separately budget-bounded IO, not cancellable
 * midway. This is not yet a hard wall-time bound for create/calloc. */
static int r1_compile_advance(r1_job* job, uint32_t units)
{
    int status;
    if (!units || units>65536) return R1_INVALID;
    if (job->terminal_error) return job->terminal_error;
    if (job->cancelled) return R1_CANCELLED;
    while (units && job->compile_phase<5) {
        if (r1_compile_finished(job)) {
            status=r1_compile_transition(job);
            if (status) {job->terminal_error=status;return status;}
        } else {
            status=r1_compile_work(job);units--;
            if (status) {job->terminal_error=status;return status;}
        }
    }
    return job->compile_phase==5 ? R1_OK : R1_BUILDING;
}
