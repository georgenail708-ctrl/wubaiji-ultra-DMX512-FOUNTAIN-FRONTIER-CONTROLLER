/* Trajectory ABI v1. Integer algorithms adapt CPython 3.13.13, PSF License v2.
 * Fixed source: 01104ce1beb3135c2e0c01ec835b994c1f55a1c0.
 * Full upstream license and actual adaptation notice accompany the release. */
#define CE_TR_ABI 1
enum { CE_TR_OBJECT=0, CE_TR_FLOAT=1, CE_TR_INT=2, CE_TR_BOOL=3,
       CE_TR_TYPE=4, CE_TR_VALUE=5, CE_TR_OVERFLOW=6 };
enum { CE_TR_NONE=0, CE_TR_SELECTED=1, CE_TR_NUMBER=2, CE_TR_ERROR=3 };
enum { CE_TR_OK=0, CE_TR_BIG=1, CE_TR_NAN=2, CE_TR_INF=3,
       CE_TR_ZERO=4, CE_TR_BYTE=5, CE_TR_PARSE_TYPE=6, CE_TR_PARSE_VALUE=7 };
typedef struct { int32_t kind, selector; int64_t offset, size; double number; } ce_traj_value;
typedef struct { int64_t offset, count; double position; } ce_traj_sample;
typedef struct { int32_t kind, selector, semantic, stage; double number; } ce_traj_result;
typedef struct { double start, duration, rate, source_duration, position;
                 int32_t stages, reserved; } ce_traj_time;
typedef struct { int32_t active, reserved; double source; } ce_traj_time_out;
typedef struct { int32_t universe, offset; int64_t fixture; } ce_traj_offset;
typedef struct { int32_t kind, mode; int64_t offset, count;
                 ce_traj_value value, target; } ce_traj_applier;
typedef struct { int32_t universe, offset; ce_traj_value value; } ce_traj_event;
typedef struct {
    uint32_t abi, struct_size;
    const ce_traj_time *times; int64_t time_count; ce_traj_time_out *time_out;
    const ce_traj_sample *samples; int64_t sample_count;
    const double *timestamps; const ce_traj_value *points; int64_t point_count;
    ce_traj_result *results;
    const ce_traj_applier *appliers; int64_t applier_count;
    const ce_traj_offset *offsets; int64_t offset_count;
    const ce_traj_event *events; int64_t event_count;
    ce_traj_event *event_out; int64_t event_capacity;
    const uint8_t *integers; int64_t integer_size;
    uint8_t *bytes_out; int64_t byte_capacity;
    uint8_t *frame, *mask;
} ce_traj_request;
typedef struct {
    int32_t status, semantic; int64_t index, written_bytes, event_count;
    uint64_t source, sample, applier, add, merge, values;
    uint64_t integer_subtract, integer_conversions, semantic_errors;
} ce_traj_report;
typedef struct { int sign; size_t size; uint8_t *digits; } ce_tr_big;
static void ce_tr_big_clear(ce_tr_big *v) {
    free(v->digits); v->digits=NULL; v->size=0; v->sign=0;
}
static int ce_tr_alloc(ce_tr_big *v, size_t n) {
    if (n==0 || n>SIZE_MAX-1) return -2;
    v->digits=(uint8_t*)calloc(n,1); v->size=n; v->sign=1;
    return v->digits ? 0 : -2;
}
static void ce_tr_normalize(ce_tr_big *v) {
    while(v->size && v->digits[v->size-1]==0) --v->size;
    if(!v->size) v->sign=0;
}
static int ce_tr_bounds(int64_t off,int64_t n,int64_t total) {
    return off>=0 && n>=0 && off<=total && n<=total-off;
}
static int ce_tr_semantic(ce_traj_report *r,int code,int64_t index) {
    r->semantic=code; r->index=index; ++r->semantic_errors; return 0;
}
