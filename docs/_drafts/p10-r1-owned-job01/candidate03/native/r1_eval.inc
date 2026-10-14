typedef struct {
    uint8_t raw_active[512], data[512], effective[1024], data_eff[1024];
    uint8_t output[1024], output_active[1024];
    float gains[512], data_f[512], changes[512];
    int counts[2], winner[1024];
} r1_eval_scratch;

static int r1_mix(r1_job* job, r1_stream* stream, r1_eval_scratch* scratch, const double* scores)
{
    int offsets[3]={0,1,job->record ? 2 : 1}, flat[2]={0,0};
    return ce_prepared_frame_resolve_mix(1,scratch->data,scratch->raw_active,stream->anchor,
        scores,2,offsets,flat,offsets[2],1.,scratch->effective,scratch->data_eff,
        scratch->counts,scratch->winner,scratch->output,scratch->output_active);
}

static void r1_copy_outputs(r1_block* block, uint64_t row, const r1_eval_scratch* scratch)
{
    int main=0,record=0;
    memcpy(block->main_data+row*512,scratch->output,512);
    memcpy(block->main_active+row*512,scratch->output_active,512);
    memcpy(block->record_data+row*512,scratch->output+512,512);
    memcpy(block->record_active+row*512,scratch->output_active+512,512);
    for (int c=0;c<512;c++) {main|=scratch->output_active[c];record|=scratch->output_active[c+512];}
    block->main_present[row]=(uint8_t)(main!=0); block->record_present[row]=(uint8_t)(record!=0);
}

static int r1_evaluate_stream(r1_job* job, r1_stream* stream,
                              double source, double* scores, r1_eval_scratch* scratch)
{
    uint64_t begin=stream->begin;
    int index=ce_frame_index(job->times+begin,(int)stream->count,source), status;
    ce_prepared_task task;
    const uint8_t* raw=r1_source_row(job,begin+(uint64_t)index);
    if (!r1_active(job,stream,source,raw,scratch->raw_active)) return 1;
    if (!r1_takeover_active(job,stream->universe,scratch->raw_active)) return 1;
    if (!stream->anchor_ready) {
        for (int c=0;c<512;c++) stream->anchor[c]=scratch->raw_active[c]
            ? ((float)stream->maximum[c]+(float)stream->minimum[c])/2.f : 0.f;
        stream->anchor_ready=1;
    }
    memset(&task,0,sizeof(task)); task.raw=raw; task.active=scratch->raw_active;
    for (int c=0;c<512;c++) scratch->gains[c]=1.f;
    r1_sparse_lookup(job,stream,(uint64_t)index,scratch->changes);
    task.gains=scratch->gains; task.changes=scratch->changes;
    r1_score_scalars(job,&task);
    status=ce_prepared_frame_eval(1,&task,scratch->data_f,scratch->data,scores);
    return status ? R1_KERNEL : r1_mix(job,stream,scratch,scores);
}

static int r1_render_frame(r1_job* job, r1_block* block, uint64_t frame, r1_eval_scratch* scratch)
{
    double position=r1_position(job,job->cursor), source;
#ifdef R1_TEST_FAIL_CURSOR
    if (job->cursor==(uint64_t)R1_TEST_FAIL_CURSOR) return R1_KERNEL;
#endif
    r1_state_before_frame(job);
    block->positions[frame]=position;
    if (job->start<=position && position<job->start+job->duration) {
        source=r1_source_time(job,position);
        for (uint64_t slot=0;slot<job->streams_n;slot++) {
            uint64_t row=frame*job->streams_n+slot;
            int status=r1_evaluate_stream(job,job->streams+slot,source,block->scores+row*512,scratch);
            if (status<0) return R1_KERNEL;
            if (!status) r1_copy_outputs(block,row,scratch);
        }
    }
    job->last_time=position; job->cursor++;
    return R1_OK;
}

static int r1_render_block(r1_job* job, r1_block* block)
{
    r1_eval_scratch* scratch=r1_allocate(job->ledger,sizeof(*scratch));
    int status=R1_BUDGET;
    if (!scratch) return status;
    r1_empty_scores(block);
    for (uint64_t frame=0;frame<block->frame_count;frame++) {
        status=r1_render_frame(job,block,frame,scratch);
        if (status) break;
    }
    r1_free(job->ledger,scratch,sizeof(*scratch));
    block->next_index=job->cursor; block->last_time=job->last_time;
    if (job->state) {
        memcpy(block->state,job->state,job->state->allocation_bytes);
        block->pending_state=0;
    }
    return status;
}
