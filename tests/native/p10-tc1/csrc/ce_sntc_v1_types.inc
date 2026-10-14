/* Independent SNTC v1 types. Production composition is source-only;
 * runtime/source/ABI acceptance remains pending. */
#include <stdint.h>
enum { QTC_ABI=1, QTC_OK=0, QTC_BAD_ABI=1, QTC_BAD_LENGTH=2,
       QTC_BAD_MAGIC=3, QTC_BAD_CHECKSUM=4, QTC_BAD_RANGE=5,
       QTC_BAD_STATUS=6, QTC_BAD_TIME=7, QTC_OTHER_FRESH_SOURCE=8,
       QTC_DISABLED=9, QTC_LOCAL_LOCKED=10 };
enum { QTC_STOP=0, QTC_PLAY=1, QTC_JUMP=2, QTC_NO_COMMAND=255 };
#define QTC_TIMEOUT_NS UINT64_C(1500000000)
typedef struct {
    uint32_t abi,size,fps,wire_status;
    uint8_t hour,minute,second,frame,sender[4];
    double position;
} QtcPacket;
typedef struct {
    uint32_t abi,size,enabled,selected,have_playing,playing,wire_status,have_time;
    uint64_t last_rx_ns,last_now_ns,revision;
    double position;
    uint8_t peer_ipv4[4],sender[4];
    uint32_t fps,reserved;
    /* Persistent edge counters: polling cannot lose an event to a later duplicate. */
    uint64_t position_revision,playing_revision,selection_revision,jump_revision;
} QtcClock;
typedef struct {
    uint32_t abi,size,enabled,selected,fresh,playing,have_playing,wire_status;
    uint64_t revision;
    double position;
    uint8_t peer_ipv4[4],sender[4];
    uint64_t last_rx_ns;
    uint32_t fps,local_allowed;
    uint64_t position_revision,playing_revision,selection_revision,jump_revision;
} QtcSnapshot;

/* Consumer-owned previous snapshots are independent of the resident clock. */
typedef struct {
    uint32_t abi,size,selected,playing,position_changed,selection_changed;
    uint32_t playing_changed,discrete_seek,local_allowed,reserved;
    double position;
} QtcConsumerPlan;
