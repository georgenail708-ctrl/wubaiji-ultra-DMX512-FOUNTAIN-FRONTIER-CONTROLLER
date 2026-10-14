#ifndef RCTX07_TYPES_H
#define RCTX07_TYPES_H
enum {RC07_COUNT=0,RC07_ALLOCATE=1,RC07_CLIPS=2,RC07_TRACKS=3,
      RC07_RUNTIME=4,RC07_TARGETS=5,RC07_READY=6};
enum {RC07_C_NONE=0,RC07_C_FOREIGN=1,RC07_C_BAD_INT=2,RC07_C_BAD_FLOAT=3,
      RC07_C_ID_UUID=4,RC07_C_LONG_NUMERIC=5,RC07_C_SORT_TYPE=6,
      RC07_C_AUDIO_RUNTIME=7,RC07_C_SOURCE_CERTIFICATE=8,RC07_C_RAW_ASSOCIATION=9};
enum {RC07_RAW_LEAF=0,RC07_STRING=1,RC07_INT=2,RC07_FLOAT=3,RC07_FLOAT3=4,
      RC07_FLOAT6=5,RC07_STRING_OR_EMPTY=6,RC07_STRING_OR_ID=7};
typedef struct {uint64_t node,key,index;} rctx07_item;
typedef struct {
    uint64_t id,motor_id,start,duration,hold,filters[3],frames,auth_universes;
    uint64_t sorted_universes,base,snapshot,active,static_root,layout;
} rctx07_clip_plan;
typedef struct {uint64_t id,parent,state,clips,layout;} rctx07_track_plan;
typedef struct {uint64_t target,context,motor;uint32_t universe,reserved;} rctx07_row;
typedef struct {
    const rctx07_value* raw;uint64_t node,cursor,fallback;
    uint32_t mode,active,or_zero,done;uint8_t local[256];uint64_t local_n;
} rctx07_scalar;
typedef struct {
    uint64_t cursor,walk,key;uint32_t active,comparing;int comparison;
} rctx07_sort;
typedef struct {uint64_t a,b,child,byte;uint32_t phase;int result;} rctx07_cmp_frame;
typedef struct r6_context_program {
    uint64_t magic,bytes,refs,source_owner,identity_space;
    r1_ledger* ledger;void* allocation;
    rfi_tree_node* nodes;uint64_t* edges;uint8_t* data;
    uint64_t nodes_n,edges_n,data_n,node_cap,edge_cap,data_cap;
    rctx07_clip_plan* clips;rctx07_track_plan* tracks;rctx07_row* rows;
    uint64_t clips_n,tracks_n,rows_n,layout,runtime,permit;
    uint32_t ready,permit_status,semantic,reserved;
} rctx07_program;
typedef struct {
    rctx07_program* program;const rctx07_raw* raw;const rfi_plan* candidates;
    r6_transition_owner owner;r1_ledger* ledger;
    rctx07_item *items,*outer;uint64_t item_capacity,item_bytes;
    rctx07_scalar scalar;rctx07_sort sort;
    rctx07_cmp_frame compare[8];uint32_t compare_depth;int compare_result;
    uint8_t numeric_a[129],numeric_b[129];
    uint64_t phase,subphase,row,scan,member,leaf,temporary,root,inner,index;
    uint64_t nodes_bound,edges_bound,data_bound,external_min,values_count,largest;
    uint64_t count_phase,count_row,count_sub,count_member;
    uint64_t sort_data_cursor; /* bytewise comparison prefix, reset per pair */
    uint64_t child_roots[12];uint8_t seen[512];
    uint32_t cancelled,semantic,branch_error,kind;int status;
} rctx07_job;
#define RCTX07_MAGIC UINT64_C(0x5243545830374152)
#endif
