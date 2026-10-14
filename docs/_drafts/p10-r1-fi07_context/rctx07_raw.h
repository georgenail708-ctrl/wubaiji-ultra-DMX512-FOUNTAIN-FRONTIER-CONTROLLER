#ifndef RCTX07_RAW_H
#define RCTX07_RAW_H
/* Mechanical enumeration of original fields/containers only. No ready tuple,
 * sorted key, chosen filter, candidate list, numerical hash or permit bool. */
enum {RC07_MISSING=0,RC07_NONE=1,RC07_BOOL=2,RC07_I64=3,RC07_F64=4,
      RC07_TEXT=5,RC07_INTEGER_BYTES=6,RC07_FOREIGN=9};
enum {RC07_ABSENT=0,RC07_NULL=1,RC07_LIST=2,RC07_TUPLE=3,RC07_SET=4,
      RC07_DICT=5,RC07_SCALAR=6,RC07_FOREIGN_CONTAINER=9};
typedef struct {
    uint32_t kind,reserved;uint64_t bits,object_token,bytes;const uint8_t* data;
} rctx07_value; /* INTEGER_BYTES: original signed little-endian two's complement */
typedef struct {uint32_t kind,reserved;uint64_t n;const rctx07_value* rows;} rctx07_values;
typedef struct {
    rctx07_value key,x,y;uint32_t value_kind,reserved;uint64_t original_length;
} rctx07_snapshot;
typedef struct {rctx07_value key;rctx07_values channels;} rctx07_active;
typedef struct {
    uint32_t kind,reserved;uint64_t object_id;rctx07_values channels;
} rctx07_rule; /* kind0 None,1 exact original FilterRule,9 foreign */
typedef struct {rctx07_value key;rctx07_rule rule;} rctx07_rule_entry;
typedef struct {
    uint64_t object_id,source_token,containing_track,frame_count;
    rctx07_value id,start,duration,hold_mode,hold_last,filters[3];
    rctx07_values universes,base_ids;
    uint32_t snapshot_kind,active_kind,id_assignable,reserved;
    uint64_t snapshot_n,active_n;
    const rctx07_snapshot* snapshot;const rctx07_active* active;
} rctx07_clip;
typedef struct {
    uint64_t object_id; rctx07_value id,parent,folder,muted,solo;
    uint32_t clips_kind,rules_kind;uint64_t clips_n,rules_n;
    const uint64_t* clips; /* exact unsorted original Track.clips, all authored rows */
    const rctx07_rule_entry* rules;rctx07_rule fallback_rule;
} rctx07_track;
typedef struct {
    rctx07_value fixture_id,x,y,x_fine,y_fine,angle,mode;
} rctx07_xy_ref;
typedef struct {rctx07_value universe;uint32_t kind,reserved;uint64_t n;const rctx07_xy_ref* rows;} rctx07_xy;
typedef struct {
    uint32_t present,audio_author,audio_chain,reserved;
    uint64_t source_owner,publication_owner;
    /* Raw existing published token and engine _audio_mod_signature fields.
     * Their origin is checked by ROOT, never constructed by a Python helper. */
    rfi_tree_view token,audio_original;
    rctx07_value interval;
    uint32_t xy_kind,source_spatial_kind;uint64_t xy_n;const rctx07_xy* xy;
} rctx07_certificate;
typedef struct {
    uint32_t abi,size,project_present,tracks_kind;
    uint64_t identity_space,source_owner,external_bytes,clips_n,tracks_n;
    const rctx07_clip* clips;const rctx07_track* tracks;
    rctx07_certificate certificate;
    uint32_t int_max_str_digits,reserved;
} rctx07_raw;
#define RCTX07_ABI 7
#endif
