/* Original endpoint score + shared ce_prepared_anchor_update. Main CE source
 * composition supplies the fixed r6_transition_sample implementation. */
extern int ce_prepared_anchor_update(const float*,const uint8_t*,const double*,double,float*,double*);

static uint64_t rfi_endpoint_index(const r1_job* source,const r1_stream* stream,double time)
{
    uint64_t left=0,right=stream->count;const double* times=source->times+stream->begin;
    while (left<right) {uint64_t middle=left+(right-left)/2;
        if (times[middle]<time) left=middle+1;else right=middle;}
    if (!left) return 0;if (left==stream->count) return left-1;
    return time-times[left-1]<times[left]-time ? left-1 : left;
}

static void rfi_endpoint_scores(const rfi_plan* plan,const rfi_reference* ref,
                                const uint8_t* active,double* scores)
{
    const r1_job* source=plan->owner.timeline->base->scene->clips[ref->clip].source;
    float changes[512];int found=ref->slot!=UINT32_MAX && ref->slot<source->streams_n
        && source->streams[ref->slot].count!=0;
    if (found) {
        const r1_stream* stream=source->streams+ref->slot;
        double endpoint=rfi_max_first(0.,source->source_duration-1e-4);
        uint64_t row=rfi_endpoint_index(source,stream,endpoint);
        r1_sparse_lookup(source,stream,row,changes);
    }
    double rate=source->rate<=1e-9 ? 1. : source->rate;
    int hold=plan->owner.timeline->clips[ref->clip].hold!=R5_HOLD_NONE;
    double cap=source->start+rfi_max_first(0.,source->duration);
    for (uint32_t c=0;c<512;c++) {
        double score=source->start;
        if (found) score=source->start+((double)changes[c]/rate);
        /* np.minimum propagates NaN; ordered scalar min does not. */
        if (hold && !isnan(score)) score=isnan(cap) ? cap : score<cap ? score : cap;
        scores[c]=active[c] ? score : -INFINITY;
    }
}

static int rfi_dependency_add(rfi_plan* plan,rfi_entry* entry,uint64_t token)
{
    for (uint64_t i=0;i<entry->deps_count;i++)
        if (plan->deps[entry->dep_first+i]==token) return R1_OK;
    if (entry->deps_count>entry->count) return R1_INVALID;
    plan->deps[entry->dep_first+entry->deps_count++]=token;return R1_OK;
}

static int rfi_anchor_step(rfi_plan* plan,uint64_t index,uint32_t units,const float** output)
{
    if (!plan || !output || index>=plan->entries_n || !units || units>65536
        || plan->phase!=RFI_READY) return R1_INVALID;
    *output=NULL;if (plan->cancelled) return R1_CANCELLED;if (plan->status) return plan->status;
    r3_job* scene=plan->owner.timeline->base->scene;rfi_entry* entry=plan->entries+index;
    if (entry->ready) {*output=entry->anchor;return R1_OK;}
    if (!entry->building) {
        if (plan->entry_cursor!=UINT64_MAX) return R1_INVALID;
        uint32_t slot=scene->universe_map[entry->universe];
        if (!slot || slot>scene->clock.streams_n) return R1_PENDING;
        memcpy(entry->anchor,scene->idle+(uint64_t)(slot-1)*512,512*sizeof(float));
        for (uint32_t c=0;c<512;c++) entry->best[c]=-INFINITY;
        entry->deps_count=0;entry->building=1;plan->entry_cursor=index;plan->ref_cursor=0;
    }
    if (plan->entry_cursor!=index) return R1_INVALID;
    while (units-- && plan->ref_cursor<entry->count) {
        const rfi_reference* ref=plan->refs+entry->first+plan->ref_cursor;
        r6_transition_request request={ref->clip,scene->clips[entry->target].source->start,entry->universe,1};
        memset(&plan->sample,0,sizeof(plan->sample));
        int status=r6_transition_sample(&plan->owner,&request,&plan->sample);
        if (status) {plan->status=status;return status;}
        plan->ref_cursor++;
        if (plan->sample.present>1 || plan->sample.reserved) {plan->status=R1_KERNEL;return R1_KERNEL;}
        if (!plan->sample.present) continue;
        int any=0;for (uint32_t c=0;c<512;c++) any|=plan->sample.active[c]!=0;
        if (!any) continue;
        status=rfi_dependency_add(plan,entry,scene->clips[ref->clip].source->source_token);
        if (status) {plan->status=status;return status;}
        double scores[512];rfi_endpoint_scores(plan,ref,plan->sample.active,scores);
        double tie=scene->clips[ref->clip].source->start*1000000.
            +((double)ref->track_rank+1.)*.001;
        if (ce_prepared_anchor_update(plan->sample.data,plan->sample.active,scores,tie,entry->anchor,entry->best)<0) {
            plan->status=R1_KERNEL;return R1_KERNEL;
        }
    }
    if (plan->ref_cursor<entry->count) return R1_BUILDING;
    int status=rfi_dependency_add(plan,entry,scene->clips[entry->target].source->source_token);
    if (status) {plan->status=status;return status;}
    if (entry->causal) {
        if (!scene->clock.state || scene->clock.state->causal_revision!=plan->revision
            || plan->revision==UINT64_MAX) {plan->status=R1_PENDING;return R1_PENDING;}
        scene->clock.state->causal_revision=++plan->revision;
    }
    entry->revision=plan->revision;entry->ready=1;entry->building=0;plan->entry_cursor=UINT64_MAX;
    *output=entry->anchor;return R1_OK;
}

static int rfi_compile_finish(rfi_plan* plan)
{
    if (!plan || plan->phase!=RFI_PREWARM) return R1_INVALID;
    /* No numerical sample or LT/audio/filter history mutation at compile.
     * Even a nonmotor sample may clear a rate state or update audio/LT. */
    plan->entry_cursor=UINT64_MAX;plan->phase=RFI_READY;return R1_OK;
}

static int rfi_at_original_prewarm(rfi_plan* plan,uint64_t index,uint32_t units,const float** output)
{
    if (!plan || !output || index>=plan->entries_n || plan->phase!=RFI_READY) return R1_INVALID;
    *output=NULL;
    /* Invoked only at an actual original prewarm event, in its exact order.
     * The original normal getter consumer instead calls anchor_step always. */
    if (plan->entries[index].causal) return R1_OK;
    return rfi_anchor_step(plan,index,units,output);
}
