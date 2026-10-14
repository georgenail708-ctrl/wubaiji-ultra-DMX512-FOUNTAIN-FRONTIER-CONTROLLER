/* B1: transport codec may supply original packed64 bytes. Bit unpack, source
 * lookup, source-duration boundary, ownership and rule math remain C. */
typedef struct {r1_atom timestamp;uint32_t bytes,reserved;uint8_t bits[64];} r4_event_raw;
typedef struct {
    uint32_t universe,key_kind; /* 0=int key, 1=exact decimal string key */
    uint64_t count;const r4_event_raw* events;
} r4_domain_raw;
typedef struct {
    double timestamp;uint8_t bits[64];
} r4_event;
typedef struct {
    uint32_t universe,key_kind;uint64_t begin,count;
} r4_domain;
typedef struct {
    r1_ledger* ledger;r4_domain* domains;r4_event* events;
    const r4_domain_raw* borrowed;
    uint64_t domains_n,events_n,domain_bytes,event_bytes,domain_cursor,event_cursor,out;
    uint32_t cancelled,semantic;
} r4_recorded;
enum {R4_MASK_MISSING=0,R4_MASK_BEFORE_FIRST=1,R4_MASK_SELECTED=2};
typedef struct {uint32_t kind,reserved;int64_t event;const r4_event* selected;} r4_mask_selection;
typedef struct {double source,declared;uint32_t completed,reserved;} r4_mask_time;
