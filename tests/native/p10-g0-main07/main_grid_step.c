/* Source-only bounded successor. Include original G0 once in this TU; original
 * Main06 conversion/grid/position exports and their controls stay unchanged. */
#include "main_grid_step.h"
#include "../p10-g0-main06/main_grid.c"
#if defined(_MSC_VER)
#pragma float_control(precise,on,push)
#pragma fp_contract(off)
#else
#pragma STDC FP_CONTRACT OFF
#endif

static int g0_v7_request_valid(const G0Request* request) {
    if(request->abi!=G0_ABI||request->size!=sizeof(*request)||request->reserved
       ||request->timer_present>1||request->stride<sizeof(G0Atom)
       ||request->start_offset>request->stride-sizeof(G0Atom)
       ||request->duration_offset>request->stride-sizeof(G0Atom))return G0_BAD_ABI;
    if(request->count>UINT64_MAX/request->stride
       ||request->row_bytes!=request->count*request->stride
       ||(request->row_bytes&&!request->rows)
       ||request->row_bytes>UINTPTR_MAX-(uintptr_t)request->rows)return G0_BAD_BUFFER;
    return G0_OK;
}

CE_API int32_t ce_main_grid_begin_g0_v7(const G0Request* input,G0CompileState* state) {
    G0Request request;G0CompileState value={0};int status;
    if(!input||!state||((uintptr_t)input%8)||((uintptr_t)state%8)
       ||g0_overlap(input,sizeof(*input),state,sizeof(*state)))return G0_BAD_BUFFER;
    request=*input;status=g0_v7_request_valid(&request);if(status)return status;
    if(g0_overlap(request.rows,request.row_bytes,state,sizeof(*state)))return G0_BAD_BUFFER;
    value.abi=G0_STEP_ABI;value.size=sizeof(value);value.status=G0_BUILDING;
    value.request=request;*state=value;return G0_BUILDING;
}

static int g0_v7_state_valid(const G0CompileState* state,const G0Grid* output) {
    int status;
    if(state->abi!=G0_STEP_ABI||state->size!=sizeof(*state)||state->reserved
       ||state->status>G0_BUILDING||state->status==G0_BAD_INDEX
       ||!(state->end>=0.))return G0_BAD_ABI;
    status=g0_v7_request_valid(&state->request);if(status)return status;
    if(state->next>state->request.count||state->completed_units<state->next
       ||state->completed_units>state->next+1
       ||(state->status==G0_BUILDING&&state->completed_units!=state->next)
       ||(state->status==G0_OK&&(state->next!=state->request.count
            ||state->completed_units!=state->next+1||!isfinite(state->end))))return G0_BAD_ABI;
    if(g0_overlap(state->request.rows,state->request.row_bytes,state,sizeof(*state))
       ||g0_overlap(state->request.rows,state->request.row_bytes,output,sizeof(*output)))return G0_BAD_BUFFER;
    return G0_OK;
}

static int g0_v7_row(G0CompileState* state) {
    const G0Request* request=&state->request;
    const uint8_t* row=(const uint8_t*)request->rows+state->next*request->stride;
    double start,duration,candidate;int status;
    status=g0_number(row+request->start_offset,&start);if(status)return status;
    status=g0_number(row+request->duration_offset,&duration);if(status)return status;
    candidate=start+(duration>0.?duration:0.);
    if(candidate>state->end)state->end=candidate;
    state->next++;return G0_BUILDING;
}

static int g0_v7_finish(const G0CompileState* state,G0Grid* output) {
    const G0Request* request=&state->request;G0Grid value={0};double interval,rounded;
    interval=(double)(request->timer_present?request->timer_ms:23)/1000.;
    if(!(interval>.005))interval=.005;
    if(interval<.015)interval=.015;
    if(interval>.05)interval=.05;
    rounded=ceil(state->end/interval);
    if(!isfinite(rounded))return G0_CEIL_INF;
    if(rounded>=0x1p64)return G0_BIG_COUNT;
    value.abi=G0_ABI;value.size=sizeof(value);value.end=state->end;value.interval=interval;
    value.count=(uint64_t)rounded;
    if(value.count==UINT64_MAX)return G0_BIG_COUNT;
    value.count++;*output=value;return G0_OK;
}

CE_API int32_t ce_main_grid_step_g0_v7(G0CompileState* state,uint32_t units,G0Grid* output) {
    int status;
    if(!state||!output||((uintptr_t)state%8)||((uintptr_t)output%8)
       ||g0_overlap(state,sizeof(*state),output,sizeof(*output)))return G0_BAD_BUFFER;
    if(units>G0_UNIT_CAP)return G0_BAD_UNITS;
    status=g0_v7_state_valid(state,output);if(status)return status;
    if(state->status!=G0_BUILDING)return (int)state->status;
    while(units&&state->status==G0_BUILDING){
        units--;
        status=state->next<state->request.count?g0_v7_row(state):g0_v7_finish(state,output);
        state->completed_units++;
        state->status=(uint32_t)status;
    }
    return (int)state->status;
}
CE_API uint32_t ce_main_grid_state_size_g0_v7(void) {return sizeof(G0CompileState);}
#if defined(_MSC_VER)
#pragma float_control(pop)
#endif
