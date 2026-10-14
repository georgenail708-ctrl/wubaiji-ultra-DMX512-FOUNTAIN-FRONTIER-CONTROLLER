#ifndef CE_R4_TYPES_H
#define CE_R4_TYPES_H
#define R4_ABI 4
#define R4_PENDING_GRID UINT64_C(2048)
#define R4_PENDING_ALIAS UINT64_C(4096)
#define R4_PENDING_CACHE_SIGNATURE UINT64_C(8192)
typedef struct {uint32_t universe,key_kind;uint64_t count;const r4_channel_raw* channels;} r4_channels_raw;
typedef struct {uint32_t universe,key_kind,present,reserved;uint64_t count;const r4_channel_raw* channels;} r4_filter_raw;
typedef struct {
    uint64_t active_domains_n,mask_domains_n,mask_events_n,base_ids_n,filter_domains_n;
    const r4_channels_raw* active_domains;const r4_domain_raw* mask_domains;
    const uint64_t* base_ids;const r4_filter_raw* filter_domains;
} r4_clip_raw;
typedef struct {uint32_t mode,reserved;r1_atom start,stop,interval,fps,end;} r4_render_request;
typedef struct {
    uint32_t abi,struct_size;r3_input raw;r4_render_request request;
    const r4_clip_raw* clip_masks;
    uint64_t patch_fixtures_n,patch_definitions_n;
    const r4_patch_fixture* patch_fixtures;const r4_patch_definition* patch_definitions;
} r4_input;
typedef struct {
    const r4_domain *event_domain,*integer_events,*text_events;
    r4_rule rule;uint8_t base_order[512],active_partial[512];
    uint64_t lt_fx_bytes,lt_rows;
    uint32_t base_present,explicit_present,active_error;
    uint8_t owned[512];uint32_t owned_explicit;
} r4_slot;
typedef struct {
    r4_recorded events;r4_slot* slots;uint64_t slots_bytes;
    uint64_t source_begin,sparse_begin;uint32_t source_phase;
} r4_clip;
typedef struct r4_job {
    r3_job* scene;const r4_input* borrowed;r4_tracks tracks;r4_clip* clips;
    uint64_t clip_bytes,phase,row,subrow,scan,selected,byte_cursor;
    uint64_t external_min;
    uint64_t slot,fixture,definition,channel,source_clip,source_slot,universe,stream_begin,sparse_begin;
    uint64_t lt_count,lt_fill,seed_row,seed_slot;
    uint32_t subphase,cancelled;int status;
    uint8_t* defaults_seen;uint64_t defaults_seen_bytes;
    r4_base_mask base;r4_pair_mask pairs;r4_rule active;
    const r4_channels_raw* selected_active;const r4_filter_raw* selected_filter;
    const r4_channels_raw* selected_owner;
} r4_job;
CE_API void ce_owned_scene_destroy_r4(r4_job* job);
CE_API int ce_owned_scene_release_r4(r3_block* block);
static void r4_report_current(r4_job* job,r3_report* report,int status);
static int r4_external_metadata(const r4_input* input,uint64_t* bytes);
#endif
