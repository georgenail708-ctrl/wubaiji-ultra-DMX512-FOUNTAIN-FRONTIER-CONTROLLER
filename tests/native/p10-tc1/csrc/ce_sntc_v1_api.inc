/* STATIC ONLY. Caller serializes this bounded resident clock. No sockets,
 * heap, thread, timer, callback, pause/stop/blackout, CE-output or cache owner.
 * uint64 raw monotonic ns enter unchanged; all time/rate/freshness math is C.
 */
#include <math.h>
#include <string.h>
#ifdef _MSC_VER
#pragma float_control(precise, on)
#pragma fp_contract(off)
#endif

/* Exact live allocations are a caller obligation. Reject all declared ce_sntc_overlap
 * without pointer-end addition (which could wrap); never claim to validate
 * accessibility/lifetime from a raw address. No restrict alias assumption. */
static int ce_sntc_overlap(const void* a,size_t an,const void* b,size_t bn) {
    uintptr_t av=(uintptr_t)a,bv=(uintptr_t)b;
    return av<=bv?bv-av<an:av-bv<bn;
}
static int ce_sntc_writable_pair(const QtcClock* state,const QtcSnapshot* out) {
    return state&&out&&!ce_sntc_overlap(state,sizeof(*state),out,sizeof(*out));
}
static int ce_sntc_advance(uint64_t* counter) {
    if(*counter==UINT64_MAX)return 0;
    ++*counter;return 1;
}
static int ce_sntc_clock_valid(const QtcClock* s) {
    return s&&s->abi==QTC_ABI&&s->size==sizeof(*s)&&!s->reserved
        &&s->enabled<=1&&s->selected<=1&&s->have_playing<=1&&s->playing<=1&&s->have_time<=1
        &&isfinite(s->position)&&s->position>=0.
        &&(s->wire_status<=QTC_JUMP||s->wire_status==QTC_NO_COMMAND)
        &&(!s->selected||(s->enabled&&s->have_time&&s->have_playing&&s->fps>=1&&s->fps<=255
                         &&s->wire_status<=QTC_JUMP&&s->last_rx_ns<=s->last_now_ns));
}
static int ce_sntc_time_valid(const QtcClock* s,uint64_t now) {
    return !s->have_time||now>=s->last_now_ns;
}
static uint32_t ce_sntc_fresh(const QtcClock* s,uint64_t now) {
    return s->enabled&&s->selected&&now>=s->last_rx_ns&&now-s->last_rx_ns<QTC_TIMEOUT_NS;
}
static void ce_sntc_snapshot(const QtcClock* s,uint64_t now,QtcSnapshot* out) {
    QtcSnapshot value;
    memset(&value,0,sizeof(value));value.abi=QTC_ABI;value.size=sizeof(value);
    value.enabled=s->enabled;value.selected=s->selected;value.fresh=ce_sntc_fresh(s,now);
    value.playing=s->playing;value.have_playing=s->have_playing;value.wire_status=s->wire_status;
    value.revision=s->revision;value.position=s->position;value.last_rx_ns=s->last_rx_ns;
    value.fps=s->fps;value.local_allowed=!value.fresh;
    value.position_revision=s->position_revision;value.playing_revision=s->playing_revision;
    value.selection_revision=s->selection_revision;value.jump_revision=s->jump_revision;
    memcpy(value.peer_ipv4,s->peer_ipv4,4);memcpy(value.sender,s->sender,4);*out=value;
}
static int ce_sntc_unselect(QtcClock* s,uint32_t force) {
    if(s->selected&&!ce_sntc_advance(&s->selection_revision)&&!force)return 0;
    s->selected=0;s->wire_status=QTC_NO_COMMAND;s->fps=0;
    memset(s->peer_ipv4,0,4);memset(s->sender,0,4);
    /* Force OFF/close may saturate only the selection marker: actual selected=0
     * still changes, and OFF must remain possible. Never alter numeric markers. */
    return 1;
}
CE_API int32_t ce_sntc_decode_v1(const uint8_t* bytes,uint32_t count,QtcPacket* out) {
    QtcPacket value;uint32_t sum=0,i;
    if(!bytes||!out)return QTC_BAD_ABI;
    if(count!=16)return QTC_BAD_LENGTH;
    if(ce_sntc_overlap(bytes,16,out,sizeof(*out)))return QTC_BAD_ABI;
    if(bytes[0]!='S'||bytes[1]!='N'||bytes[2]!='T'||bytes[3]!='C')return QTC_BAD_MAGIC;
    for(i=0;i<14;++i)sum+=bytes[i];
    if(sum!=((uint32_t)bytes[14]*256u+(uint32_t)bytes[15]))return QTC_BAD_CHECKSUM;
    memset(&value,0,sizeof(value));value.abi=QTC_ABI;value.size=sizeof(value);
    value.fps=bytes[4]?bytes[4]:25u;
    if(bytes[5]>23||bytes[6]>59||bytes[7]>59||bytes[8]>=value.fps)return QTC_BAD_RANGE;
    if(bytes[9]>QTC_JUMP)return QTC_BAD_STATUS;
    value.hour=bytes[5];value.minute=bytes[6];value.second=bytes[7];value.frame=bytes[8];
    value.wire_status=bytes[9];memcpy(value.sender,bytes+10,4);
    /* Current tpTimeCode.totalSeconds order, integer rate, no drop-frame. */
    value.position=(double)value.hour*3600.0+(double)value.minute*60.0
        +(double)value.second+(double)value.frame/(double)value.fps;
    *out=value;return QTC_OK;
}
CE_API int32_t ce_sntc_clock_init_v1(QtcClock* state,uint32_t size,double position,uint32_t playing,
                             uint32_t known,QtcSnapshot* out) {
    QtcClock value;
    if(!ce_sntc_writable_pair(state,out)||size!=sizeof(*state))return QTC_BAD_ABI;
    if(!isfinite(position)||position<0.||playing>1||known>1)return QTC_BAD_RANGE;
    memset(&value,0,sizeof(value));value.abi=QTC_ABI;value.size=sizeof(value);
    value.position=position;value.playing=playing;value.have_playing=known;value.wire_status=QTC_NO_COMMAND;
    *state=value;ce_sntc_snapshot(state,0,out);return QTC_OK;
}
CE_API int32_t ce_sntc_clock_enabled_v1(QtcClock* state,uint32_t enabled,QtcSnapshot* out) {
    QtcClock value;
    if(!ce_sntc_writable_pair(state,out)||!ce_sntc_clock_valid(state))return QTC_BAD_ABI;
    if(enabled>1)return QTC_BAD_RANGE;
    value=*state;value.enabled=enabled;if(!enabled)ce_sntc_unselect(&value,1);
    *state=value;ce_sntc_snapshot(state,state->last_now_ns,out);return QTC_OK;
}
CE_API int32_t ce_sntc_clock_ingest_v1(QtcClock* state,const uint8_t* bytes,uint32_t count,const uint8_t* peer,
                               uint64_t now,uint32_t caller_playing,QtcSnapshot* out) {
    QtcClock value;QtcPacket packet;int32_t result;
    uint32_t position_changed,playing_changed,selection_changed,jump_changed;
    if(!ce_sntc_writable_pair(state,out)||!ce_sntc_clock_valid(state)||!peer)return QTC_BAD_ABI;
    if(caller_playing>1)return QTC_BAD_RANGE;
    if(!state->enabled)return QTC_DISABLED;
    if(ce_sntc_overlap(peer,4,state,sizeof(*state))||ce_sntc_overlap(peer,4,out,sizeof(*out)))return QTC_BAD_ABI;
    if(bytes&&count==16&&(ce_sntc_overlap(bytes,16,state,sizeof(*state))
        ||ce_sntc_overlap(bytes,16,out,sizeof(*out))||ce_sntc_overlap(bytes,16,peer,4)))return QTC_BAD_ABI;
    result=ce_sntc_decode_v1(bytes,count,&packet);if(result)return result;
    if(!ce_sntc_time_valid(state,now))return QTC_BAD_TIME;
    if(ce_sntc_fresh(state,now)&&(memcmp(peer,state->peer_ipv4,4)||memcmp(packet.sender,state->sender,4)))
        return QTC_OTHER_FRESH_SOURCE;
    selection_changed=!state->selected||memcmp(peer,state->peer_ipv4,4)
        ||memcmp(packet.sender,state->sender,4);
    position_changed=packet.position!=state->position;
    jump_changed=packet.wire_status==QTC_JUMP&&(selection_changed
        ||state->wire_status!=QTC_JUMP||position_changed);
    value=*state;
    if(packet.wire_status==QTC_STOP){value.playing=0;value.have_playing=1;}
    else if(packet.wire_status==QTC_PLAY){value.playing=1;value.have_playing=1;}
    else if(!state->selected||!value.have_playing){value.playing=caller_playing;value.have_playing=1;}
    /* An enabled but unselected clock has no external playing authority.
     * First/re-enabled JUMP adopts the current local caller, not init's seed. */
    playing_changed=value.playing!=state->playing||value.have_playing!=state->have_playing;
    if((position_changed||playing_changed)&&!ce_sntc_advance(&value.revision))return QTC_BAD_RANGE;
    if(position_changed&&!ce_sntc_advance(&value.position_revision))return QTC_BAD_RANGE;
    if(playing_changed&&!ce_sntc_advance(&value.playing_revision))return QTC_BAD_RANGE;
    if(selection_changed&&!ce_sntc_advance(&value.selection_revision))return QTC_BAD_RANGE;
    if(jump_changed&&!ce_sntc_advance(&value.jump_revision))return QTC_BAD_RANGE;
    value.enabled=1;value.selected=1;value.position=packet.position;value.wire_status=packet.wire_status;
    value.fps=packet.fps;value.last_rx_ns=now;value.last_now_ns=now;value.have_time=1;
    memcpy(value.peer_ipv4,peer,4);memcpy(value.sender,packet.sender,4);
    *state=value;ce_sntc_snapshot(state,now,out);return QTC_OK;
}
CE_API int32_t ce_sntc_clock_sample_v1(QtcClock* state,uint64_t now,QtcSnapshot* out) {
    QtcClock value;
    if(!ce_sntc_writable_pair(state,out)||!ce_sntc_clock_valid(state))return QTC_BAD_ABI;
    if(!ce_sntc_time_valid(state,now))return QTC_BAD_TIME;
    value=*state;value.have_time=1;value.last_now_ns=now;
    /* Staleness only changes computed freshness/local permission, never the
     * selected source, last position, actual playback or numeric revision. */
    *state=value;ce_sntc_snapshot(state,now,out);return QTC_OK;
}
CE_API int32_t ce_sntc_clock_release_for_local_v1(QtcClock* state,uint64_t now,double position,uint32_t playing,
                                          QtcSnapshot* out) {
    QtcClock value;uint32_t position_changed,playing_changed;
    if(!ce_sntc_writable_pair(state,out)||!ce_sntc_clock_valid(state))return QTC_BAD_ABI;
    if(!isfinite(position)||position<0.||playing>1)return QTC_BAD_RANGE;
    if(!ce_sntc_time_valid(state,now))return QTC_BAD_TIME;
    if(ce_sntc_fresh(state,now))return QTC_LOCAL_LOCKED;
    value=*state;position_changed=position!=value.position;
    playing_changed=playing!=value.playing||!value.have_playing;
    if((position_changed||playing_changed)&&!ce_sntc_advance(&value.revision))return QTC_BAD_RANGE;
    if(position_changed&&!ce_sntc_advance(&value.position_revision))return QTC_BAD_RANGE;
    if(playing_changed&&!ce_sntc_advance(&value.playing_revision))return QTC_BAD_RANGE;
    if(!ce_sntc_unselect(&value,0))return QTC_BAD_RANGE;
    value.position=position;value.playing=playing;value.have_playing=1;
    value.have_time=1;value.last_now_ns=now;*state=value;ce_sntc_snapshot(state,now,out);return QTC_OK;
}
CE_API int32_t ce_sntc_clock_force_release_v1(QtcClock* state,QtcSnapshot* out) {
    QtcClock value;
    if(!ce_sntc_writable_pair(state,out)||!ce_sntc_clock_valid(state))return QTC_BAD_ABI;
    value=*state;value.enabled=0;ce_sntc_unselect(&value,1);*state=value;
    ce_sntc_snapshot(state,state->last_now_ns,out);return QTC_OK;
}
CE_API uint32_t ce_sntc_size_v1(uint32_t kind) {
    switch(kind){case 0:return sizeof(QtcPacket);case 1:return sizeof(QtcClock);
        case 2:return sizeof(QtcSnapshot);default:return 0;}
}

/* Identity of the independently reviewed source composition before this
 * declaration was appended. Fixed ABI sizes alone are not its qualification. */
CE_API uint64_t ce_sntc_contract_v1(void) {
    return UINT64_C(0x7f7a5497f0dee5a4);
}
