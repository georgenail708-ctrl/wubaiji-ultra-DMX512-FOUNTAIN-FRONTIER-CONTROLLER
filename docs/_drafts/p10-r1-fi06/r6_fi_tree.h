#ifndef CE_RFI_TREE_H
#define CE_RFI_TREE_H
/* Lossless ORIGINAL metadata trees; current views must be built inside the
 * native source/context compiler, never by calling the old Python signature. */
enum {RFT_NONE=0,RFT_BOOL=1,RFT_INT=2,RFT_FLOAT=3,RFT_TEXT=4,RFT_BYTES=5,
      RFT_TUPLE=6,RFT_LIST=7,RFT_SET=8,RFT_FROZENSET=9,RFT_DICT=10};
typedef struct {
    uint32_t kind,reserved;uint64_t object_token,bits,child_first,child_count,data_first,data_count;
} rfi_tree_node;
typedef struct {
    uint64_t root,identity_space,nodes_n,edges_n,data_n;
    const rfi_tree_node* nodes;const uint64_t* edges;const uint8_t* data;
} rfi_tree_view;
typedef struct {
    uint64_t a,b,cursor,search;uint32_t phase,unordered;int result,reserved;
} rfi_eq_frame;
typedef struct {rfi_eq_frame* frames;uint64_t capacity;} rfi_tree_work;
typedef struct {
    rfi_tree_view context,motor_context,permit,attachment;
    uint32_t have_permit,spatial_qualified,have_attachment,reserved;
} rfi_current_context;
/* Complete original round6/static/filter/layout and motor tuple in C. This
 * actual implementation is pending in the main CE job compiler. */
static int r6_fi_current_context(r6_transition_owner* owner,uint64_t target,
                               uint32_t universe,rfi_current_context* out);
typedef struct {
    uint64_t source_token;uint32_t universe,reserved;
    rfi_tree_view context,motor_context,proof_schema,proof_slot,proof_values;
    uint32_t have_proof,geometry_reserved;uint64_t deps_n;const uint64_t* deps;
    const float* anchor; /* Original exact float32[512], raw bytes only. */
} rfi_legacy_row;
typedef struct {
    uint64_t rows_n,revision,payload_bytes;const rfi_legacy_row* rows;
    const uint8_t* payload; /* Entire original dict pickle, lossless IO retention. */
    rfi_tree_work work; /* Precharged owning workspace, not arbitrary callbacks. */
} rfi_legacy_table;
typedef struct {uint64_t index;uint32_t cached,changed;} rfi_legacy_decision;
typedef struct {
    rfi_legacy_table table;r1_ledger* ledger;uint64_t bytes;
    uint8_t* changed; /* Original map order/payload survives context mismatch. */
} rfi_legacy_owner;
#endif
