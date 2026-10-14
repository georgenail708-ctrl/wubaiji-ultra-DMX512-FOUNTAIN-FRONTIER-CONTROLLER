/* All current trees are built here in one precharged immutable owning arena. */
static int rc07_node(rctx07_program* p,uint32_t kind,uint64_t children,uint64_t bytes,uint64_t* out)
{
    if (p->nodes_n==p->node_cap || children>p->edge_cap-p->edges_n || bytes>p->data_cap-p->data_n) return R1_BUDGET;
    *out=p->nodes_n++;rfi_tree_node* n=p->nodes+*out;memset(n,0,sizeof(*n));n->kind=kind;
    n->child_first=p->edges_n;n->child_count=children;n->data_first=p->data_n;n->data_count=bytes;
    p->edges_n+=children;p->data_n+=bytes;return R1_OK;
}
static int rc07_tuple(rctx07_program* p,uint64_t n,uint64_t* out) {return rc07_node(p,RFT_TUPLE,n,0,out);}
static void rc07_edge(rctx07_program* p,uint64_t root,uint64_t child,uint64_t node)
{p->edges[p->nodes[root].child_first+child]=node;}
static int rc07_u64(rctx07_program* p,uint64_t value,uint64_t* out)
{
    int status=rc07_node(p,RFT_INT,0,9,out);if (status) return status;
    uint8_t* data=p->data+p->nodes[*out].data_first;
    for (int i=0;i<8;i++) data[i]=(uint8_t)(value>>(i*8));data[8]=0;return R1_OK;
}
static int rc07_float(rctx07_program* p,double value,uint64_t* out)
{
    int status=rc07_node(p,RFT_FLOAT,0,0,out);if (!status) memcpy(&p->nodes[*out].bits,&value,8);return status;
}
static int rc07_literal(rctx07_program* p,const char* value,uint64_t* out)
{
    uint64_t n=(uint64_t)strlen(value);if (n>32) return R1_INVALID;
    int status=rc07_node(p,RFT_TEXT,0,n,out);if (!status) memcpy(p->data+p->nodes[*out].data_first,value,(size_t)n);return status;
}
static rfi_tree_view rc07_view(const rctx07_program* p,uint64_t root)
{
    rfi_tree_view out={root,p->identity_space,p->nodes_n,p->edges_n,p->data_n,p->nodes,p->edges,p->data};return out;
}
static int rc07_truth(const rctx07_value* v,int* truth)
{
    if (v->reserved || v->kind==RC07_FOREIGN) return R1_PENDING;
    if (v->kind==RC07_MISSING || v->kind==RC07_NONE) {*truth=0;return R1_OK;}
    if (v->kind==RC07_TEXT) {*truth=v->bytes!=0;return R1_OK;}
    if (v->kind==RC07_F64) {double x;memcpy(&x,&v->bits,8);*truth=x!=0.;return R1_OK;}
    if (v->kind==RC07_BOOL) {if (v->bits>1) return R1_PENDING;*truth=v->bits!=0;return R1_OK;}
    if (v->kind==RC07_I64) {*truth=v->bits!=0;return R1_OK;}
    /* Arbitrary integer truth is read incrementally by the scalar converter;
     * never walk an unbounded original integer inside a branch helper. */
    return R1_PENDING;
}
static void rc07_program_retain(rctx07_program* p) {if (p && p->magic==RCTX07_MAGIC) p->refs++;}
static void rc07_program_release(rctx07_program* p)
{
    if (!p || p->magic!=RCTX07_MAGIC || --p->refs) return;
    r1_ledger* ledger=p->ledger;void* allocation=p->allocation;uint64_t bytes=p->bytes;
    p->magic=0;r1_free(ledger,allocation,bytes);r1_ledger_release(ledger);
}
