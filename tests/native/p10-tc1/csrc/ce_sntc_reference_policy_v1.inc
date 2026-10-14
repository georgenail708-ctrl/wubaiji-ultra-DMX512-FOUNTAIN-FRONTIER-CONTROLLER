/* Independent UNCOMPILED successor draft. Include after CE_API/QcmPlan and
 * stdint/string declarations. Keep the original media mapping source intact.
 * All ns differences/ms drift and seek/coalescing decisions are C-owned.
 * plan is read-only, out.next commits ONLY after actual IO succeeds. */
enum { QRA_ABI=1,QRA_OK=0,QRA_BAD_ABI=1,QRA_BAD_RANGE=2,QRA_OLD_TIME=3 };
enum { QRA_NONE=0,QRA_PLAY=1,QRA_PAUSE=2 };
typedef struct {
    uint32_t abi,size,player_ready,player_playing,player_seekable,muted;
    uint32_t have_last_request,request_pending,have_play_start,reserved;
    uint64_t provider_epoch,audio_epoch,last_request_audio_epoch,pending_audio_epoch;
    uint64_t now_ns,last_io_ns,play_started_ns;
    int64_t player_ms,last_target_ms,pending_target_ms;
    uint32_t transport_pending,transport_action;
    uint64_t transport_audio_epoch;
} QraInput;
typedef struct {
    uint32_t abi,size,have_source,playing;
    uint64_t provider_epoch,audio_epoch;
} QraState;
typedef struct {
    uint32_t abi,size,seek_requested,seek_unavailable;
    uint32_t transport_action,pending_same,ack_pending,requires_io;
    uint32_t ack_transport,reserved;
    int64_t target_ms;
    uint64_t request_ns;
    QraState next;
} QraPlan;

static int ce_sntc_ref_overlap(const void* a,size_t an,const void* b,size_t bn) {
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    return x<=y?y-x<an:x-y<bn;
}
CE_API int32_t ce_sntc_reference_init_v1(QraState* state,uint32_t size) {
    if(!state||size!=sizeof(*state)||((uintptr_t)state%8))return QRA_BAD_ABI;
    memset(state,0,sizeof(*state));state->abi=QRA_ABI;state->size=sizeof(*state);
    return QRA_OK;
}
CE_API int32_t ce_sntc_reference_plan_v1(const QraState* state,
    const QcmPlan* base,const QraInput* raw,QraPlan* out) {
    QraPlan value;uint32_t force,need_seek=0,ready_elapsed,seek_elapsed,pending_transport;
    uint64_t difference;
    if(!state||!base||!raw||!out)return QRA_BAD_ABI;
    if(((uintptr_t)state%8)||((uintptr_t)base%8)||((uintptr_t)raw%8)||((uintptr_t)out%8))
        return QRA_BAD_ABI;
    if(ce_sntc_ref_overlap(state,sizeof(*state),out,sizeof(*out))
       ||ce_sntc_ref_overlap(base,sizeof(*base),out,sizeof(*out))
       ||ce_sntc_ref_overlap(raw,sizeof(*raw),out,sizeof(*out))
       ||ce_sntc_ref_overlap(state,sizeof(*state),base,sizeof(*base))
       ||ce_sntc_ref_overlap(state,sizeof(*state),raw,sizeof(*raw))
       ||ce_sntc_ref_overlap(base,sizeof(*base),raw,sizeof(*raw)))return QRA_BAD_ABI;
    if(state->abi!=QRA_ABI||state->size!=sizeof(*state)
       ||base->abi!=QCM_ABI||base->size!=sizeof(*base)
       ||raw->abi!=QRA_ABI||raw->size!=sizeof(*raw))return QRA_BAD_ABI;
    if(state->have_source>1||state->playing>1||raw->reserved
       ||raw->player_ready>1||raw->player_playing>1||raw->player_seekable>1||raw->muted>1
       ||raw->have_last_request>1||raw->request_pending>1||raw->have_play_start>1
       ||raw->transport_pending>1||raw->transport_action>QRA_PAUSE
       ||(raw->transport_pending&&raw->transport_action==QRA_NONE)
       ||(raw->request_pending&&!raw->have_last_request)
       ||base->selected>1||base->playing>1||base->audio_inside>1||base->apply_transport>1
       ||base->pause_outside_clip>1||base->event>QCM_SEEK
       ||(base->selected&&base->event==QCM_DETACHED)
       ||(!base->selected&&base->event!=QCM_DETACHED)
       ||(raw->player_ready&&raw->player_ms<0)
       ||(raw->have_last_request&&raw->last_target_ms<0)
       ||(raw->request_pending&&raw->pending_target_ms<0)
       ||(base->audio_inside&&base->audio_ms<0))return QRA_BAD_RANGE;
    if((raw->have_last_request&&raw->last_io_ns>raw->now_ns)
       ||(raw->have_play_start&&raw->play_started_ns>raw->now_ns))return QRA_OLD_TIME;
    memset(&value,0,sizeof(value));value.abi=QRA_ABI;value.size=sizeof(value);
    value.next=*state;value.target_ms=base->audio_ms;value.request_ns=raw->now_ns;
    if(!base->selected){value.next.have_source=0;*out=value;return QRA_OK;}
    pending_transport=raw->transport_pending&&raw->transport_audio_epoch==raw->audio_epoch
        ?raw->transport_action:QRA_NONE;
    if(raw->request_pending&&raw->pending_audio_epoch==raw->audio_epoch
       &&raw->player_ready&&raw->player_ms==raw->pending_target_ms)value.ack_pending=1;
    if(raw->transport_pending&&raw->transport_audio_epoch==raw->audio_epoch&&raw->player_ready
       &&((raw->transport_action==QRA_PLAY&&raw->player_playing)
          ||(raw->transport_action==QRA_PAUSE&&!raw->player_playing)))value.ack_transport=1;
    if(!raw->player_ready){*out=value;return QRA_OK;}
    if(raw->muted){
        if(raw->player_playing||pending_transport==QRA_PLAY)value.transport_action=QRA_PAUSE;
        if(raw->transport_pending&&raw->transport_audio_epoch==raw->audio_epoch
           &&raw->transport_action==value.transport_action)value.transport_action=QRA_NONE;
        value.requires_io=value.transport_action!=QRA_NONE;
        *out=value;return QRA_OK;
    }
    force=!state->have_source||state->provider_epoch!=raw->provider_epoch
       ||state->audio_epoch!=raw->audio_epoch||base->event==QCM_FIRST
       ||base->event==QCM_SEEK||(!base->playing&&state->playing)
       ||(base->playing&&!raw->player_playing&&base->apply_transport);
    if(base->audio_inside){
        /* Already queued exact source+target is a real request, never proof of
         * audible publication; reuse it rather than issue another async seek. */
        value.pending_same=raw->request_pending
            &&raw->pending_audio_epoch==raw->audio_epoch
            &&raw->pending_target_ms==base->audio_ms;
        if(force)need_seek=1;
        else if(base->event==QCM_FORWARD){
            ready_elapsed=!raw->have_play_start
                ||raw->now_ns-raw->play_started_ns>=UINT64_C(750000000);
            seek_elapsed=!raw->have_last_request
                ||raw->last_request_audio_epoch!=raw->audio_epoch
                ||raw->now_ns-raw->last_io_ns>=UINT64_C(500000000);
            difference=raw->player_ms>=base->audio_ms
                ?(uint64_t)raw->player_ms-(uint64_t)base->audio_ms
                :(uint64_t)base->audio_ms-(uint64_t)raw->player_ms;
            need_seek=ready_elapsed&&seek_elapsed&&difference>350;
        }
        /* Plain HOLD ignores free-clock drift. It never times out into pause.
         * STOP boundary/first/source/explicit jump still gets a target above. */
        if(need_seek&&!value.pending_same){
            if(raw->player_seekable)value.seek_requested=1;
            else value.seek_unavailable=1;
        }
        /* An opposite command already queued in QMediaPlayer may arrive after
         * the observed state. Supersede it using the newest external intent. */
        if(base->playing&&(!raw->player_playing||pending_transport==QRA_PAUSE)
           &&(base->apply_transport||force||pending_transport==QRA_PAUSE)
           &&!value.seek_unavailable)
            value.transport_action=QRA_PLAY;
    }
    /* Explicit STOP must pause even if source is outside/nonseekable. Seek
     * failure is reported independently; it must not swallow this command. */
    if((raw->player_playing||pending_transport==QRA_PLAY)
       &&(!base->playing||base->pause_outside_clip
          ||(pending_transport==QRA_PLAY&&!base->audio_inside)))
        value.transport_action=QRA_PAUSE;
    if(raw->transport_pending&&raw->transport_audio_epoch==raw->audio_epoch
       &&raw->transport_action==value.transport_action)value.transport_action=QRA_NONE;
    value.next.have_source=1;value.next.provider_epoch=raw->provider_epoch;
    value.next.audio_epoch=raw->audio_epoch;value.next.playing=base->playing;
    value.requires_io=value.seek_requested||value.transport_action!=QRA_NONE;
    *out=value;return QRA_OK;
}
CE_API uint32_t ce_sntc_reference_size_v1(uint32_t kind) {
    switch(kind){case 0:return sizeof(QraInput);case 1:return sizeof(QraState);
    case 2:return sizeof(QraPlan);default:return 0;}
}
