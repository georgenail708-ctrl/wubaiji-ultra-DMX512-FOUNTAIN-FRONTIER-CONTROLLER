/* R1-03 scene ABI: raw authored arrays, not host PreparedTask/LT scope blobs.
 * C-side capability gates are explicit. No Python callbacks in any ABI. */
#ifndef CE_R3_TYPES_H
#define CE_R3_TYPES_H
#define R3_ABI 3
#define R3_WORKFLOW_MAX_BYTES UINT64_C(1342177280) /* future safety window <=1.25 GiB */
#define R3_PENDING_MODEL UINT64_C(128)
#define R3_PENDING_LTHISTORY UINT64_C(256)
#define R3_PENDING_FADEIN UINT64_C(512)
#define R3_PENDING_PACKAGE UINT64_C(1024)
enum {R3_FI,R3_FO,R3_LT,R3_THRESHOLD,R3_ATTACK,R3_RELEASE,R3_BRIGHTNESS,R3_ENABLED,R3_EXTRA};
enum {R3_OTHER,R3_PUMP,R3_VALVE,R3_R,R3_G,R3_B,R3_W,R3_MOTOR};
struct r3_frame_raw {uint32_t universe,payload_size;r1_atom timestamp;const uint8_t* payload;};
typedef struct {uint64_t token,parent;uint32_t folder,muted,solo,reserved;} r3_track_raw;
typedef struct {
    uint64_t token; uint32_t channel_count,reserved;
    const uint32_t* channel_kinds; /* exact canonical type enum metadata */
} r3_definition_raw;
typedef struct {
    uint64_t token,definition,package_token;
    uint32_t universe,reserved;r1_atom start_channel;
} r3_fixture_raw;
typedef struct {
    uint32_t universe,data_size,active_size,reserved;
    uint8_t data[512],active[512]; /* raw NetworkManager defaults, never idle target */
} r3_default_raw;
typedef struct {
    uint64_t track_token,features;
    uint64_t channel_presence_count;const uint32_t* channel_presence;
    r1_input recorded;
    const r3_frame_raw* scatter; /* pinned raw source owner until native compile READY */
    r1_atom extra[R3_EXTRA];
    uint32_t source_kind,curve_in,curve_out,strategy_in,strategy_out;
    uint32_t color_is_white,red,green,blue,reserved;
} r3_clip_raw;
typedef struct {
    uint32_t abi,struct_size;
    uint64_t byte_budget,external_bytes,source_token,clip_count,track_count,fixture_count,definition_count,default_count;
    r1_atom timer_ms,modulation_gain;
    const r3_clip_raw* clips;const r3_track_raw* tracks;
    const r3_fixture_raw* fixtures;const r3_definition_raw* definitions;
    const r3_default_raw* defaults;const r1_raw_state* state;
    uint32_t lt_empty_mask,nm_present; /* exact input presence/emptiness metadata */
} r3_input;
typedef struct {
    _lt_scope scope;int32_t* fx_rows;
    uint8_t light[512],owned[512];
} r3_lt_slot;
typedef struct {
    r1_job* source;
    uint64_t track_index,chain_token;
    double fi,fo,attack,release;
    int threshold,brightness,lt,enabled,source_kind,allowed;
    int curve_in,curve_out,strategy_in,strategy_out,white,red,green,blue;
    uint8_t *consumed,*consumed_seen;
    uint32_t* channel_presence;uint64_t presence_n,presence_bytes,consumed_bytes,lt_bytes;
    r3_lt_slot* lt_slots;
} r3_clip;
typedef struct {
    uint64_t fixture_token;uint32_t universe,source_kind;
    double values[2],times[2];uint8_t exists[2],color[3],color_valid;
} r3_lt_value;
typedef struct {
    uint32_t abi,struct_size;int32_t status;uint32_t publishable;
    uint64_t pending_features,pending_state,source_token,compile_clip,cursor,total_frames,streams;
    uint64_t used_bytes,peak_bytes,compile_phase,warning_flags;
    double interval,end,last_time;
} r3_report;
typedef struct r3_job {
    r1_job clock; /* same ledger owns all child jobs, buffers and independent blocks */
    r3_clip* clips;r3_track_raw* tracks;r3_fixture_raw* fixtures;
    r3_definition_raw* definitions;
    r3_default_raw* defaults;uint64_t defaults_n,defaults_bytes;
    double *lt_state,*lt_time;uint8_t *lt_exists,*lt_color,*lt_color_valid,*lt_touched,*lt_touched_color;
    uint64_t clips_n,tracks_n,fixtures_n,definitions_n,compile_clip;
    uint64_t clips_bytes,tracks_bytes,fixtures_bytes,definitions_bytes,lt_bytes;
    uint32_t* universe_map;uint64_t map_bytes;
    float* idle;uint64_t idle_bytes;
    double modulation_gain;
    uint64_t pending_features,pending_state;
    uint64_t external_bytes;
    uint32_t compile_phase;
    uint32_t lt_empty_mask,nm_present;
    uint32_t lt_used;
    uint64_t slots_n;
    ce_prepared_task* tasks;
    uint8_t *candidate_data,*candidate_active,*lt_raw,*effective,*data_eff,*mix_scratch;
    float *candidate_float,*anchors,*gains,*changes;
    double* scores;int *flat,*offsets,*counts,*winner;
    uint64_t *row_clip,*row_stream;
    uint64_t work_bytes,flat_bytes,offset_bytes,count_bytes,winner_bytes,row_bytes;
    void* work;
    uint64_t active_count;int64_t* order;uint64_t order_bytes;
} r3_job;
typedef struct {uint64_t slot;double local;const uint8_t* input;uint8_t* output;} r3_lt_call;
typedef struct {
    uint32_t abi,struct_size;uint64_t allocation_bytes,clip_slots,fixtures;
    uint64_t complete_mask; /* scoped representation, not product publication */
    r1_taken* consumed;
    uint64_t* fixture_tokens;uint32_t* fixture_universes;
    double *lt_state,*lt_time;
    uint8_t *lt_exists,*lt_color,*lt_color_valid;
} r3_history;
typedef struct {
    uint64_t magic,allocation_bytes;r1_ledger* ledger;
    r1_block* frames;r3_history* history;
} r3_block;
#endif
