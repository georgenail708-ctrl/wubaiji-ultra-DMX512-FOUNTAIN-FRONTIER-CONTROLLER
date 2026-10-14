static int r1_domain_shape(const r1_raw_domain* value)
{
    if (value->kind>2 || value->reserved) return R1_PENDING;
    if (value->bytes && !value->data) return R1_PENDING;
    if (value->kind!=1 && value->bytes) return R1_PENDING;
    return value->kind==1 && !value->bytes ? R1_PENDING : R1_OK;
}

static int r1_state_domains(const r1_raw_state* source)
{
    for (int d=0;d<9;d++) {
        if (r1_domain_shape(source->domains+d)) return R1_PENDING;
    }
    return source->domains[4].kind==1 && source->domains[5].kind==1 ? R1_OK : R1_PENDING;
}

static int r1_state_taken(const r1_raw_state* source)
{
    if (source->taken_count && !source->taken) return R1_INVALID;
    if (source->taken_count>SIZE_MAX/sizeof(r1_taken)) return R1_INVALID;
    for (uint64_t i=0;i<source->taken_count;i++) {
        if (source->taken[i].payload_size!=512) return R1_PENDING;
    }
    return R1_OK;
}

static int r1_state_shape(const r1_raw_state* source)
{
    int status;
    if (source->abi!=R1_ABI || source->struct_size!=sizeof(*source)) return R1_INVALID;
    if (source->origin_mode>1 || source->reserved) return R1_PENDING;
    status=r1_state_domains(source);
    return status ? status : r1_state_taken(source);
}

static uint64_t r1_state_bytes(const r1_raw_state* source, uint64_t limit)
{
    uint64_t bytes;
    if (limit<sizeof(r1_state) || source->taken_count>(limit-sizeof(r1_state))/sizeof(r1_taken)) return 0;
    bytes=sizeof(r1_state)+source->taken_count*sizeof(r1_taken);
    if (bytes>limit) return 0;
    for (int d=0;d<9;d++) {
        if (source->domains[d].bytes>limit-bytes) return 0;
        bytes+=source->domains[d].bytes;
    }
    return bytes;
}

static void r1_copy_state(r1_state* state, const r1_raw_state* source, uint64_t bytes)
{
    uint64_t offset=sizeof(*state);
    state->abi=R1_ABI; state->struct_size=sizeof(*state); state->allocation_bytes=bytes;
    state->complete_mask=R1_STATE_PENDING; state->causal_revision=source->causal_revision;
    state->origin_mode=source->origin_mode;
    state->taken_count=source->taken_count; state->taken_offset=offset;
    if (source->taken_count) memcpy((uint8_t*)state+offset,source->taken,source->taken_count*sizeof(r1_taken));
    offset+=source->taken_count*sizeof(r1_taken);
    for (int d=0;d<9;d++) {
        state->domains[d].kind=source->domains[d].kind;
        state->domains[d].bytes=source->domains[d].bytes; state->domains[d].offset=offset;
        if (source->domains[d].bytes) memcpy((uint8_t*)state+offset,source->domains[d].data,source->domains[d].bytes);
        offset+=source->domains[d].bytes;
    }
}

static void r1_owned_origin(r1_state* state)
{
    if (state->origin_mode!=1) return;
    /* Exact owned_engine(seed)→seek(0): clear consumption and causal entries;
     * causal revision starts from fresh PE zero, not the live owner's revision.
     * filter state is NOT among copied ENGINE_INPUTS; fresh PE omits the attr.
     * isolated_rule_history is the canonical {key:None,t:None,vals:{}}. */
    for (int d=0;d<3;d++) {state->domains[d].kind=3;state->domains[d].bytes=0;}
    state->domains[3].kind=0; state->domains[3].bytes=0;
    state->domains[7].kind=4; state->domains[7].bytes=0;
    state->domains[8].kind=3; state->domains[8].bytes=0;
    state->causal_revision=1; state->taken_count=0;
}

static int r1_state_create(r1_job* job, const r1_raw_state* source)
{
    uint64_t bytes;
    int status;
    if (!source) return R1_OK; /* explicit old draft output-only, pending all 9 */
    if (source->abi!=R1_ABI || source->struct_size!=sizeof(*source)) return R1_INVALID;
    if (source->taken_count>SIZE_MAX/sizeof(r1_taken)) return R1_INVALID;
    bytes=r1_state_bytes(source,job->ledger->limit-job->ledger->used);
    if (!bytes) return R1_BUDGET;
    status=r1_state_shape(source);
    if (status) return status;
    job->state=r1_allocate(job->ledger,bytes);
    if (!job->state) return R1_BUDGET;
    r1_copy_state(job->state,source,bytes);
    r1_owned_origin(job->state);
    return R1_OK;
}

static void r1_state_before_frame(r1_job* job)
{
    if (!job->state) return;
    /* Original no-LT gap clears state/time, but retains color and every other
     * input domain. Offline range sets previous time=None at entry, so this
     * chronological range does not perform rewind/causal revision increments. */
    for (int d=4;d<=5;d++) {job->state->domains[d].kind=3;job->state->domains[d].bytes=0;}
}

static int r1_takeover_active(const r1_job* job, uint32_t universe, uint8_t* active)
{
    int count=0;
    if (job->state) {
        const r1_taken* rows=(const r1_taken*)((uint8_t*)job->state+job->state->taken_offset);
        for (uint64_t i=0;i<job->state->taken_count;i++) {
            if (rows[i].source_token==job->source_token && rows[i].universe==universe)
                ce_domain_mask(3,512,active,rows[i].mask,active,&count);
        }
    }
    count=0;
    for (int c=0;c<512;c++) count+=active[c]!=0;
    return count;
}
