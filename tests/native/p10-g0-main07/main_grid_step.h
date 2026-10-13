#ifndef CE_MAIN_GRID_STEP_G0_H
#define CE_MAIN_GRID_STEP_G0_H
#include "../p10-g0-main06/main_grid.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { G0_STEP_ABI=7, G0_BUILDING=7, G0_BAD_UNITS=8, G0_UNIT_CAP=64 };
typedef struct {
    uint32_t abi,size,status,reserved;
    uint64_t next,completed_units;
    double end;
    G0Request request;
} G0CompileState;
CE_API int32_t ce_main_grid_begin_g0_v7(const G0Request*,G0CompileState*);
CE_API int32_t ce_main_grid_step_g0_v7(G0CompileState*,uint32_t,G0Grid*);
CE_API uint32_t ce_main_grid_state_size_g0_v7(void);
#ifdef __cplusplus
}
#endif
#endif
