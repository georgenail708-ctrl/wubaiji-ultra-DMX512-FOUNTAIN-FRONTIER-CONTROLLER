#ifndef CE_R6_TRANSITION_CONTRACT_H
#define CE_R6_TRANSITION_CONTRACT_H
/* Internal fixed C symbol seam for the independently owned FI06 source port.
 * Never a function pointer, Python callback or host-prepared previous frame.
 * A null/missing implementation is pending and cannot seal a full CE job. */
struct r6_scene_plan;
typedef struct {
    r5_job* timeline;struct r6_scene_plan* geometry;uint64_t source_epoch;
} r6_transition_owner;

typedef struct {
    uint64_t previous_clip;double timeline_t;uint32_t universe,ignore_fade_out;
} r6_transition_request;

typedef struct {
    float data[512];uint8_t active[512];uint32_t present,source_slot;
    double local_t,source_t;uint32_t hold_extension,reserved;
} r6_transition_output;

/* Matches the original _sample_transition_clip_output route, which does not
 * apply the main sequential copy map or main raw LT apply. Actual filters/
 * audio effects belong to this call; light_mask contributes ownership only.
 * Caller scores use endpoint source last_change
 * separately; do not infer scores from this data or timeline_t. */
static int r6_transition_sample(r6_transition_owner* owner,const r6_transition_request* request,
                                 r6_transition_output* output);
#endif
