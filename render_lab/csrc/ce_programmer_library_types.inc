/* RESEARCH ONLY. Include after the five ce_curve_scalar modules. */
#define CE_PL_ABI 2
enum { PL_QUANTIZE=1, PL_SEGMENT=2, PL_DIRECTION=3, PL_REVEAL=4,
       PL_MORPH=5, PL_SHAPE=6, PL_WINDOWS=7, PL_BLEND=8,
       PL_PATH=9, PL_SHAPE_TIME=10, PL_WINDOW_SELECT=11,
       PL_STATIC_SELECT=12, PL_PARTITION=13, PL_OFFSETS=14,
       PL_EFFECTIVE=15, PL_UNIT_PHASE=16 };
enum { PL_SEQUENTIAL=1, PL_WHOLE=2, PL_TYPED=4,
       PL_DEFAULT_FROM=8, PL_DEFAULT_TO=16, PL_PATH_VALUE_READY=64,
       PL_DIRECTION_SHIFT=8 };
enum { PL_VALUE_STAGE=100, PL_QUANT_STAGE=101, PL_COORD_STAGE=102 };
typedef struct {
    uint32_t abi,struct_size; int32_t op; uint32_t flags;
    const ce_cs_request *scalar; int64_t n,group_n;
    const double *a,*b,*c,*d;
    const ce_traj_value *values; const int64_t *offsets;
    ce_traj_value groups,batches; double p0,p1,p2,p3;
    double *out_f; int64_t *out_i; uint8_t *out_b,*out_flags;
    const ce_traj_value *typed_a,*typed_b,*typed_p;
    ce_traj_value *out_value;uint8_t *out_bytes;int64_t byte_capacity;
} ce_pl_request;
typedef struct {
    int32_t status,semantic; int64_t index; int32_t stage,reserved;
    ce_traj_result result; ce_cs_report scalar;
    uint64_t values,quantize,segment,direction,reveal,shape,window,morph;
    uint64_t blend,partition,offsets;
    int64_t written_bytes;
    ce_cs_sort_report scalar_sort;
} ce_pl_report;
typedef struct {
    const ce_pl_request *q; ce_pl_report *r; ce_cs_context scalar;
} ce_pl_context;
static int ce_pl_fail(ce_pl_context *c,int semantic,int stage,int64_t index) {
    c->r->semantic=semantic; c->r->stage=stage; c->r->index=index;
    ce_cs_result_init(&c->r->result); c->r->result.kind=CE_TR_ERROR;
    c->r->result.semantic=semantic; c->r->result.stage=stage;
    return 0;
}
static int ce_pl_scalar_result(ce_pl_context *c,const ce_traj_result *v) {
    if(v->kind!=CE_TR_ERROR) return 0;
    c->r->result=*v; c->r->semantic=v->semantic; c->r->stage=v->stage;
    c->r->index=c->scalar.error_index; return 1;
}
static ce_traj_value ce_pl_float(double v) {
    ce_traj_value out={CE_TR_FLOAT,-1,0,0,v}; return out;
}
static int ce_pl_validate(const ce_pl_request *q) {
    if(!q || q->abi!=CE_PL_ABI || q->struct_size!=sizeof(*q)) return -1;
    if(q->op<PL_QUANTIZE || q->op>PL_UNIT_PHASE) return -1;
    if(q->n<0 || q->n>INT_MAX || q->group_n<0 || q->group_n>INT_MAX) return -1;
    if(!q->scalar || ce_cs_validate(q->scalar)) return -1;
    if(!q->out_f || !q->out_i || !q->out_b || !q->out_flags) return -1;
    if(!q->out_value || q->byte_capacity<0 || (q->byte_capacity && !q->out_bytes)) return -1;
    if(q->typed_p) for(int i=0;i<4;++i)
        if(!ce_cs_value_valid(q->scalar,q->typed_p+i)) return -1;
    int64_t columns=q->n;
    if(q->op==PL_SEGMENT || q->op==PL_PATH || q->op==PL_WINDOWS || q->op==PL_STATIC_SELECT)
        columns=q->n+1;
    if(q->op==PL_OFFSETS || q->op==PL_REVEAL || q->op==PL_MORPH) columns=q->group_n;
    if(q->typed_a) for(int64_t i=0;i<columns;++i)
        if(!ce_cs_value_valid(q->scalar,q->typed_a+i)) return -1;
    if(q->typed_b) for(int64_t i=0;i<(q->op==PL_WINDOWS?q->n+1:q->n);++i)
        if(!ce_cs_value_valid(q->scalar,q->typed_b+i)) return -1;
    if(q->op==PL_SHAPE || q->op==PL_UNIT_PHASE) {
        if(!q->offsets || q->offsets[0] || q->offsets[q->group_n]!=q->n) return -1;
        for(int64_t g=0;g<q->group_n;++g)
            if(q->offsets[g]<0 || q->offsets[g]>q->offsets[g+1]) return -1;
    }
    return 0;
}
