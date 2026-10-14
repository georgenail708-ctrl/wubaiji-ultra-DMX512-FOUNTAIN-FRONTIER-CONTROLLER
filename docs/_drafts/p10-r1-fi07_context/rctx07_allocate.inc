static int rc07_allocate(rctx07_job* j)
{
    uint64_t objects=j->raw->clips_n+j->raw->tracks_n,bytes=sizeof(rctx07_program),nodes=0;
    if (objects<j->raw->clips_n || !r3_add_bytes(&nodes,j->values_count,4) || !r3_add_bytes(&nodes,objects,64)
        || !r3_add_bytes(&nodes,j->candidates->refs_n,12)
        || !r3_add_bytes(&nodes,j->candidates->entries_n,32)
        || !r3_add_bytes(&nodes,64,1)) return R1_BUDGET;
    j->nodes_bound=nodes;if (nodes>UINT64_MAX/4) return R1_BUDGET;j->edges_bound=nodes*4;
    if (j->data_bound>UINT64_MAX-135) return R1_BUDGET;
    uint64_t data_capacity=(j->data_bound+135)&~UINT64_C(7);
    if (!r3_add_bytes(&bytes,nodes,sizeof(rfi_tree_node))
        || !r3_add_bytes(&bytes,j->edges_bound,sizeof(uint64_t))
        || !r3_add_bytes(&bytes,data_capacity,1)
        || !r3_add_bytes(&bytes,j->raw->clips_n,sizeof(rctx07_clip_plan))
        || !r3_add_bytes(&bytes,j->raw->tracks_n,sizeof(rctx07_track_plan))
        || !r3_add_bytes(&bytes,j->candidates->entries_n,sizeof(rctx07_row))) return R1_BUDGET;
    void* allocation=r1_allocate(j->ledger,bytes);if (!allocation) return R1_BUDGET;
    rctx07_program* p=allocation;j->program=p;p->allocation=allocation;p->bytes=bytes;
    p->magic=RCTX07_MAGIC;p->refs=1;p->ledger=j->ledger;j->ledger->refs++;
    p->source_owner=j->raw->source_owner;p->identity_space=j->raw->identity_space;
    p->nodes=(rfi_tree_node*)(p+1);p->node_cap=nodes;
    p->edges=(uint64_t*)(p->nodes+nodes);p->edge_cap=j->edges_bound;
    p->data=(uint8_t*)(p->edges+p->edge_cap);p->data_cap=data_capacity;
    p->clips=(rctx07_clip_plan*)(p->data+p->data_cap);p->clips_n=j->raw->clips_n;
    p->tracks=(rctx07_track_plan*)(p->clips+p->clips_n);p->tracks_n=j->raw->tracks_n;
    p->rows=(rctx07_row*)(p->tracks+p->tracks_n);p->rows_n=j->candidates->entries_n;
    j->item_capacity=j->largest>j->candidates->refs_n ? j->largest : j->candidates->refs_n;
    if (!r3_add_bytes(&j->item_bytes,j->item_capacity,2*sizeof(rctx07_item))) return R1_BUDGET;
    if (j->item_bytes) {j->items=r1_allocate(j->ledger,j->item_bytes);if (!j->items) return R1_BUDGET;j->outer=j->items+j->item_capacity;}
    j->phase=RC07_CLIPS;j->row=j->subphase=j->member=j->leaf=j->scan=j->kind=0;return R1_OK;
}
