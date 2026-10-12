/* Research ABI1. Include after mature trajectory integer/scalar primitives.
 * No new Python runtime, global owner, DLL, or output cache is introduced. */
enum { PR_QUANTIZE,PR_MIX,PR_RANGE,PR_MASTER,PR_DEFAULT,PR_READ,PR_MOTOR,
       PR_TRUNCATE,PR_VIRTUAL,PR_FILL,PR_ADVANCE,PR_FINISH,PR_SCRUB,PR_DEBOUNCE,
       PR_MEAN,PR_CURVE,PR_SPARSE,PR_JITTER,PR_FLOAT,PR_TICK,PR_Z_FINAL,PR_ROUND,PR_JITTER_BOUNDS };
enum { PR_OPERAND=8,PR_FLOOR_NAN=9,PR_FLOOR_INF=10,PR_DIV_BIG=11 };
enum { PR_NONE=9,PR_SELECTED=10 };
enum { PR_FILL_PREFIX_ONLY=1 };
typedef struct {
    int64_t value_offset,value_count,number_offset,number_count,index,total;
    int32_t mode; uint32_t flags,present; int32_t layers[4]; uint32_t reserved;
} ce_pr_item;
typedef struct {
    uint32_t abi,struct_size; int32_t op; uint32_t flags;
    const ce_traj_value *values; int64_t value_count;
    const ce_pr_item *items; int64_t item_count;
    const int64_t *offsets; int64_t offset_count;
    const double *numbers; int64_t number_count;
    const uint8_t *integers; int64_t integer_size;
    ce_traj_value *value_out; int64_t value_capacity;
    double *number_out; int64_t number_capacity;
    int64_t *index_out; int64_t index_capacity;
    uint8_t *bytes_out; int64_t byte_capacity;
} ce_pr_request;
typedef struct {
    int32_t status,semantic; int64_t index; int32_t stage; uint32_t reserved;
    int64_t completed,written_values,written_numbers,written_indices,written_bytes;
    uint64_t read,row,range,default_value,physical,fill,clock,curve,mean;
    uint64_t integer_arithmetic,integer_conversions,semantic_errors;
} ce_pr_report;
typedef struct {
    const ce_pr_request *q; ce_pr_report *r; ce_traj_request integers;
    int64_t item; ce_traj_value *fill_cells; int64_t fill_count;
} ce_pr_context;
typedef struct {
    int kind,selector; double number; ce_tr_big integer;
} ce_pr_number;

static int ce_pr_fail(ce_pr_context *c,int semantic,int stage,int selector) {
    if(semantic<0) return semantic;
    if(!c->r->semantic) {
        c->r->semantic=semantic; c->r->index=c->item; c->r->stage=stage;
        c->r->reserved=selector>=0?(uint32_t)selector+1u:0;
        ++c->r->semantic_errors;
    }
    return semantic;
}
static int ce_pr_space(int64_t count,size_t size,const void *pointer) {
    return count>=0 && (uint64_t)count<=SIZE_MAX/size && (!count || pointer);
}
static int ce_pr_value_valid(const ce_pr_request *q,const ce_traj_value *v) {
    if(v->selector<-1 || v->kind<0 || v->kind>9 || v->kind==7) return 0;
    if(v->kind==CE_TR_INT)
        return v->size>0 && ce_tr_bounds(v->offset,v->size,q->integer_size);
    return v->offset==0 && v->size==0;
}
static int ce_pr_capacity(const ce_pr_request *q,int64_t per,int64_t cap) {
    return per==0 || q->item_count<=cap/per;
}
static int ce_pr_shape(const ce_pr_request *q,const ce_pr_item *p) {
    static const int v[] ={1,4,0,0,1,1,1,1,5,0,3,2,2,2,-1,5,1,0,1,4,0,1,0};
    static const int d[] ={0,0,2,3,0,0,0,0,0,0,0,0,0,0,0,0,0,-1,0,0,1,0,1};
    int vn=v[q->op],dn=d[q->op];
    if(q->op==PR_RANGE && (p->flags&16u)) vn=2;
    if((vn>=0 && p->value_count!=vn) || (dn>=0 && p->number_count!=dn)) return 0;
    uint32_t flags=0; int modes=0;
    switch(q->op) {
    case PR_QUANTIZE:modes=3;break;
    case PR_MIX: if(p->present&~15u) return 0;
        for(int i=0;i<4;++i) if(p->layers[i]<0 || p->layers[i]>4) return 0;break;
    case PR_RANGE: flags=31; modes=2; if((p->flags&7u)>5u) return 0;break;
    case PR_MASTER: flags=3; modes=2; if(p->flags>2u) return 0;break;
    case PR_VIRTUAL:flags=1;if(p->present&~15u) return 0;break;
    case PR_FILL:flags=PR_FILL_PREFIX_ONLY;break;
    case PR_FINISH:case PR_TICK:flags=1;break;
    case PR_CURVE:flags=1;modes=4;break;
    case PR_JITTER:flags=1;if(p->number_count!=((p->flags&1)?3:2)) return 0;break;
    default:break;
    }
    return !p->reserved && !(p->flags&~flags) && p->mode>=0 && p->mode<=modes;
}
static int ce_pr_validate(const ce_pr_request *q) {
    if(!q || q->abi!=1 || q->struct_size!=sizeof(*q) || q->flags
       || q->op<0 || q->op>PR_JITTER_BOUNDS) return -1;
    if(!ce_pr_space(q->value_count,sizeof(ce_traj_value),q->values)
       || !ce_pr_space(q->item_count,sizeof(ce_pr_item),q->items)
       || !ce_pr_space(q->offset_count,sizeof(int64_t),q->offsets)
       || !ce_pr_space(q->number_count,sizeof(double),q->numbers)
       || !ce_pr_space(q->integer_size,1,q->integers)
       || !ce_pr_space(q->value_capacity,sizeof(ce_traj_value),q->value_out)
       || !ce_pr_space(q->number_capacity,sizeof(double),q->number_out)
       || !ce_pr_space(q->index_capacity,sizeof(int64_t),q->index_out)
       || !ce_pr_space(q->byte_capacity,1,q->bytes_out)) return -1;
    static const int vo[]={1,1,1,0,0,1,0,1,4,1,2,0,0,0,1,1,1,0,0,2,0,1,0};
    static const int no[]={1,0,2,2,1,1,0,0,1,0,0,1,2,0,0,1,0,1,1,1,1,0,2};
    static const int io[]={0,0,0,1,0,0,2,0,0,1,1,1,0,1,0,0,0,0,0,2,1,0,0};
    if(!ce_pr_capacity(q,vo[q->op],q->value_capacity)
       || !ce_pr_capacity(q,no[q->op],q->number_capacity)
       || !ce_pr_capacity(q,io[q->op],q->index_capacity)) return -2;
    for(int64_t i=0;i<q->value_count;++i) if(!ce_pr_value_valid(q,q->values+i)) return -1;
    for(int64_t i=0;i<q->item_count;++i) {
        const ce_pr_item *p=q->items+i;
        if(!ce_tr_bounds(p->value_offset,p->value_count,q->value_count)
           || !ce_tr_bounds(p->number_offset,p->number_count,q->number_count)
           || !ce_pr_shape(q,p)) return -1;
    }
    return 0;
}
static int ce_pr_double(ce_pr_context *c,const ce_traj_value *v,double *out,int stage) {
    ce_traj_report report={0};
    int rc=ce_tr_value_double(&c->integers,v,&report,out);
    c->r->integer_conversions+=report.integer_conversions;
    if(v->kind==8 || v->kind==PR_NONE) rc=CE_TR_PARSE_TYPE;
    return rc?ce_pr_fail(c,rc,stage,v->selector):0;
}
static void ce_pr_number_out(ce_pr_context *c,double value) {
    c->q->number_out[c->r->written_numbers++]=value;
}
static void ce_pr_index_out(ce_pr_context *c,int64_t value) {
    c->q->index_out[c->r->written_indices++]=value;
}
static int ce_pr_value_out(ce_pr_context *c,const ce_pr_number *n) {
    ce_traj_value out={0}; int rc=0;
    if(n->selector>=0) { out.kind=PR_SELECTED; out.selector=n->selector; }
    else if(n->kind==CE_TR_FLOAT) { out.kind=CE_TR_FLOAT;out.selector=-1;out.number=n->number; }
    else {
        ce_traj_report report={0}; report.written_bytes=c->r->written_bytes;
        ce_traj_request view=c->integers;
        view.bytes_out=c->q->bytes_out;view.byte_capacity=c->q->byte_capacity;
        rc=ce_tr_encode(&n->integer,&view,&report,&out);
        if(rc) return rc;
        out.selector=-1;c->r->written_bytes=report.written_bytes;
    }
    c->q->value_out[c->r->written_values++]=out;return 0;
}
