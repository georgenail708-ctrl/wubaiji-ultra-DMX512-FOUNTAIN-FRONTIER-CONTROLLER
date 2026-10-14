/* Source-only LOCK4 C math on the existing CE DLL. No heap/thread/clock IO.
 * Original reference: MainWindow._connect_timeline_transport._clock_source.
 * This include is independent of the frozen SNTC clock ABI. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef struct {
    uint32_t abi,size,playing,reserved;
    double raw_t0,raw_position,raw_now;
} QtcLocalClockInput;
typedef struct {
    uint32_t abi,size,render,reserved;
    double position;
} QtcLocalClockPlan;

/* Declaration sizes/offsets, not runtime acceptance. */
typedef char qtc_local_input_size[(sizeof(QtcLocalClockInput)==40)?1:-1];
typedef char qtc_local_plan_size[(sizeof(QtcLocalClockPlan)==24)?1:-1];
typedef char qtc_local_t0_offset[(offsetof(QtcLocalClockInput,raw_t0)==16)?1:-1];
typedef char qtc_local_now_offset[(offsetof(QtcLocalClockInput,raw_now)==32)?1:-1];
typedef char qtc_local_position_offset[(offsetof(QtcLocalClockPlan,position)==16)?1:-1];

static int qtc_local_overlap(const void* a,size_t an,const void* b,size_t bn) {
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    return x<=y ? y-x<an : x-y<bn;
}

CE_API int32_t ce_sntc_local_clock_v1(const QtcLocalClockInput* input,QtcLocalClockPlan* out) {
    QtcLocalClockPlan value;double position;
    if(!input||!out||((uintptr_t)input&7u)||((uintptr_t)out&7u)
            ||qtc_local_overlap(input,sizeof(*input),out,sizeof(*out)))return 1;
    if(input->abi!=1||input->size!=sizeof(*input)||input->playing>1||input->reserved)return 1;
    memset(&value,0,sizeof(value));value.abi=1;value.size=sizeof(value);
    /* Exact old source early branch: no t0/position/now arithmetic or read. */
    if(!input->playing){*out=value;return 0;}
    position=input->raw_now-input->raw_t0;
    /* max(0.0, position): first operand wins NaN and equal signed-zero ties. */
    value.position=position>0. ? position : 0.;value.render=1;
    *out=value;return 0;
}

CE_API uint32_t ce_sntc_local_clock_size_v1(uint32_t kind) {
    switch(kind){case 0:return sizeof(QtcLocalClockInput);
        case 1:return sizeof(QtcLocalClockPlan);default:return 0;}
}
