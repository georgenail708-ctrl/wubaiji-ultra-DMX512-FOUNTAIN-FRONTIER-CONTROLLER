/* Incremental source-only producer. Frozen03/04 bytes/exports are preserved.
 * Numeric data preparation, temporal phases, held selection, LT/masks/fades,
 * score/arbitration/mix/state all execute inside a chronological C chunk.
 * No Python callback, computed perframe input matrix or default product mount. */
#include "../../candidate04/native/ce_owned_recorded_r4.c"
#include "r5_types.h"
#include "r5_values.inc"
#include "r5_direction_mode.inc"
#include "r5_sequence_compile.inc"
#include "r5_clock.inc"
#include "r5_schedule.inc"
#include "r5_shape.inc"
#include "r5_compile.inc"
#include "r5_seed.inc"
#include "r5_eval.inc"
#include "r5_hold_consumption.inc"
#include "r5_history.inc"
#include "r5_api.inc"
#include "r5_create.inc"
