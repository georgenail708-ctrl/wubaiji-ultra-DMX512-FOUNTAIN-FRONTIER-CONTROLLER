/* K05ab research candidate v1; include after curve LUT and trajectory helpers.
 * Integer/float comparison adapts CPython 3.13.13 float_richcompare at
 * 01104ce1beb3135c2e0c01ec835b994c1f55a1c0; retain the existing full PSF license
 * and append the actual base-256 adaptation notice if adopted by root. */
#define CE_CS_ABI 1
enum { CS_MODEL=0, CS_PHASE=1, CS_EVAL_PHASE=2, CS_VALUE=3 };
enum { CS_LOOP=1, CS_MODEL_PRESENT=2, CS_DURATION_FALSE=4, CS_FB_ERROR=8 };
enum { CS_TEXT=8, CS_NONE=9, CS_INDEX=8, CS_METADATA=9 };
enum { CS_DURATION_STAGE=1, CS_TIME_STAGE=2, CS_INPUT_STAGE=3,
       CS_SELECT_STAGE=4, CS_X0_STAGE=5, CS_Y0_STAGE=6,
       CS_X1_STAGE=7, CS_Y1_STAGE=8, CS_OUTX_STAGE=9, CS_OUTY_STAGE=10,
       CS_INX_STAGE=11, CS_INY_STAGE=12, CS_MODEL_RESULT_STAGE=13,
       CS_FB_META_STAGE=14, CS_FB_ENDPOINT_STAGE=15, CS_RATIO_STAGE=16,
       CS_DIFFERENCE_STAGE=17, CS_ADD_STAGE=18, CS_FB_RESULT_STAGE=19 };
typedef struct {
    ce_traj_value x,y,c1x,c1y,c2x,c2y;
    uint32_t present,reserved;
} ce_cs_point;
typedef struct { ce_traj_value x,y; } ce_cs_pair;
typedef struct {
    uint32_t abi,struct_size; int32_t mode; uint32_t flags;
    ce_traj_value argument,duration;
    const ce_cs_point *model_points; int64_t model_count;
    const ce_cs_pair *fallback_points; int64_t fallback_count;
    const uint8_t *integers; int64_t integer_size;
    int32_t fallback_semantic,fallback_selector;
} ce_cs_request;
typedef struct {
    int32_t status,semantic; int64_t index; int32_t stage,reserved;
    ce_traj_result result;
    uint64_t phase,model,segment,bezier,bezier_samples,values,clamps;
    uint64_t integer_subtract,integer_conversions,semantic_errors,handled_semantic;
} ce_cs_report;
typedef struct {
    uint64_t calls,keys,comparisons,runs,merges;
} ce_cs_sort_report;
typedef struct {
    const ce_cs_request *request; ce_cs_report *report;
    ce_traj_request integer_view; int64_t error_index;
    const ce_traj_value *selected;
    ce_cs_sort_report *sort_report;
} ce_cs_context;
static int ce_cs_validate(const ce_cs_request *q);
static void ce_cs_result_init(ce_traj_result *out) {
    memset(out,0,sizeof(*out)); out->selector=-1;
}
static int ce_cs_error(ce_cs_context *c,ce_traj_result *out,int semantic,
                        int stage,int selector,int64_t index) {
    ce_cs_result_init(out); out->kind=CE_TR_ERROR; out->semantic=semantic;
    out->stage=stage; out->selector=selector; c->error_index=index; return 0;
}
static void ce_cs_number(ce_traj_result *out,double value) {
    ce_cs_result_init(out); out->kind=CE_TR_NUMBER; out->number=value;
}
static int ce_cs_count(int64_t n,size_t size) {
    return n>=0 && (uint64_t)n<=SIZE_MAX/size;
}
static int ce_cs_value_valid(const ce_cs_request *q,const ce_traj_value *v) {
    if(v->kind==CE_TR_INT) return v->size>0
        && ce_tr_bounds(v->offset,v->size,q->integer_size);
    if(v->kind==CE_TR_BOOL) return v->number==0. || v->number==1.;
    if(v->kind==CS_TEXT) return (v->size==1 || v->size==2)
        && (v->offset==0 || v->offset==CE_TR_BIG || v->offset==CE_TR_PARSE_TYPE
            || v->offset==CE_TR_PARSE_VALUE || v->offset==CS_METADATA);
    return (v->kind>=CE_TR_OBJECT && v->kind<=CE_TR_OVERFLOW) || v->kind==CS_NONE;
}
static int ce_cs_model_valid(const ce_cs_request *q) {
    if(!ce_cs_count(q->model_count,sizeof(ce_cs_point))) return 0;
    if(q->model_count && !q->model_points) return 0;
    for(int64_t i=0;i<q->model_count;++i) {
        const ce_cs_point *p=q->model_points+i;
        if(p->reserved || (p->present&~15u)) return 0;
        const ce_traj_value *fields[6]={&p->x,&p->y,&p->c1x,&p->c1y,&p->c2x,&p->c2y};
        for(int j=0;j<6;++j) if(!ce_cs_value_valid(q,fields[j])) return 0;
    }
    return 1;
}
static int ce_cs_fallback_valid(const ce_cs_request *q) {
    if(!ce_cs_count(q->fallback_count,sizeof(ce_cs_pair))) return 0;
    if(q->fallback_count && !q->fallback_points) return 0;
    for(int64_t i=0;i<q->fallback_count;++i)
        if(!ce_cs_value_valid(q,&q->fallback_points[i].x)
            || !ce_cs_value_valid(q,&q->fallback_points[i].y)) return 0;
    if(q->flags&CS_FB_ERROR) return q->fallback_semantic==CS_METADATA
        && q->fallback_selector>=0;
    return q->fallback_semantic==0;
}
static int ce_cs_validate(const ce_cs_request *q) {
    if(!q || q->abi!=CE_CS_ABI || q->struct_size!=sizeof(*q)) return -1;
    if(q->mode<CS_MODEL || q->mode>CS_VALUE || (q->flags&~15u)) return -1;
    if(!ce_cs_count(q->integer_size,1) || (q->integer_size && !q->integers)) return -1;
    if(!ce_cs_value_valid(q,&q->argument)) return -1;
    if(q->mode==CS_PHASE || q->mode==CS_VALUE) {
        if(!ce_cs_value_valid(q,&q->duration)) return -1;
        if((q->flags&CS_DURATION_FALSE) && q->duration.kind!=CS_TEXT
            && q->duration.kind!=CS_NONE) return -1;
    }
    if(q->mode==CS_MODEL || ((q->mode==CS_EVAL_PHASE || q->mode==CS_VALUE)
        && (q->flags&CS_MODEL_PRESENT))) if(!ce_cs_model_valid(q)) return -1;
    if(q->mode==CS_EVAL_PHASE || q->mode==CS_VALUE)
        if(!ce_cs_fallback_valid(q)) return -1;
    return 0;
}
