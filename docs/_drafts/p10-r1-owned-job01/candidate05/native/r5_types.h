#ifndef CE_R5_TYPES_H
#define CE_R5_TYPES_H
#define R5_ABI 5
#define R5_PENDING_STATE UINT64_C(16384)
#define R5_PENDING_GEOMETRY UINT64_C(32768)
#define R5_PENDING_SOURCE_REMAP UINT64_C(65536)
#define R5_PENDING_PRECHARGE_IO UINT64_C(131072)
enum {R5_HOLD_NONE,R5_HOLD_FRAME,R5_HOLD_CYCLE};
enum {R5_RUN_NORMAL,R5_RUN_REVERSE,R5_RUN_PINGPONG,R5_RUN_LOOP,R5_RUN_ONCE};
enum {R5_GROUPS,R5_BATCHES,R5_PAUSE,R5_SINGLE,R5_DELAY_IN,R5_DELAY_OUT,R5_PHASE,
      R5_HOLD_LAST,R5_MOTOR_PHASE,R5_PRECHARGE,R5_LEAD_MS,R5_LEVEL,R5_NUMBERS};
typedef struct {uint32_t kind,reserved;uint64_t bytes;const uint8_t* utf8;} r5_text;
typedef struct {
    r5_text hold,run,sequence,direction_in,direction_out,direction_legacy,spatial_scope;
    r1_atom values[R5_NUMBERS];uint64_t pauses_n;const r1_atom* pauses;
    uint64_t clip_universes_n;const r1_atom* clip_universes;
    uint64_t pending_feature_atoms;
} r5_clip_raw;
typedef struct {
    uint64_t token; r1_atom x,y,z;uint64_t shape_token;
} r5_fixture_geometry_raw;
typedef struct {
    uint64_t source_token,track_order;uint32_t universe,mask_present;
    uint8_t mask[512];uint32_t outer_present,reserved;
} r5_hold_state;
typedef struct {uint64_t source_token,track_order;uint32_t present,value;} r5_latch_state;
typedef struct {
    uint32_t abi,struct_size;r4_input raw;const r5_clip_raw* phases;
    uint64_t geometry_n;const r5_fixture_geometry_raw* geometry;
    uint32_t source_epoch_mode,nm_present; /* actual caller presence, not simulation */
    uint64_t hold_n,latch_n;const r5_hold_state* hold;const r5_latch_state* latch;
} r5_input;
typedef struct {
    uint64_t bytes;uint8_t *hold_mask,*hold_seen;
    uint8_t hold_latch,hold_latch_present,hold_extension,held,eligible;
    uint8_t hold_outer_present;
    uint8_t real_hold,raw_dir_in_set;
    uint32_t hold,run,groups,batches,sequential,wing,dir_in,dir_out,motor_no_delay;
    double *pauses;uint64_t pause_count,pause_bytes;
    double single,step,delay_in,delay_out,pause_fallback,local,source,gain,interval;
    uint32_t in_pause,cyclic,primary_universe,primary_slot;
    uint32_t compile_stage,pause_cursor,cross_channel;
    uint32_t channel;uint64_t cross_sum;
    uint64_t sample_count,sample_cursor,sample_step;
    uint8_t min_value[512],max_value[512],last_sample[512];
    uint32_t crossings[512];uint64_t sample_bytes;
    uint32_t precharge,level;double lead,trigger;
    float* sampled;uint64_t sampled_bytes;
    int64_t group_id;
} r5_clip;
typedef struct r5_job {
    r4_job* base;const r5_input* borrowed;r5_clip* clips;
    uint64_t clip_bytes,phase,row,subphase,scan,slot,channel;
    uint64_t bytes;double timer_ms;uint32_t cancelled;int status;
    uint8_t *covered;uint64_t coverage_bytes;
    int64_t *active_order,*hold_order,*body_order;uint64_t order_bytes;
    uint64_t active_n,hold_n;
    uint64_t external_min,seed_cursor,seed_clip,seed_slot,sort_cursor,sort_walk;
    int64_t sort_value;
} r5_job;
typedef struct {
    uint32_t abi,struct_size;uint64_t bytes,clip_count,slots_count;
    r5_hold_state* hold;r5_latch_state* latch;
} r5_history;
typedef struct {
    uint64_t magic,bytes;r1_ledger* ledger;r3_block* frames;r5_history* history;
} r5_block;
CE_API void ce_owned_scene_destroy_r5(r5_job* job);
CE_API int ce_owned_scene_release_r5(r5_block* block);
#endif
