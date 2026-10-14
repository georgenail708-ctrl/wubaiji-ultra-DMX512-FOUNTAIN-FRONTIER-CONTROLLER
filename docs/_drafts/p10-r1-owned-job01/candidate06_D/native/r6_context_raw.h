#ifndef CE_R6_CONTEXT_RAW_H
#define CE_R6_CONTEXT_RAW_H
/* NEW raw context input, never a host-built signature/context. Text tokens are
 * structural identities shared with the raw source/model codec. Numeric casts,
 * rounding, deduplication, sorting and tuple construction belong to CE. */
typedef struct {uint64_t token;r5_text text;} r6_context_id;
typedef struct {
    r4_channel_raw universe;uint32_t container_kind,reserved;
    uint64_t channels_n;const r4_channel_raw* channels;
} r6_context_channels;
typedef struct {
    uint64_t token;uint32_t kind,reserved;r4_channel_raw x,y;
    uint64_t length; /* original list/tuple len, before the >=2 selector */
} r6_context_snapshot;
typedef struct {
    uint64_t source_token,frame_count,base_ids_n,universe_n,snapshot_n,active_n;
    const uint64_t* base_ids;const r4_channel_raw* universes;
    const r6_context_snapshot* snapshot;const r6_context_channels* active;
    r4_channel_raw start,duration;
    uint32_t active_kind,snapshot_kind,base_kind,universes_kind;
} r6_context_clip;
typedef struct {
    uint64_t token,parent_token,clips_n;const uint64_t* clips;
    r1_atom folder,muted,solo;
    uint32_t list_kind,reserved; /* exact original LIST, not DFS order */
} r6_context_track;
typedef struct {
    uint64_t track,universe_token;uint32_t present,empty,all,reserved;
    r4_channel_raw universe;uint64_t channels_n;const r4_channel_raw* channels;
} r6_context_filter;
typedef struct {
    uint64_t source_token,owner_token,publication_token;
    uint32_t present,source_kind,spatial_kind,reserved;
    /* Raw published source metadata. ROOT admission must bind these identities
     * to the actual immutable source/proof owner. No caller-provided true flag
     * establishes that owner, and no Python signature is accepted here. */
    uint64_t runtime_n;const r4_channel_raw* runtime;
} r6_context_certificate;
typedef struct {
    uint32_t abi,size;uint64_t identity_space,source_owner;
    uint64_t ids_n,clips_n,tracks_n,filters_n,certificates_n,external_bytes;
    const r6_context_id* ids;const r6_context_clip* clips;
    const r6_context_track* tracks;const r6_context_filter* filters;
    const r6_context_certificate* certificates;
} r6_context_raw;
struct r6_context_program;
#endif
