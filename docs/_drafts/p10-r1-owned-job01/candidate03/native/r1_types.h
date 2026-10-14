/* Research R1-01. C owns numeric work and every returned block. No callbacks. */
#ifndef CE_R1_TYPES_H
#define CE_R1_TYPES_H
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define R1_ABI 1
#define R1_UNIVERSE_LIMIT 64000
#define R1_STATE_PENDING UINT64_C(0x1ff) /* 7 domains + Draw + causal anchors */
#define R1_PENDING_FEATURE UINT64_C(1)
#define R1_PENDING_ATOM UINT64_C(2)
#define R1_PENDING_PAYLOAD UINT64_C(4)
#define R1_PENDING_UNIVERSE UINT64_C(8)
#define R1_PENDING_WARNING UINT64_C(16)
#define R1_PENDING_CAPACITY UINT64_C(32)
#define R1_PENDING_SORT UINT64_C(64)
#define R1_SPARSE_BLOCK 64
#define R1_MAGIC UINT64_C(0x52313031424c4f43)
#define R1_KERNEL_RESERVE UINT64_C(3072) /* existing CSR max-group=1 malloc */
enum { R1_OK=0, R1_CANCELLED=1, R1_DONE=2, R1_BUILDING=3, R1_INVALID=-1,
       R1_BUDGET=-2, R1_PENDING=-3, R1_KERNEL=-4 };
enum { R1_MISSING=0, R1_F64=1, R1_I64=2, R1_BOOL=3, R1_NONE=4, R1_UNSUPPORTED=9 };
enum { R1_START, R1_DURATION, R1_RATE, R1_SOURCE_DURATION,
       R1_TIMER_MS, R1_TRACK_ORDER, R1_RECORD, R1_RESIZE, R1_ATOMS };

typedef struct { uint32_t kind, reserved; uint64_t bits; } r1_atom;
typedef struct {
    uint32_t universe, payload_size;
    r1_atom timestamp;
    uint8_t data[512];
} r1_raw_frame;
typedef struct { uint32_t universe, reserved; int64_t channel; } r1_channel;
typedef struct { uint32_t kind, reserved; uint64_t bytes; const uint8_t* data; } r1_raw_domain;
typedef struct {
    uint64_t source_token;
    uint32_t universe, payload_size;
    uint8_t mask[512];
} r1_taken;
typedef struct {
    uint32_t abi, struct_size;
    uint32_t origin_mode, reserved; /* 0=raw range entry; 1=raw live seed→owned zero */
    uint64_t causal_revision, taken_count;
    r1_raw_domain domains[9]; /* absent=0, encoded exact dict=1, None=2 */
    const r1_taken* taken;
} r1_raw_state;
typedef struct { uint32_t kind, reserved; uint64_t bytes, offset; } r1_state_domain;
typedef struct {
    uint32_t abi, struct_size;
    uint32_t origin_mode, reserved;
    uint64_t allocation_bytes, complete_mask, causal_revision, taken_count, taken_offset;
    r1_state_domain domains[9]; /* C empty-dict=3; canonical Draw origin=4 */
} r1_state;
typedef struct {
    uint32_t abi, struct_size;
    uint64_t features, source_token, byte_budget, frame_count, channel_count;
    r1_atom atoms[R1_ATOMS];
    const r1_raw_frame *frames;
    const r1_channel *channels;
    const r1_raw_state *state;
} r1_input;
typedef struct {
    uint32_t abi, struct_size;
    int32_t status;
    uint32_t publishable;
    uint64_t pending_features, pending_state, source_token;
    uint64_t cursor, total_frames, streams, used_bytes, peak_bytes, kernel_reserved_bytes;
    double interval, end, last_time;
} r1_report;
typedef struct { uint32_t struct_size, max_frames; uint64_t max_block_bytes; } r1_step;
typedef struct r1_ledger { uint64_t limit, used, peak, refs; } r1_ledger;
typedef struct {
    uint32_t universe, reserved;
    uint64_t begin, count, sparse_begin;
    uint8_t base[512], previous[512];
    uint8_t minimum[512], maximum[512];
    float anchor[512];
    int seen, explicit_count, anchor_ready;
} r1_stream;
typedef struct { uint64_t source, slot; double time; } r1_sort_row;
typedef struct r3_frame_raw r3_frame_raw;
typedef struct {
    r1_ledger *ledger;
    r1_stream *streams;
    r1_state *state;
    double *times;
    uint8_t *data;
    uint64_t *change_words;
    int32_t *change_before;
    float *narrow_times;
    r1_raw_frame *raw_owned;
    const r3_frame_raw* scatter;uint32_t* data_order;uint64_t data_order_bytes;
    r1_channel *channels_owned;
    const r1_channel* channels_borrowed;
    r1_sort_row *sort_rows, *sort_scratch;
    uint32_t *compile_map;
    uint64_t raw_bytes, channels_bytes, sort_bytes, map_bytes;
    uint64_t compile_cursor, merge_width, merge_begin, merge_left, merge_right, merge_out;
    uint64_t sparse_blocks, sparse_word_bytes, sparse_before_bytes, narrow_bytes;
    uint64_t channels_n, pending_features;
    int32_t compile_last[512];
    uint64_t compile_slot;
    uint32_t compile_phase, warning_flags;
    uint64_t streams_n, records_n, cursor, total_frames, source_token;
    uint64_t streams_bytes, times_bytes, data_bytes;
    double start, duration, rate, source_duration, interval, end, track_order, last_time;
    double declared_source, cycle_first, cycle_period, cycle_last;
    int record, cancelled, resize, cycle_valid, extends_source, terminal_error;
} r1_job;
typedef struct {
    uint64_t magic, allocation_bytes;
    r1_ledger *ledger;
    uint32_t abi, struct_size;
    uint64_t first_index, frame_count, stream_count, source_token;
    uint64_t pending_state;
    uint32_t publishable, reserved;
    r1_state *state;
    uint32_t *universes;
    double *positions, *scores;
    uint8_t *main_data, *main_active, *record_data, *record_active;
    uint8_t *main_present, *record_present;
    /* Nonempty cursor/clock state; deliberately NOT a fake seven-domain exit. */
    uint64_t next_index;
    double last_time;
} r1_block;
#endif
