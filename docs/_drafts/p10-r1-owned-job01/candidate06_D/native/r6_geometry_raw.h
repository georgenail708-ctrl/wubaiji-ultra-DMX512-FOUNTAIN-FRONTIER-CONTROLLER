/* Raw authored geometry metadata. No positions/groups/spans computed by host. */
#ifndef CE_R6_GEOMETRY_RAW_H
#define CE_R6_GEOMETRY_RAW_H
typedef struct {
    r4_patch_fixture patch;r5_text name,source_id;r1_atom pos_x,pos_y,pos_z;
} r6_fixture_raw;
typedef struct {
    uint64_t token;uint32_t container_kind,count;r1_atom x,y;
} r6_snapshot_raw; /* 1=list,2=tuple; all other plain containers fail snapshot */
typedef struct {
    uint64_t token;uint32_t dict_kind,reserved;r1_atom x,y,pos_x,pos_y;
} r6_page_point_raw;
typedef struct {
    uint32_t dict_kind,visible_present,visible_kind,reserved;
    uint64_t visible_n,points_n;const uint64_t* visible;const r6_page_point_raw* points;
} r6_page_raw; /* visible kind1=list/tuple/set; None/empty is an empty valid set */
typedef struct {
    uint64_t fixtures_n,snapshot_n,pages_n;
    const r6_fixture_raw* fixtures;const r6_snapshot_raw* snapshot;const r6_page_raw* pages;
    r1_atom active_page;uint32_t use_snapshot,project_present;
    uint64_t external_limit; /* same canonical raw-owner charge as outer job */
} r6_coordinate_raw;
typedef struct {
    r1_ledger* ledger;const r6_coordinate_raw* raw;
    const uint64_t* native_ids;uint64_t n,bytes;
    double* xy;int64_t* fixture;uint8_t* snapshot_valid;
    uint64_t stage,row,scan,page,point,visible_scan,selected_snapshot;
    uint64_t active_page;uint32_t active_valid,complete_snapshot,has_snapshot,selected_visible,cancelled;
    uint64_t external_min;uint32_t page_admitted;
} r6_coordinates;
#endif
