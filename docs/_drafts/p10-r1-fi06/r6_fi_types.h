#ifndef CE_R6_FI_TYPES_H
#define CE_R6_FI_TYPES_H
/* Include after actual R1/R3/R4/R5 types and r6_transition_contract.h.
 * Internal C component only: no function-pointer/Python sample callbacks. */
#define RFI_ABI 1
enum {RFI_IDLE=0,RFI_METADATA=1,RFI_COUNT=2,RFI_ALLOCATE=3,RFI_FILL=4,RFI_SEED=5,RFI_PREWARM=6,RFI_READY=7};
enum {RFI_NO_ERROR=0,RFI_UNIVERSE_INT_NAN=1,RFI_UNIVERSE_INT_INF=2,RFI_UNIVERSE_INT_NONE=3};
typedef struct {uint64_t clip;uint32_t universe,reserved;} rfi_target;
typedef struct {uint64_t count;const r1_atom* values;} rfi_universes_raw;
typedef struct {
    uint64_t containing_track,object_token;
    rfi_universes_raw universes;
    r1_atom filters[3]; /* motor_limit_scale, speed_dps, pump_master_scale */
} rfi_clip_raw;
typedef struct {
    rfi_universes_raw universes;r1_atom universe;
} rfi_track_raw;
typedef struct {
    uint32_t abi,size,history_kind,reserved; /* 0=admitted empty,1=native seed,9=unknown */
    uint64_t source_epoch,context_owner,clip_count,track_count;
    const rfi_clip_raw* clips;const rfi_track_raw* tracks;
    const struct rfi_history* history;
} rfi_input;
typedef struct {uint64_t clip,track_rank;uint32_t slot,reserved;} rfi_reference;
typedef struct {
    uint64_t target,first,count;uint32_t universe,causal,ready,building,ordinary,reserved;
    uint64_t dep_first,deps_count,revision;
    float anchor[512];double best[512];
} rfi_entry;
typedef struct {
    uint64_t target,source_token;uint32_t universe,causal;
    uint64_t dep_first,dep_count,revision;
    float anchor[512];
} rfi_history_entry;
typedef struct rfi_history {
    uint32_t abi,size;uint64_t magic,bytes,source_epoch,context_owner,revision,entries,dependencies,clips;
    uint64_t entry_capacity,dependency_capacity;
    r1_ledger* ledger;rfi_history_entry* rows;uint64_t* deps;
    uint64_t* filter_tokens;double* filters;
} rfi_history;
typedef struct {
    r6_transition_owner owner;const rfi_input* borrowed;
    const rfi_target* targets; /* Actual native-produced output domain inventory. */
    r1_ledger* ledger;r4_tracks* tracks;
    rfi_entry* entries;rfi_reference* refs;uint64_t* deps;
    double* filters; /* C-normalized [clip_count][3], part of immutable context */
    uint64_t entries_n,entries_bytes,refs_n,refs_capacity,refs_bytes;
    uint64_t deps_bytes,filters_bytes,source_epoch,context_owner,revision;
    uint64_t target,stream,track_cursor,clip_cursor,entry_cursor,ref_cursor;
    uint64_t history_cursor,copied_entries,copied_deps,external_min;
    uint32_t phase,cancelled,semantic,eligible_tracks,using_fallback;
    uint64_t fallback_track;int status;
    r6_transition_output sample; /* One owned scratch result, never prior grid output. */
} rfi_plan;
static void rfi_destroy(rfi_plan* plan);
static int rfi_anchor_step(rfi_plan* plan,uint64_t entry,uint32_t units,const float** output);
#endif
