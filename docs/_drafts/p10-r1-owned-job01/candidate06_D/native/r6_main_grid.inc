/* Exact ROOT-owned G0 Main contract. Its raw extent includes all authored
 * clips, independently of the numerical renderer's supported source slots.
 * No source/end/timer guessing, no host-computed positions/frame count. */
typedef char r6_g0_atom_layout[(sizeof(r1_atom)==sizeof(G0Atom)
    && offsetof(r1_atom,kind)==offsetof(G0Atom,kind)
    && offsetof(r1_atom,reserved)==offsetof(G0Atom,reserved)
    && offsetof(r1_atom,bits)==offsetof(G0Atom,bits)) ? 1 : -1];
static int r6_source_current(const r6_job* job)
{
    const r6_input* input=job->borrowed;
    if (!input || input->abi!=R6_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    const r3_input* raw=&input->raw.raw.raw;
    if (raw->clips!=job->authored_rows || raw->clip_count!=job->authored_n
            || raw->source_token!=job->source_epoch || input->model_epoch!=job->model_epoch
            || input->fixture_epoch!=job->fixture_epoch
            || job->timeline->base->scene->clock.source_token!=job->source_epoch) return R1_PENDING;
    if (job->grid_state && (job->grid_state->request.rows!=raw->clips
            || job->grid_state->request.count!=raw->clip_count
            || job->grid_state->request.timer_ms!=input->main_timer_ms
            || job->grid_state->request.timer_present!=input->main_timer_present)) return R1_PENDING;
    return R1_OK;
}

static int r6_grid_status(r6_job* job,int status)
{
    if (status==G0_BUILDING) return R1_OK;
    if (status==G0_OK) {job->grid_ready=1;return R1_OK;}
    job->timeline->base->scene->pending_features|=R4_PENDING_GRID;
    if (status==G0_BIG_COUNT) return R1_BUDGET;
    if (status==G0_CEIL_INF) {job->semantic=R6_SEMANTIC_CEIL_INFINITY;return R1_KERNEL;}
    return status==G0_BAD_ABI || status==G0_BAD_BUFFER || status==G0_BAD_UNITS ? R1_INVALID : R1_PENDING;
}

static int r6_main_grid_compile_one(r6_job* job)
{
    int status=r6_source_current(job);if (status) return status;
    if (job->cancelled || job->timeline->base->scene->clock.cancelled) return R1_CANCELLED;
    if (job->grid_ready) return R1_OK;
    if (job->grid_state) return r6_grid_status(job,ce_main_grid_step_g0_v7(job->grid_state,1,&job->grid));
    const r3_input* raw=&job->borrowed->raw.raw.raw;uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,raw->clip_count,sizeof(r3_clip_raw)) || bytes>raw->external_bytes) return R1_BUDGET;
    G0Request request={0};request.abi=G0_ABI;request.size=sizeof(request);request.rows=raw->clips;
    request.count=raw->clip_count;request.row_bytes=bytes;request.stride=sizeof(r3_clip_raw);
    request.start_offset=offsetof(r3_clip_raw,recorded)+offsetof(r1_input,atoms)+R1_START*sizeof(r1_atom);
    request.duration_offset=offsetof(r3_clip_raw,recorded)+offsetof(r1_input,atoms)+R1_DURATION*sizeof(r1_atom);
    request.timer_ms=job->borrowed->main_timer_ms;request.timer_present=job->borrowed->main_timer_present;
    job->grid_state_bytes=sizeof(G0CompileState);
    job->grid_state=r1_allocate(job->timeline->base->scene->clock.ledger,job->grid_state_bytes);
    if (!job->grid_state) return R1_BUDGET;
    return r6_grid_status(job,ce_main_grid_begin_g0_v7(&request,job->grid_state));
}

static int r6_main_grid_adopt(r6_job* job)
{
    if (!job->grid_ready || !job->grid_state || job->grid_state->status!=G0_OK) return R1_INVALID;
    int status=r6_source_current(job);if (status) return status;
    r1_job* clock=&job->timeline->base->scene->clock;
    clock->interval=job->grid.interval;clock->end=job->grid.end;clock->total_frames=job->grid.count;
    return R1_OK;
}

static int r6_main_position(const r6_job* job,double* output)
{
    uint64_t cursor=job->timeline->base->scene->clock.cursor;
    return ce_main_grid_position_g0(&job->grid,cursor,output)==G0_OK ? R1_OK : R1_KERNEL;
}
