/* Source-only, uncompiled. All seconds/delta/ms/sample/video decisions in C.
 * No IO, heap, thread, callbacks, player commands or output-buffer ownership. */

#include <math.h>
#include <string.h>
#ifdef _MSC_VER
#pragma float_control(precise,on)
#pragma fp_contract(off)
#endif
static int ce_sntc_media_overlaps(const void* a,size_t an,const void* b,size_t bn) {
    uintptr_t av=(uintptr_t)a,bv=(uintptr_t)b;
    return av<=bv?bv-av<an:av-bv<bn;
}
static int ce_sntc_media_trunc_index(double value,int64_t* out) {
    /* 2^63 itself is excluded before float-to-int conversion, avoiding UB. */
    if(!isfinite(value)||value<0.0||value>=9223372036854775808.0)return 0;
    *out=(int64_t)value;return 1;
}
static int ce_sntc_media_valid_input(const QcmInput* p) {
    return p&&p->abi==QCM_ABI&&p->size==sizeof(*p)&&!p->reserved
      &&p->audio_ready<=1&&p->audio_history_known<=1
      &&p->player_ready<=1&&p->player_playing<=1&&p->player_seekable<=1&&p->muted<=1
      &&(!p->audio_history_known||isfinite(p->audio_history_t))
      &&p->audio_samples<=INT64_MAX&&(!p->audio_ready||p->sample_rate>=1)
      &&p->video_ready<=1&&p->video_direction<=2&&p->video_loop<=1
      &&p->video_published<=1&&p->video_pending<=1&&p->video_failed<=1
      &&p->video_rate_parse_status<=1
      &&(!p->video_ready||(isfinite(p->video_anchor_t)&&isfinite(p->video_fps)
          &&p->video_fps>0.
          &&p->video_total>=1&&p->video_total<=INT64_MAX
          &&(p->video_anchor_dir==1||p->video_anchor_dir==-1)));
}
static int ce_sntc_media_video_target(const QcmInput* in,double t,int64_t* target,int32_t* target_dir) {
    int64_t first,last,anchor,step;uint64_t span,rel,n,period,phase;
    double elapsed=t-in->video_anchor_t,rate=in->video_rate;
    /* Frozen normalize_video_rate: Type/Value caught->1, NaN/nonpositive->1,
     * then ordered [.05,8] clamp. Retained conversion errors are not swallowed. */
    if(in->video_rate_parse_status||isnan(rate)||rate<=0.)rate=1.;
    else if(rate<.05)rate=.05;else if(rate>8.)rate=8.;
    first=in->video_in<0?0:in->video_in;
    if((uint64_t)first>=in->video_total)first=(int64_t)in->video_total-1;
    last=in->video_out<=0?(int64_t)in->video_total-1:in->video_out;
    if((uint64_t)last>=in->video_total)last=(int64_t)in->video_total-1;
    if(last<first)last=first;
    anchor=in->video_anchor_frame<first?first:in->video_anchor_frame;
    if(anchor>last)anchor=last;
    if(elapsed<0.)elapsed=0.;
    if(!ce_sntc_media_trunc_index(elapsed*in->video_fps*rate,&step))return 0;
    *target_dir=in->video_anchor_dir;
    span=(uint64_t)(last-first)+1;rel=(uint64_t)(anchor-first);n=(uint64_t)step;
    if(in->video_direction==2){
        if(span==1){*target=first;*target_dir=(step&1)?-in->video_anchor_dir:in->video_anchor_dir;return 1;}
        if(span-1>UINT64_MAX/2)return 0;
        period=2*(span-1);phase=in->video_anchor_dir<0?(period-rel)%period:rel;
        n%=period;phase=phase>=period-n?phase-(period-n):phase+n;
        if(step)*target_dir=phase==0?-1:phase<span?1:-1;
        rel=phase<span?phase:period-phase;
    }else if(in->video_loop){
        n%=span;
        rel=in->video_direction==0?(rel+n)%span:(rel+span-n)%span;
    }else if(in->video_direction==0){
        rel=n>span-1-rel?span-1:rel+n;
    }else rel=n>rel?0:rel-n;
    *target=first+(int64_t)rel;return 1;
}
CE_API int32_t ce_sntc_media_init_v1(QcmState* state,uint32_t size) {
    if(!state||size!=sizeof(*state))return QCM_BAD_ABI;
    memset(state,0,sizeof(*state));state->abi=QCM_ABI;state->size=sizeof(*state);return QCM_OK;
}
CE_API int32_t ce_sntc_media_plan_v1(QcmState* state,const QtcSnapshot* clock,
                             const QcmInput* in,QcmPlan* out) {
    QcmState value;QcmPlan plan;uint32_t first,seek,changed;
    double start,duration;int64_t sample;
    if(!state||!clock||!in||!out)return QCM_BAD_ABI;
    if(ce_sntc_media_overlaps(state,sizeof(*state),out,sizeof(*out))
       ||ce_sntc_media_overlaps(state,sizeof(*state),clock,sizeof(*clock))
       ||ce_sntc_media_overlaps(state,sizeof(*state),in,sizeof(*in))
       ||ce_sntc_media_overlaps(out,sizeof(*out),clock,sizeof(*clock))
       ||ce_sntc_media_overlaps(out,sizeof(*out),in,sizeof(*in))
       ||ce_sntc_media_overlaps(clock,sizeof(*clock),in,sizeof(*in)))return QCM_BAD_ABI;
    if(state->abi!=QCM_ABI||state->size!=sizeof(*state)
       ||clock->abi!=QTC_ABI||clock->size!=sizeof(*clock))return QCM_BAD_ABI;
    if(!ce_sntc_media_valid_input(in)||state->selected>1||state->playing>1||state->have_playing>1
       ||state->have_video>1||!isfinite(state->position)
       ||clock->selected>1||clock->playing>1||clock->have_playing>1
       ||clock->enabled>1||clock->fresh>1||clock->local_allowed>1
       ||clock->local_allowed==clock->fresh
       ||(clock->wire_status>QTC_JUMP&&clock->wire_status!=QTC_NO_COMMAND)
       ||(clock->selected&&(!clock->enabled||!clock->have_playing||clock->wire_status>QTC_JUMP
                            ||clock->fps<1||clock->fps>255))
       ||(clock->fresh&&!clock->selected)
       ||!isfinite(clock->position)||clock->position<0.)return QCM_BAD_RANGE;
    if(state->provider_epoch==in->provider_epoch
       &&(clock->selection_revision<state->selection_revision
          ||clock->jump_revision<state->jump_revision||clock->position_revision<state->position_revision
          ||clock->playing_revision<state->playing_revision||clock->revision<state->revision))
        return QCM_OLD_SAMPLE;
    value=*state;memset(&plan,0,sizeof(plan));plan.abi=QCM_ABI;plan.size=sizeof(plan);
    plan.selected=clock->selected;plan.playing=clock->playing;
    plan.feature_t=clock->position; /* Timeline feature coordinate; sample index is clip-local. */
    if(!clock->selected){
        plan.event=QCM_DETACHED;value.selected=0;value.have_video=0;
    }else{
        first=!state->selected||state->provider_epoch!=in->provider_epoch
            ||state->selection_revision!=clock->selection_revision;
        seek=!first&&(clock->jump_revision!=state->jump_revision||clock->position<state->position);
        changed=first||seek||clock->position!=state->position;
        plan.event=first?QCM_FIRST:seek?QCM_SEEK:clock->position>state->position?QCM_FORWARD:QCM_HOLD;
        plan.apply_position=changed;
        plan.apply_transport=first||clock->playing!=state->playing||clock->have_playing!=state->have_playing;
        plan.timeline_dt=plan.event==QCM_FORWARD?clock->position-state->position:0.;
        /* max(0., raw) uses its first operand on NaN, exactly the old helper. */
        start=in->clip_start>0.?in->clip_start:0.;
        duration=in->clip_duration>0.?in->clip_duration:0.;
        plan.audio_inside=clock->position>=start&&clock->position<=start+duration;
        plan.pause_outside_clip=!plan.audio_inside&&in->player_ready&&!in->muted
            &&in->player_playing&&(changed||state->audio_epoch!=in->audio_epoch);
        if(in->player_ready&&!in->muted&&plan.audio_inside&&in->player_playing!=clock->playing)
            plan.apply_transport=1;
        if(plan.audio_inside){
            plan.audio_t=clock->position-start;
            if(!ce_sntc_media_trunc_index(plan.audio_t*1000.,&plan.audio_ms))return QCM_BAD_RANGE;
            if(in->audio_ready){
                if(!ce_sntc_media_trunc_index(plan.audio_t*(double)in->sample_rate,&sample))return QCM_BAD_RANGE;
                plan.audio_sample=sample;plan.audio_has_samples=(uint64_t)sample<in->audio_samples;
                plan.suppress_onset_pulse=in->audio_history_known
                    &&in->audio_history_epoch==in->audio_epoch&&in->audio_history_t==plan.feature_t;
                plan.audio_compute=plan.audio_has_samples&&!plan.suppress_onset_pulse;
            }
            if(in->player_ready&&!in->muted&&(changed||in->player_ms!=plan.audio_ms)){
                plan.apply_audio_ms=in->player_seekable;plan.player_seek_unavailable=!in->player_seekable;
            }
        }
        if(in->video_ready){
            if(!ce_sntc_media_video_target(in,clock->position,&plan.video_frame,&plan.video_play_dir))return QCM_BAD_RANGE;
            /* IO completion/pending state is actual producer metadata. A desired
             * target stored in this state is never proof of publication. */
            plan.video_hold=!(in->video_published&&in->video_published_epoch==in->video_epoch
                &&in->video_published_frame==plan.video_frame);
            plan.video_decode=plan.video_hold&&!in->video_failed
                &&!(in->video_pending&&in->video_pending_epoch==in->video_epoch
                     &&in->video_pending_frame==plan.video_frame);
            value.have_video=1;value.video_frame=plan.video_frame;value.video_epoch=in->video_epoch;
        }else value.have_video=0;
        value.selected=1;value.position=clock->position;
        value.playing=clock->playing;value.have_playing=clock->have_playing;
    }
    value.provider_epoch=in->provider_epoch;value.selection_revision=clock->selection_revision;
    value.audio_epoch=in->audio_epoch;
    value.jump_revision=clock->jump_revision;value.position_revision=clock->position_revision;
    value.playing_revision=clock->playing_revision;value.revision=clock->revision;
    *state=value;*out=plan;return QCM_OK;
}
CE_API uint32_t ce_sntc_media_size_v1(uint32_t kind) {
    switch(kind){case 0:return sizeof(QcmInput);case 1:return sizeof(QcmState);
        case 2:return sizeof(QcmPlan);default:return 0;}
}

/* Source composition identity before this declaration, checked with actual ABI. */
CE_API uint64_t ce_sntc_media_contract_v1(void) {
    return UINT64_C(0xf1856d3ec60d78dd);
}
