/* All raw numeric/membership/candidate math is internal C work. Include after
 * R4 track compiler and R5 atom parser; input owners stay pinned until READY. */
static double rfi_min_first(double a,double b) {return b<a ? b : a;}
static double rfi_max_first(double a,double b) {return b>a ? b : a;}

static int rfi_member(const rfi_universes_raw* list,uint32_t universe,uint32_t* semantic)
{
    int found=0;
    if (list->count && !list->values) return R1_INVALID;
    for (uint64_t i=0;i<list->count;i++) {
        const r1_atom atom=list->values[i];double value;int status;
        if (atom.reserved || atom.kind==R1_MISSING || (atom.kind==R1_BOOL && atom.bits>1)) return R1_PENDING;
        if (atom.kind==R1_NONE) {*semantic=RFI_UNIVERSE_INT_NONE;return R1_PENDING;}
        status=r5_number(atom,1.,&value);if (status) return status;
        if (isnan(value)) {*semantic=RFI_UNIVERSE_INT_NAN;return R1_PENDING;}
        if (isinf(value)) {*semantic=RFI_UNIVERSE_INT_INF;return R1_PENDING;}
        /* Python int(huge finite f64) need not fit int64 to compare with an
         * actual positive output universe. No overflowing float-to-int cast. */
        found|=trunc(value)==(double)universe;
    }
    return found; /* All int conversions precede the original membership test. */
}

static int rfi_filter_primitives(const rfi_clip_raw* raw)
{
    for (uint32_t i=0;i<3;i++) if (raw->filters[i].kind!=R1_MISSING
        && raw->filters[i].kind!=R1_F64 && raw->filters[i].kind!=R1_I64
        && raw->filters[i].kind!=R1_BOOL) return 0;
    return 1;
}

static int rfi_universe_allowed(rfi_plan* plan,uint64_t clip,uint32_t universe)
{
    const rfi_clip_raw* raw=plan->borrowed->clips+clip;
    const rfi_track_raw* track=plan->borrowed->tracks+raw->containing_track;
    if (raw->universes.count) return rfi_member(&raw->universes,universe,&plan->semantic);
    /* Original track-universe conversion catches all conversion failures and
     * substitutes [requested_universe]. Unsupported foreign protocols remain
     * PENDING; genuine builtin None/NaN/Inf use the original caught branch. */
    if (track->universes.count) {
        uint32_t semantic=0;int result=rfi_member(&track->universes,universe,&semantic);
        if (semantic) return 1;
        if (result==R1_PENDING) {
            for (uint64_t i=0;i<track->universes.count;i++)
                if (track->universes.values[i].kind!=R1_NONE
                    && track->universes.values[i].kind!=R1_MISSING
                    && track->universes.values[i].kind!=R1_F64
                    && track->universes.values[i].kind!=R1_I64
                    && track->universes.values[i].kind!=R1_BOOL) return R1_PENDING;
            return 1;
        }
        return result;
    }
    rfi_universes_raw single={1,&track->universe};uint32_t semantic=0;
    if (track->universe.kind==R1_MISSING) return universe==1;
    int result=rfi_member(&single,universe,&semantic);
    return semantic || (result==R1_PENDING && track->universe.kind==R1_NONE) ? 1 : result;
}

static void rfi_destroy(rfi_plan* plan)
{
    if (!plan || !plan->ledger) return;
    r1_free(plan->ledger,plan->entries,plan->entries_bytes);
    r1_free(plan->ledger,plan->refs,plan->refs_bytes);
    r1_free(plan->ledger,plan->deps,plan->deps_bytes);
    r1_free(plan->ledger,plan->filters,plan->filters_bytes);
    memset(plan,0,sizeof(*plan));
}

static int rfi_begin(rfi_plan* plan,const r6_transition_owner* owner,const rfi_input* input,
                     const rfi_target* native_targets,uint64_t targets)
{
    r3_job* scene;uint64_t bytes=0;
    if (!plan || !owner || !owner->timeline || !owner->timeline->base || !input
        || input->abi!=RFI_ABI || input->size!=sizeof(*input) || input->reserved
        || (targets && !native_targets)) return R1_INVALID;
    scene=owner->timeline->base->scene;
    if (input->history_kind>1) return R1_PENDING;
    if ((input->history_kind==0 && input->history) || (input->history_kind==1 && !input->history)) return R1_INVALID;
    if (input->source_epoch!=owner->source_epoch || !input->context_owner
        || input->clip_count!=scene->clips_n || input->track_count!=scene->tracks_n
        || (scene->clips_n && !input->clips) || (scene->tracks_n && !input->tracks)
        || owner->timeline->base->tracks.phase!=R4_TRACK_READY) return R1_PENDING;
    /* Checked raw lower bound BEFORE following nested atoms/target rows. It
     * adds to the existing R5 external charge, never pretends raw arrays free. */
    bytes=owner->timeline->external_min;
    if (!r3_add_bytes(&bytes,1,sizeof(*input))
        || !r3_add_bytes(&bytes,scene->clips_n,sizeof(rfi_clip_raw))
        || !r3_add_bytes(&bytes,scene->tracks_n,sizeof(rfi_track_raw))
        || bytes>scene->external_bytes) return R1_BUDGET;
    memset(plan,0,sizeof(*plan));plan->owner=*owner;plan->borrowed=input;
    plan->targets=native_targets;plan->entries_n=targets;plan->ledger=scene->clock.ledger;
    plan->tracks=&owner->timeline->base->tracks;plan->source_epoch=input->source_epoch;
    plan->context_owner=input->context_owner;plan->external_min=bytes;
    plan->revision=scene->clock.state ? scene->clock.state->causal_revision : 0;
    if (!r3_add_bytes(&plan->entries_bytes,targets,sizeof(rfi_entry))
        || !r3_add_bytes(&plan->filters_bytes,scene->clips_n,3*sizeof(double))) return R1_BUDGET;
    if (targets) plan->entries=r1_allocate(plan->ledger,plan->entries_bytes);
    if (scene->clips_n) plan->filters=r1_allocate(plan->ledger,plan->filters_bytes);
    if ((targets && !plan->entries) || (scene->clips_n && !plan->filters)) {rfi_destroy(plan);return R1_BUDGET;}
    plan->phase=1;return R1_BUILDING;
}

static int rfi_metadata_one(rfi_plan* plan)
{
    r3_job* scene=plan->owner.timeline->base->scene;
    if (plan->clip_cursor<scene->clips_n) {
        uint64_t i=plan->clip_cursor++;const rfi_clip_raw* raw=plan->borrowed->clips+i;
        if (raw->containing_track>=scene->tracks_n || !raw->object_token
            || raw->containing_track!=scene->clips[i].track_index) return R1_PENDING;
        if (!r3_add_bytes(&plan->external_min,raw->universes.count,sizeof(r1_atom))
            || plan->external_min>scene->external_bytes) return R1_BUDGET;
        if (raw->universes.count && !raw->universes.values) return R1_INVALID;
        for (uint32_t k=0;k<3;k++) {
            double value,fallback=k==1 ? 0. : 1.;r1_atom atom=raw->filters[k];
            if (atom.reserved || (atom.kind==R1_BOOL && atom.bits>1)) return R1_PENDING;
            int status=atom.kind==R1_NONE ? (value=fallback,R1_OK) : r5_number(atom,fallback,&value);
            if (status) return status;
            plan->filters[i*3+k]=k==1 ? rfi_max_first(0.,value)
                : rfi_max_first(0.,rfi_min_first(1.,value));
        }
        return R1_OK;
    }
    if (plan->track_cursor<scene->tracks_n) {
        const rfi_track_raw* raw=plan->borrowed->tracks+plan->track_cursor++;
        if (!r3_add_bytes(&plan->external_min,raw->universes.count,sizeof(r1_atom))
            || plan->external_min>scene->external_bytes) return R1_BUDGET;
        if (raw->universes.count && !raw->universes.values) return R1_INVALID;
        return R1_OK;
    }
    for (uint64_t i=0;i<plan->tracks->count;i++) plan->eligible_tracks+=plan->tracks->allowed[i]!=0;
    plan->clip_cursor=plan->track_cursor=plan->entry_cursor=0;plan->phase=2;return R1_OK;
}

static int rfi_candidate(rfi_plan* plan,uint64_t previous,uint64_t track,uint64_t rank,rfi_reference* ref)
{
    r3_job* scene=plan->owner.timeline->base->scene;
    const rfi_target* target=plan->targets+plan->entry_cursor;
    const rfi_clip_raw* raw=plan->borrowed->clips+previous;
    if (raw->containing_track!=track || raw->object_token==plan->borrowed->clips[target->clip].object_token) return 0;
    const r1_job *source=scene->clips[previous].source,*current=scene->clips[target->clip].source;
    double start=source->start,duration=source->duration,end=start+duration,t=current->start;
    if (duration<=0. || start>=t-1e-9) return 0;
    int overlap=start<=t && t<=end+1e-6;
    int hold=plan->owner.timeline->clips[previous].hold!=R5_HOLD_NONE && t-end>=-1e-6;
    if (!overlap && !hold) return 0;
    int result=rfi_universe_allowed(plan,previous,target->universe);
    if (result!=1) return result;
    uint32_t slot=UINT32_MAX;
    for (uint64_t i=0;i<source->streams_n;i++) if (source->streams[i].universe==target->universe) {
        if (i>UINT32_MAX) return R1_BUDGET;slot=(uint32_t)i;break;
    }
    *ref=(rfi_reference){previous,rank,slot,0};return 1;
}

static int rfi_scan_one(rfi_plan* plan,int fill)
{
    r3_job* scene=plan->owner.timeline->base->scene;
    if (plan->entry_cursor==plan->entries_n) {plan->phase=fill ? 5 : 3;return R1_OK;}
    const rfi_target* target=plan->targets+plan->entry_cursor;
    if (!plan->track_cursor && !plan->clip_cursor) {
        if (target->reserved || target->clip>=scene->clips_n || target->universe<1
            || target->universe>=R1_UNIVERSE_LIMIT || !scene->universe_map[target->universe]) return R1_PENDING;
        if (fill) {
            rfi_entry* entry=plan->entries+plan->entry_cursor;memset(entry,0,sizeof(*entry));
            entry->target=target->clip;entry->universe=target->universe;
            entry->first=plan->ref_cursor;entry->dep_first=plan->ref_cursor+plan->entry_cursor;
            entry->ordinary=rfi_filter_primitives(plan->borrowed->clips+target->clip);
        }
    }
    uint64_t tracks=plan->eligible_tracks ? plan->tracks->out : 1;
    if (plan->track_cursor==tracks) {
        if (fill) plan->entries[plan->entry_cursor].causal&=plan->entries[plan->entry_cursor].ordinary;
        plan->entry_cursor++;plan->track_cursor=plan->clip_cursor=0;return R1_OK;
    }
    uint64_t rank=plan->eligible_tracks ? plan->track_cursor : 0;
    uint64_t track=plan->eligible_tracks ? (uint64_t)plan->tracks->order[rank]
        : plan->borrowed->clips[target->clip].containing_track;
    if (plan->eligible_tracks && !plan->tracks->allowed[track]) {
        plan->track_cursor++;plan->clip_cursor=0;return R1_OK;
    }
    if (plan->clip_cursor==scene->clips_n) {plan->clip_cursor=0;plan->track_cursor++;return R1_OK;}
    rfi_reference ref;int result=rfi_candidate(plan,plan->clip_cursor++,track,rank,&ref);
    if (result<0) return result;
    if (!result) return R1_OK;
    if (fill) {
        if (plan->ref_cursor>=plan->refs_n) return R1_INVALID;
        plan->refs[plan->ref_cursor++]=ref;rfi_entry* entry=plan->entries+plan->entry_cursor;
        entry->count++;entry->causal|=plan->filters[ref.clip*3+1]>1e-6;
        entry->ordinary&=rfi_filter_primitives(plan->borrowed->clips+ref.clip);
    } else {
        uint64_t count=plan->refs_n,bytes=0;
        if (count==UINT64_MAX) return R1_BUDGET;count++;
        if (!r3_add_bytes(&bytes,count,sizeof(rfi_reference)+sizeof(uint64_t))
            || !r3_add_bytes(&bytes,plan->entries_n,sizeof(uint64_t))
            || bytes>plan->ledger->limit-plan->ledger->used) return R1_BUDGET;
        plan->refs_n=count;
    }
    return R1_OK;
}

static int rfi_allocate_refs(rfi_plan* plan)
{
    uint64_t deps=plan->refs_n;
    if (deps>UINT64_MAX-plan->entries_n) return R1_BUDGET;deps+=plan->entries_n;
    if (!r3_add_bytes(&plan->refs_bytes,plan->refs_n,sizeof(rfi_reference))
        || !r3_add_bytes(&plan->deps_bytes,deps,sizeof(uint64_t))) return R1_BUDGET;
    if (plan->refs_n) plan->refs=r1_allocate(plan->ledger,plan->refs_bytes);
    if (deps) plan->deps=r1_allocate(plan->ledger,plan->deps_bytes);
    if ((plan->refs_n && !plan->refs) || (deps && !plan->deps)) return R1_BUDGET;
    plan->entry_cursor=plan->track_cursor=plan->clip_cursor=plan->ref_cursor=0;plan->phase=4;
    return R1_OK;
}
