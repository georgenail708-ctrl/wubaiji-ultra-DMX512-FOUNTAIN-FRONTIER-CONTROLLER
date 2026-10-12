/* RESEARCH ONLY. Append after the frozen scalar/Library includes. */
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#define CE_PS_ABI 1
enum { CE_PS_TYPED=1 };
typedef struct {
    uint32_t abi,struct_size; int32_t direction; uint32_t flags;
    int64_t n; const ce_cs_request *scalar;
    const double *xs,*ys; const ce_traj_value *coordinates;
    const uint64_t *x_tokens,*y_tokens; int64_t *indices;
} ce_ps_request;
typedef struct {
    int32_t status,semantic; int64_t index; int32_t stage,reserved;
    ce_traj_result result; ce_cs_report scalar;
    uint64_t keys,comparisons,runs,merges,values;
} ce_ps_report;
typedef struct {
    double first,second; uint64_t first_owner,second_owner; int pair;
} ce_ps_key;
typedef struct {
    const ce_ps_request *q; ce_ps_report *r; ce_cs_context scalar;
} ce_ps_context;
static int ce_ps_fail(ce_ps_context *c,int semantic,int64_t index,int selector) {
    c->r->semantic=semantic; c->r->index=index; c->r->stage=102;
    ce_cs_result_init(&c->r->result); c->r->result.kind=CE_TR_ERROR;
    c->r->result.semantic=semantic; c->r->result.stage=102;
    c->r->result.selector=selector; return 0;
}
static int ce_ps_valid(const ce_ps_request *q) {
    if(!q || q->abi!=CE_PS_ABI || q->struct_size!=sizeof(*q)) return -1;
    if(q->direction<2 || q->direction>7 || q->flags&~CE_PS_TYPED) return -1;
    if(q->n<0 || (uint64_t)q->n>(uint64_t)PTRDIFF_MAX/sizeof(ce_ps_key)/4) return -1;
    if(!q->scalar || ce_cs_validate(q->scalar)) return -1;
    if(!q->n) return 0;
    if(!q->indices || !q->x_tokens || !q->y_tokens) return -1;
    if(q->flags&CE_PS_TYPED) return q->coordinates?0:-1;
    return q->xs && q->ys?0:-1;
}
