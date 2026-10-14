/* Genuine original-map import. A context mismatch is NOT an empty map: raw
 * original payload and row order stay owned until the getter replaces a key. */
static void rfi_legacy_destroy(rfi_legacy_owner* owner)
{
    if (!owner || !owner->ledger) return;
    r1_free(owner->ledger,owner->table.rows,owner->bytes);memset(owner,0,sizeof(*owner));
}

static int rfi_same_forest(const rfi_tree_view* a,const rfi_tree_view* b)
{
    return a->identity_space==b->identity_space && a->nodes_n==b->nodes_n && a->edges_n==b->edges_n
        && a->data_n==b->data_n && a->nodes==b->nodes && a->edges==b->edges && a->data==b->data;
}

static int rfi_legacy_import(rfi_legacy_owner* out,r1_ledger* ledger,const rfi_legacy_table* raw,
                            uint64_t actual_current_nodes)
{
    uint64_t bytes=0,deps=0,frames=0;const rfi_tree_view* forest=NULL;
    if (!out || !ledger || !raw || actual_current_nodes==UINT64_MAX || (raw->rows_n && !raw->rows)
        || (raw->payload_bytes && !raw->payload)) return R1_INVALID;
    if (!r3_add_bytes(&bytes,raw->rows_n,sizeof(rfi_legacy_row)+512*sizeof(float)+1)
        || !r3_add_bytes(&bytes,raw->payload_bytes,1) || bytes>ledger->limit-ledger->used) return R1_BUDGET;
    /* Inspect fixed row descriptors only after admitting their lower bound;
     * nested arrays are not followed before their own complete charge. */
    for (uint64_t i=0;i<raw->rows_n;i++) {
        const rfi_legacy_row* row=raw->rows+i;
        if (row->reserved || row->geometry_reserved || row->have_proof>1 || !row->anchor
            || (row->deps_n && !row->deps) || row->universe<1 || row->universe>=R1_UNIVERSE_LIMIT) return R1_PENDING;
        if (deps>UINT64_MAX-row->deps_n) return R1_BUDGET;deps+=row->deps_n;
        if (!forest) forest=&row->context;
        for (uint32_t j=0;j<5;j++) {
            const rfi_tree_view* view=j==0 ? &row->context : j==1 ? &row->motor_context
                : j==2 ? &row->proof_schema : j==3 ? &row->proof_slot : &row->proof_values;
            if (!rfi_same_forest(forest,view) || !view->nodes || view->root>=view->nodes_n) return R1_PENDING;
        }
    }
    if (forest) {
        if (!r3_add_bytes(&bytes,forest->nodes_n,sizeof(rfi_tree_node))
            || !r3_add_bytes(&bytes,forest->edges_n,sizeof(uint64_t))
            || !r3_add_bytes(&bytes,forest->data_n,1)) return R1_BUDGET;
        if (forest->nodes_n>UINT64_MAX-actual_current_nodes-1) return R1_BUDGET;
        frames=forest->nodes_n+actual_current_nodes+1;
    } else {if (actual_current_nodes==UINT64_MAX) return R1_BUDGET;frames=actual_current_nodes+1;}
    if (!r3_add_bytes(&bytes,deps,sizeof(uint64_t))
        || !r3_add_bytes(&bytes,frames,sizeof(rfi_eq_frame)) || bytes>ledger->limit-ledger->used) return R1_BUDGET;
    /* Nested root-node access follows the complete forest/work lower bound. */
    for (uint64_t i=0;i<raw->rows_n;i++) {
        const rfi_legacy_row* row=raw->rows+i;
        const rfi_tree_view* views[5]={&row->context,&row->motor_context,&row->proof_schema,&row->proof_slot,&row->proof_values};
        for (uint32_t j=0;j<5;j++) if (!rfi_node(views[j],views[j]->root)) return R1_PENDING;
    }
    memset(out,0,sizeof(*out));uint8_t* allocation=r1_allocate(ledger,bytes);if (!allocation) return R1_BUDGET;
    out->ledger=ledger;out->bytes=bytes;out->table=*raw;
    rfi_legacy_row* rows=(rfi_legacy_row*)allocation;out->table.rows=rows;
    uint8_t* cursor=allocation+raw->rows_n*sizeof(rfi_legacy_row);
    float* anchors=(float*)cursor;cursor+=raw->rows_n*512*sizeof(float);
    /* Keep following 64-bit arrays aligned. Flags are last, not before them. */
    uint64_t* dependencies=(uint64_t*)cursor;cursor+=deps*sizeof(uint64_t);
    rfi_tree_node* nodes=(rfi_tree_node*)cursor;uint64_t* edges;uint8_t* data;
    if (forest) {
        cursor+=forest->nodes_n*sizeof(rfi_tree_node);edges=(uint64_t*)cursor;
        cursor+=forest->edges_n*sizeof(uint64_t);
        out->table.work.frames=(rfi_eq_frame*)cursor;cursor+=frames*sizeof(rfi_eq_frame);
        out->table.work.capacity=frames;data=cursor;cursor+=forest->data_n;
        memcpy(nodes,forest->nodes,forest->nodes_n*sizeof(rfi_tree_node));
        if (forest->edges_n) memcpy(edges,forest->edges,forest->edges_n*sizeof(uint64_t));
        if (forest->data_n) memcpy(data,forest->data,forest->data_n);
    } else {edges=NULL;data=NULL;out->table.work.frames=(rfi_eq_frame*)cursor;
        cursor+=frames*sizeof(rfi_eq_frame);out->table.work.capacity=frames;}
    /* Only byte payload/flags follow possibly odd tree-data lengths. All
     * 64-bit arrays/workspace above remain aligned within the owning block. */
    uint8_t* payload=cursor;cursor+=raw->payload_bytes;out->table.payload=payload;
    if (raw->payload_bytes) memcpy(payload,raw->payload,(size_t)raw->payload_bytes);
    out->changed=cursor;cursor+=raw->rows_n;
    uint64_t dep=0;
    for (uint64_t i=0;i<raw->rows_n;i++) {
        rows[i]=raw->rows[i];rows[i].anchor=anchors+i*512;
        memcpy(anchors+i*512,raw->rows[i].anchor,512*sizeof(float));
        rows[i].deps=dependencies+dep;
        if (rows[i].deps_n) memcpy(dependencies+dep,raw->rows[i].deps,rows[i].deps_n*sizeof(uint64_t));dep+=rows[i].deps_n;
        rfi_tree_view* views[5]={&rows[i].context,&rows[i].motor_context,&rows[i].proof_schema,&rows[i].proof_slot,&rows[i].proof_values};
        for (uint32_t j=0;j<5;j++) {views[j]->nodes=nodes;views[j]->edges=edges;views[j]->data=data;}
    }
    if ((uint64_t)(cursor-allocation)!=bytes) {rfi_legacy_destroy(out);return R1_INVALID;}
    return R1_OK;
}

static int rfi_getter_from_original(rfi_plan* plan,uint64_t entry_index,rfi_legacy_owner* legacy,
                                   uint32_t units,const float** output)
{
    if (!plan || !legacy || !output || entry_index>=plan->entries_n || !units || plan->phase!=RFI_READY) return R1_INVALID;
    *output=NULL;rfi_entry* entry=plan->entries+entry_index;
    if (entry->ready || !entry->causal || entry->building) return rfi_anchor_step(plan,entry_index,units,output);
    r3_job* scene=plan->owner.timeline->base->scene;uint64_t token=scene->clips[entry->target].source->source_token;
    rfi_current_context current={0};int status=r6_fi_current_context(&plan->owner,entry->target,entry->universe,&current);
    if (status) return status;
    if (!current.context.nodes || !current.motor_context.nodes || current.reserved) return R1_PENDING;
    /* This capacity must already be precharged by the producer before frame0.
     * The getter never allocates workspace or treats insufficient capacity EQ. */
    uint64_t need=current.context.nodes_n;
    if (current.motor_context.nodes_n>need) need=current.motor_context.nodes_n;
    if (current.have_permit && current.permit.nodes_n>need) need=current.permit.nodes_n;
    uint64_t old_nodes=legacy->table.rows_n ? legacy->table.rows[0].context.nodes_n : 0;
    if (need==UINT64_MAX || old_nodes>UINT64_MAX-need-1 || !legacy->table.work.frames
        || legacy->table.work.capacity<old_nodes+need+1) return R1_BUDGET;
    for (uint64_t i=0;i<legacy->table.rows_n;i++) {
        const rfi_legacy_row* row=legacy->table.rows+i;
        if (row->source_token!=token || row->universe!=entry->universe || legacy->changed[i]) continue;
        int matched=0;status=rfi_legacy_matches(row,&current,&legacy->table.work,&matched);
        if (status) return status;
        if (matched) {
            if (row->deps_n>entry->count+1) return R1_PENDING;
            memcpy(entry->anchor,row->anchor,512*sizeof(float));entry->deps_count=0;
            for (uint64_t k=0;k<row->deps_n;k++) {status=rfi_dependency_add(plan,entry,row->deps[k]);if (status) return status;}
            entry->ready=1;entry->revision=plan->revision;*output=entry->anchor;return R1_OK;
        }
        /* Keep the complete old dict until an actual new anchor commit. Never
         * discard all older entries because source/geometry generation changed. */
        status=rfi_anchor_step(plan,entry_index,units,output);
        if (status==R1_OK) legacy->changed[i]=1;
        return status;
    }
    return rfi_anchor_step(plan,entry_index,units,output);
}
