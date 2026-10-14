/* Incremental A/C component. Owned R4 job embeds this C cursor, not a host
 * pre-sort service. Inputs are copied raw track atoms after budget admission. */
typedef struct {uint64_t parent,next;} r4_dfs_level;
typedef struct {
    r1_ledger* ledger;
    r3_track_raw* tracks;
    const r3_track_raw* borrowed;
    int64_t *parents,*order,*rank;
    uint8_t *emitted,*muted,*solo,*allowed;
    uint64_t* visits;
    r4_dfs_level* stack;
    int64_t *new_order;uint64_t order_bytes,capacity,new_capacity,copy_cursor;
    uint32_t resume_phase;
    uint64_t count,bytes,cursor,scan,out,depth,walk,tag;
    int64_t last_match;
    uint32_t phase,any_solo,cancelled,semantic;
} r4_tracks;

enum {R4_TRACK_IDS=0,R4_TRACK_PARENT=1,R4_TRACK_DFS=2,R4_TRACK_ORPHANS=3,
      R4_TRACK_ANCESTORS=4,R4_TRACK_SOLO=5,R4_TRACK_ALLOW=6,R4_TRACK_READY=7,R4_TRACK_GROW=8};

/* Missing/duplicate authored IDs are represented; a rooted recursive folder
 * cycle selects the original RecursionError domain rather than pretending an
 * arbitrary white list or returning a truncated successful order. */
#define R4_RECURSIVE_FOLDER 1
