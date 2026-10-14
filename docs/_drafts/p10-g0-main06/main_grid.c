/* Actual ordinary Main grid. Source-only; no allocation/IO/thread/callback.
 * Keep old native G0/R1/oracles intact. C precision must match both ISA builds. */
#include "main_grid.h"
#include <math.h>
#include <stddef.h>
#include <string.h>
#if defined(_MSC_VER)
#pragma float_control(precise,on,push)
#pragma fp_contract(off)
#else
#pragma STDC FP_CONTRACT OFF
#endif
static int g0_overlap(const void* a,uint64_t an,const void* b,uint64_t bn) {
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    return an&&bn&&(x<=y?y-x<an:x-y<bn);
}
static int g0_number(const uint8_t* address,double* out) {
    G0Atom atom;int64_t integer;memcpy(&atom,address,sizeof(atom));
    if(atom.reserved)return G0_BAD_ABI;
    switch(atom.kind){
    case 0:case 4:*out=0.;return G0_OK;
    case 1:memcpy(out,&atom.bits,sizeof(*out));break;
    case 2:memcpy(&integer,&atom.bits,sizeof(integer));*out=(double)integer;break;
    case 3:if(atom.bits>1)return G0_BAD_ABI;*out=atom.bits?1.:0.;break;
    default:return G0_PENDING;
    }
    /* Original getattr(...,0) or0 maps both signed zeros to positive zero. */
    if(*out==0.)*out=0.;return G0_OK;
}
CE_API int32_t ce_main_grid_compile_g0(const G0Request* input,G0Grid* out) {
    G0Request request;G0Grid value={0};double end=0.,interval,rounded;int status;
    if(!input||!out||((uintptr_t)input%8)||((uintptr_t)out%8))return G0_BAD_BUFFER;
    if(g0_overlap(input,sizeof(*input),out,sizeof(*out)))return G0_BAD_BUFFER;
    request=*input;
    if(request.abi!=G0_ABI||request.size!=sizeof(request)||request.reserved
       ||request.timer_present>1||request.stride<sizeof(G0Atom)
       ||request.start_offset>request.stride-sizeof(G0Atom)
       ||request.duration_offset>request.stride-sizeof(G0Atom))return G0_BAD_ABI;
    if(request.count>UINT64_MAX/request.stride
       ||request.row_bytes!=request.count*request.stride
       ||(request.row_bytes&&!request.rows)
       ||g0_overlap(request.rows,request.row_bytes,out,sizeof(*out)))return G0_BAD_BUFFER;
    for(uint64_t k=0;k<request.count;k++){
        const uint8_t* row=(const uint8_t*)request.rows+k*request.stride;
        double start,duration,candidate;
        status=g0_number(row+request.start_offset,&start);if(status)return status;
        status=g0_number(row+request.duration_offset,&duration);if(status)return status;
        /* Python max(0,duration) keeps its left operand for NaN/ties. */
        candidate=start+(duration>0.?duration:0.);
        if(candidate>end)end=candidate;
    }
    interval=(double)(request.timer_present?request.timer_ms:23)/1000.;
    if(!(interval>.005))interval=.005;
    if(interval<.015)interval=.015;
    if(interval>.05)interval=.05;
    rounded=ceil(end/interval);
    if(!isfinite(rounded))return G0_CEIL_INF;
    if(rounded>=0x1p64)return G0_BIG_COUNT;
    value.abi=G0_ABI;value.size=sizeof(value);value.end=end;value.interval=interval;
    value.count=(uint64_t)rounded;
    if(value.count==UINT64_MAX)return G0_BIG_COUNT;
    value.count++;*out=value;return G0_OK;
}
CE_API int32_t ce_main_grid_position_g0(const G0Grid* grid,uint64_t index,double* out) {
    double position;
    if(!grid||!out||((uintptr_t)grid%8)||((uintptr_t)out%8)
       ||g0_overlap(grid,sizeof(*grid),out,sizeof(*out)))return G0_BAD_BUFFER;
    if(grid->abi!=G0_ABI||grid->size!=sizeof(*grid)||grid->status||grid->reserved
       ||!grid->count||!isfinite(grid->end)||grid->end<0.
       ||!isfinite(grid->interval)||grid->interval<.015||grid->interval>.05)return G0_BAD_ABI;
    if(index>=grid->count)return G0_BAD_INDEX;
    position=(double)index*grid->interval;
    *out=position<grid->end?position:grid->end;return G0_OK;
}
CE_API uint32_t ce_main_grid_size_g0(uint32_t kind) {
    switch(kind){case 0:return sizeof(G0Atom);case 1:return sizeof(G0Request);
    case 2:return sizeof(G0Grid);default:return 0;}
}
#if defined(_MSC_VER)
#pragma float_control(pop)
#endif
