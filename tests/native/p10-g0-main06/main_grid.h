#ifndef CE_MAIN_GRID_G0_H
#define CE_MAIN_GRID_G0_H
#include <stdint.h>
#ifndef CE_API
#ifdef _WIN32
#define CE_API __declspec(dllexport)
#else
#define CE_API __attribute__((visibility("default")))
#endif
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum { G0_ABI=6,G0_OK=0,G0_BAD_ABI=1,G0_BAD_BUFFER=2,G0_CEIL_INF=3,
       G0_PENDING=4,G0_BIG_COUNT=5,G0_BAD_INDEX=6 };
typedef struct { uint32_t kind,reserved;uint64_t bits; } G0Atom;
typedef struct {
    uint32_t abi,size;const void* rows;uint64_t count,row_bytes;
    uint32_t stride,start_offset,duration_offset;int32_t timer_ms;
    uint32_t timer_present,reserved;
} G0Request;
typedef struct {
    uint32_t abi,size,status,reserved;uint64_t count;double end,interval;
} G0Grid;
CE_API int32_t ce_main_grid_compile_g0(const G0Request*,G0Grid*);
CE_API int32_t ce_main_grid_position_g0(const G0Grid*,uint64_t,double*);
CE_API uint32_t ce_main_grid_size_g0(uint32_t);
#ifdef __cplusplus
}
#endif
#endif
