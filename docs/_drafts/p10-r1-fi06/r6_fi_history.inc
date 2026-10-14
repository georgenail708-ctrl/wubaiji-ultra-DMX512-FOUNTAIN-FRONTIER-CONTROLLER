/* Owning sidecar: anchor values/dependencies/filter context are retained with
 * the actual original causal revision. It does not overwrite nine R1 domains. */
#define RFI_HISTORY_MAGIC UINT64_C(0x5246493036484953)

static int rfi_history_shape(const rfi_plan* plan,const rfi_history* history)
{
    uint64_t bytes=sizeof(*history);r3_job* scene=plan->owner.timeline->base->scene;
    if (!history || history->abi!=RFI_ABI || history->size!=sizeof(*history)
        || history->magic!=RFI_HISTORY_MAGIC || !history->ledger
        || history->source_epoch!=plan->source_epoch || history->context_owner!=plan->context_owner
        || history->revision>plan->revision || history->clips!=scene->clips_n
        || history->entries>history->entry_capacity || history->dependencies>history->dependency_capacity
        || history->entry_capacity!=plan->entries_n
        || history->dependency_capacity!=plan->refs_n+plan->entries_n
        || (history->entry_capacity && !history->rows) || (history->dependency_capacity && !history->deps)
        || (history->clips && (!history->filter_tokens || !history->filters))) return R1_PENDING;
    if (!r3_add_bytes(&bytes,history->entry_capacity,sizeof(rfi_history_entry))
        || !r3_add_bytes(&bytes,history->dependency_capacity,sizeof(uint64_t))
        || !r3_add_bytes(&bytes,history->clips,sizeof(uint64_t)+3*sizeof(double))
        || bytes!=history->bytes) return R1_INVALID;
    return R1_OK;
}

static int rfi_seed_one(rfi_plan* plan)
{
    r3_job* scene=plan->owner.timeline->base->scene;const rfi_history* history=plan->borrowed->history;
    if (!history) {plan->entry_cursor=UINT64_MAX;plan->history_cursor=0;plan->phase=RFI_PREWARM;return R1_OK;}
    if (!plan->history_cursor && !plan->copied_entries && !plan->copied_deps) {
        int status=rfi_history_shape(plan,history);if (status) return status;
    }
    if (plan->copied_entries<scene->clips_n) {
        uint64_t i=plan->copied_entries++;
        if (history->filter_tokens[i]!=scene->clips[i].source->source_token
            || memcmp(history->filters+i*3,plan->filters+i*3,3*sizeof(double))) return R1_PENDING;
        return R1_OK;
    }
    if (plan->history_cursor==history->entries) {
        plan->entry_cursor=UINT64_MAX;plan->history_cursor=0;plan->phase=RFI_PREWARM;return R1_OK;
    }
    const rfi_history_entry* row=history->rows+plan->history_cursor;
    if (row->target>=scene->clips_n || row->source_token!=scene->clips[row->target].source->source_token
        || row->causal>1 || row->revision>plan->revision
        || row->dep_first>history->dependencies || row->dep_count>history->dependencies-row->dep_first)
        return R1_PENDING;
    if (plan->copied_deps==0) {
        uint64_t found=UINT64_MAX;
        for (uint64_t i=0;i<plan->entries_n;i++) if (plan->entries[i].target==row->target
            && plan->entries[i].universe==row->universe) {found=i;break;}
        if (found==UINT64_MAX || plan->entries[found].ready
            || plan->entries[found].causal!=row->causal || row->dep_count>plan->entries[found].count+1)
            return R1_PENDING;
        plan->entry_cursor=found;
        memcpy(plan->entries[found].anchor,row->anchor,512*sizeof(float));
    }
    rfi_entry* entry=plan->entries+plan->entry_cursor;
    if (plan->copied_deps<row->dep_count) {
        uint64_t token=history->deps[row->dep_first+plan->copied_deps++];
        int found=token==row->source_token;
        for (uint64_t i=0;i<entry->count;i++) if (token==scene->clips[plan->refs[entry->first+i].clip].source->source_token) found=1;
        if (!found) return R1_PENDING;
        return rfi_dependency_add(plan,entry,token);
    }
    int have_target=0;for (uint64_t i=0;i<entry->deps_count;i++)
        have_target|=plan->deps[entry->dep_first+i]==row->source_token;
    if (!have_target) return R1_PENDING;
    entry->ready=1;entry->revision=row->revision;
    plan->history_cursor++;plan->copied_deps=0;return R1_OK;
}

static int rfi_compile_step(rfi_plan* plan,uint32_t units)
{
    if (!plan || !units || units>65536 || !plan->ledger) return R1_INVALID;
    if (plan->cancelled) return R1_CANCELLED;if (plan->status) return plan->status;
    int status=R1_OK;
    while (units-- && plan->phase!=RFI_READY && !status) switch (plan->phase) {
    case RFI_METADATA:status=rfi_metadata_one(plan);break;
    case RFI_COUNT:status=rfi_scan_one(plan,0);break;
    case RFI_ALLOCATE:status=rfi_allocate_refs(plan);break;
    case RFI_FILL:status=rfi_scan_one(plan,1);break;
    case RFI_SEED:status=rfi_seed_one(plan);break;
    case RFI_PREWARM:status=rfi_compile_finish(plan);break;
    default:status=R1_INVALID;break;
    }
    if (status!=R1_OK) {plan->status=status;return status;}
    return plan->phase==RFI_READY ? R1_OK : R1_BUILDING;
}

static int rfi_history_precharge(const rfi_plan* plan,rfi_history** output)
{
    r3_job* scene;uint64_t rows,deps,bytes=sizeof(rfi_history);
    if (!plan || !output || plan->phase!=RFI_READY || plan->status || plan->entry_cursor!=UINT64_MAX) return R1_INVALID;
    *output=NULL;scene=plan->owner.timeline->base->scene;
    rows=plan->entries_n;deps=plan->refs_n;
    if (deps>UINT64_MAX-rows) return R1_BUDGET;deps+=rows;
    if (!r3_add_bytes(&bytes,rows,sizeof(rfi_history_entry))
        || !r3_add_bytes(&bytes,deps,sizeof(uint64_t))
        || !r3_add_bytes(&bytes,scene->clips_n,sizeof(uint64_t)+3*sizeof(double))) return R1_BUDGET;
    rfi_history* history=r1_allocate(plan->ledger,bytes);if (!history) return R1_BUDGET;
    memset(history,0,sizeof(*history));history->abi=RFI_ABI;history->size=sizeof(*history);
    history->magic=RFI_HISTORY_MAGIC;history->bytes=bytes;history->ledger=plan->ledger;
    history->source_epoch=plan->source_epoch;history->context_owner=plan->context_owner;history->revision=plan->revision;
    history->entry_capacity=rows;history->dependency_capacity=deps;history->clips=scene->clips_n;
    history->rows=(rfi_history_entry*)(history+1);history->deps=(uint64_t*)(history->rows+rows);
    history->filter_tokens=history->deps+deps;history->filters=(double*)(history->filter_tokens+scene->clips_n);
    for (uint64_t i=0;i<scene->clips_n;i++) history->filter_tokens[i]=scene->clips[i].source->source_token;
    if (plan->filters_bytes) memcpy(history->filters,plan->filters,plan->filters_bytes);
    if (plan->ledger->refs==UINT64_MAX) {r1_free(plan->ledger,history,bytes);return R1_BUDGET;}
    plan->ledger->refs++;*output=history;return R1_OK;
}

static int rfi_history_refresh(const rfi_plan* plan,rfi_history* history)
{
    if (!plan || !history || plan->phase!=RFI_READY || plan->status
        || plan->entry_cursor!=UINT64_MAX || history->ledger!=plan->ledger) return R1_INVALID;
    int status=rfi_history_shape(plan,history);if (status) return status;
    r3_job* scene=plan->owner.timeline->base->scene;uint64_t row=0,dep=0;
    for (uint64_t i=0;i<plan->entries_n;i++) if (plan->entries[i].ready) {
        const rfi_entry* entry=plan->entries+i;
        if (row>=history->entry_capacity || entry->deps_count>history->dependency_capacity-dep) return R1_INVALID;
        rfi_history_entry* target=history->rows+row++;
        target->target=entry->target;target->source_token=scene->clips[entry->target].source->source_token;
        target->universe=entry->universe;target->causal=entry->causal;target->revision=entry->revision;
        target->dep_first=dep;target->dep_count=entry->deps_count;
        memcpy(target->anchor,entry->anchor,512*sizeof(float));
        if (entry->deps_count) memcpy(history->deps+dep,plan->deps+entry->dep_first,entry->deps_count*sizeof(uint64_t));
        dep+=entry->deps_count;
    }
    history->entries=row;history->dependencies=dep;history->revision=plan->revision;return R1_OK;
}

static int rfi_history_release(rfi_history* history)
{
    if (!history || history->magic!=RFI_HISTORY_MAGIC || !history->ledger || !history->ledger->refs) return R1_INVALID;
    r1_ledger* ledger=history->ledger;uint64_t bytes=history->bytes;
    history->magic=0;r1_free(ledger,history,bytes);r1_ledger_release(ledger);return R1_OK;
}
