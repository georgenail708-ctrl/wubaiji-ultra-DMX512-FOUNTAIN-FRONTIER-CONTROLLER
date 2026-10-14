static int r1_atom_number(r1_atom atom, double missing, double* result)
{
    int64_t integer;
    switch (atom.kind) {
    case R1_MISSING:*result=missing;break;
    case R1_F64:memcpy(result,&atom.bits,sizeof(double));break;
    case R1_I64:memcpy(&integer,&atom.bits,sizeof(integer));*result=(double)integer;break;
    case R1_BOOL:*result=atom.bits ? 1.0 : 0.0;break;
    case R1_NONE:*result=0.0;break;
    default:return R1_PENDING;
    }
    if (atom.reserved || !isfinite(*result)) return R1_PENDING;
    return R1_OK;
}

static int r1_atoms(const r1_input* input, double* values)
{
    static const double defaults[R1_ATOMS]={0.,0.,1.,0.,23.,0.,0.,1.};
    for (int k=0;k<R1_ATOMS;k++) {
        if (r1_atom_number(input->atoms[k],defaults[k],values+k)) return R1_PENDING;
    }
    return R1_OK;
}

static int r1_input_shape(const r1_input* input)
{
    if (!input || input->abi!=R1_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    if (!input->frame_count || input->frame_count>INT_MAX) return R1_PENDING;
    if (!input->frames || (input->channel_count && !input->channels)) return R1_INVALID;
    if (input->frame_count>SIZE_MAX/sizeof(r1_raw_frame)) return R1_INVALID;
    if (input->channel_count>SIZE_MAX/sizeof(r1_channel)) return R1_INVALID;
    return R1_OK;
}

static int r1_frames_gate(const r1_input* input, uint64_t* pending)
{
    double time;
    for (uint64_t i=0;i<input->frame_count;i++) {
        const r1_raw_frame* frame=input->frames+i;
        if (!frame->universe || frame->universe>=R1_UNIVERSE_LIMIT) *pending|=R1_PENDING_UNIVERSE;
        if (frame->payload_size!=512) *pending|=R1_PENDING_PAYLOAD;
        if (r1_atom_number(frame->timestamp,0.,&time)) *pending|=R1_PENDING_ATOM;
    }
    return *pending ? R1_PENDING : R1_OK;
}

static int r1_gate(const r1_input* input, double* values, r1_report* report)
{
    int status=r1_input_shape(input);
    memset(report,0,sizeof(*report));
    report->abi=R1_ABI; report->struct_size=sizeof(*report);
    report->pending_state=R1_STATE_PENDING;
    if (status) return status;
    report->source_token=input->source_token;
    /* Declared shape admission precedes any frame/channel/state traversal.
     * Compilation will additionally charge actual per-universe sparse padding. */
    if (input->byte_budget<R1_UNIVERSE_LIMIT*sizeof(uint32_t)+sizeof(r1_job)+sizeof(r1_ledger)+sizeof(r1_stream)
            +R1_KERNEL_RESERVE+input->frame_count*(sizeof(r1_raw_frame)+16+2*sizeof(r1_sort_row))
            +input->channel_count*sizeof(r1_channel)) return R1_BUDGET;
    if (input->features) report->pending_features|=R1_PENDING_FEATURE;
    if (r1_atoms(input,values)) report->pending_features|=R1_PENDING_ATOM;
    if (input->atoms[R1_TIMER_MS].kind==R1_NONE) report->pending_features|=R1_PENDING_ATOM;
    /* Frames are validated incrementally after the budget-admitted owned copy. */
    return report->pending_features ? R1_PENDING : R1_OK;
}

static int r1_clock_plan(r1_job* job, const double* values)
{
    double ratio, n;
    job->start=values[R1_START]; job->duration=_ce_rt_nonnegative(values[R1_DURATION]);
    job->rate=_ce_rt_max_left(.001,values[R1_RATE] ? values[R1_RATE] : 1.);
    job->source_duration=_ce_rt_nonnegative(values[R1_SOURCE_DURATION]);
    job->declared_source=job->source_duration;
    if (!(job->source_duration>0.)) job->source_duration=_ce_rt_nonnegative(job->duration*job->rate);
    job->interval=_ce_rt_max_left(.005,values[R1_TIMER_MS]/1000.);
    job->interval=_ce_rt_min_left(_ce_rt_max_left(job->interval,.015),.05);
    job->track_order=values[R1_TRACK_ORDER]; job->record=values[R1_RECORD]!=0.;
    job->resize=values[R1_RESIZE]!=0.; job->cycle_first=INFINITY; job->cycle_last=-INFINITY;
    job->end=_ce_rt_max_left(0.,job->start+job->duration); job->last_time=NAN;
    ratio=job->end/job->interval; n=ceil(ratio);
    if (!isfinite(n) || n>INT64_MAX-2.) return R1_PENDING;
    job->total_frames=(uint64_t)n+1;
    return R1_OK;
}
