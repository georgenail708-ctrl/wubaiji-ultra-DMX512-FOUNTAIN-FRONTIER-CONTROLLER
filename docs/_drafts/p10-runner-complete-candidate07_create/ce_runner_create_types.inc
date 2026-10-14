/* MIT project successor. Include after the unchanged complete R06 include. */
enum { RR_CREATE_DIRECT=0,RR_CREATE_V2=1,RR_CREATE_LEGACY=2,RR_CREATE_PARTIAL=3,RR_CREATE_PANEL=4 };
typedef struct {
    uint32_t operation; const char* id; const char* color;
    const unsigned char* count_bytes; size_t n_count;
    double car_length,radius,base_speed,car_unit;
    const rr_point* points; int n_points; size_t host_bytes;
    uint32_t anticipated_attach; int anticipated_points;
} rr_create_request;
typedef struct {
    rr_edit_report edit;
    uint32_t count_selector,count_fits,phase; size_t host_bytes;
} rr_create_report;
typedef struct rr_create_slot {
    rr_context* owner; unsigned long long token; size_t previous_external;
    rr_create_report report; struct rr_create_slot* link;
} rr_create_slot;
static rr_create_slot* rr_create_pending;
