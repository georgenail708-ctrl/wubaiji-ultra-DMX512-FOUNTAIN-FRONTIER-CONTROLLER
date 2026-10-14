#ifndef CE_R6_SCENE_TYPES_H
#define CE_R6_SCENE_TYPES_H
#define R6_ABI 6
#define R6_PENDING_SEGMENTS UINT64_C(262144)
#define R6_PENDING_ATTRIBUTES UINT64_C(524288)
#define R6_PENDING_TRANSITION UINT64_C(1048576)
#define R6_PENDING_CONTEXT UINT64_C(2097152)
enum {R6_COMPILE_GRID=0,R6_COMPILE_TIMELINE=1,R6_COMPILE_GEOMETRY=2,
      R6_COMPILE_CONTEXT=3,R6_READY=4};

typedef struct {
    r6_coordinate_raw coordinates;
    const r6_definition_raw* definitions;uint64_t definitions_n;
    const r6_group_raw* groups;uint64_t groups_n;
    const r6_shape_page_raw* shapes;uint64_t shape_pages_n;
    r1_atom skip_base_order;
    uint64_t overrides_n,curve_points_n,ratio_in_n,ratio_out_n,force_points_n;
    uint32_t decimal_limit,reserved;
} r6_clip_geometry_raw;

typedef struct {
    uint32_t abi,struct_size;r5_input raw;
    const r6_clip_geometry_raw* geometry;
    const r6_context_raw* context; /* original raw supplier; never a ready tuple */
    uint64_t model_epoch,fixture_epoch;
    int32_t main_timer_ms;uint32_t main_timer_present; /* actual Qt metadata */
} r6_input;

typedef struct {
    r6_order source_order,local_order;
    r6_global_groups in_groups,out_groups;
    r6_source_traits traits;r6_motor motor;r6_copy_plan copies;r6_sample_cache samples;
    uint64_t bytes,local_ids_bytes;uint64_t* local_ids;float* direction_scratch;
    int span_index[512];uint8_t completed[512],arrived[512],prearrival[512];
    uint32_t phase,in_ready,out_ready,copy_ready;
    uint64_t row,channel;uint32_t scalar_only;
} r6_slot_plan;

typedef struct {
    r6_base_order base;r6_coordinates coordinates;r6_virtual virtual_positions;r6_shapes shapes;
    r6_slot_plan* slots;r6_used_slot* used;r6_source_span_slot* source_slots;
    uint64_t slots_bytes,used_bytes,source_slots_bytes;
    r6_fixture_raw* filtered;uint64_t filtered_bytes,filtered_n;
    uint64_t phase,row,slot,scan;uint32_t directions_ready,found,scalar_only;
} r6_clip_plan;

typedef struct r6_scene_plan {
    r6_clip_plan* clips;uint64_t clips_n,bytes;
} r6_scene_plan;

typedef struct {
    r5_job* timeline;const r6_input* borrowed;r6_scene_plan geometry;
    r6_transition_owner transition;
    G0Grid grid;G0CompileState* grid_state;uint64_t grid_state_bytes;
    const r3_clip_raw* authored_rows;uint64_t authored_n,source_epoch,model_epoch,fixture_epoch;
    uint64_t phase,row,slot,scan,external_min;uint32_t cancelled;int status;
    uint32_t semantic,grid_ready;
} r6_job;

enum {R6_SEMANTIC_NONE=0,R6_SEMANTIC_CEIL_INFINITY=1};
typedef struct {r3_report base;uint32_t semantic,grid_ready;uint64_t grid_units;} r6_report;

CE_API void ce_owned_scene_destroy_r6(r6_job* job);
CE_API int ce_owned_scene_release_r6(r5_block* block);
#endif
