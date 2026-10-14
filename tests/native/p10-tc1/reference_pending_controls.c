/* Independent source-only controls. Not compiled/run under SOURCE_ONLY_HOLD.
 * Existing native/source/oracle receipts remain unchanged. */
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define CE_API
#include "csrc/ce_sntc_media_v1_types.inc"
#include "csrc/ce_sntc_reference_policy_v1.inc"

static QraInput input(uint32_t playing,uint32_t pending,uint32_t muted) {
    QraInput raw={0};raw.abi=QRA_ABI;raw.size=sizeof(raw);
    raw.player_ready=1;raw.player_playing=playing;raw.player_seekable=1;
    raw.muted=muted;raw.audio_epoch=7;raw.provider_epoch=3;raw.now_ns=1000000000;
    raw.transport_pending=pending!=QRA_NONE;raw.transport_action=pending;
    raw.transport_audio_epoch=7;return raw;
}
static QraState state(void) {
    QraState value={0};value.abi=QRA_ABI;value.size=sizeof(value);
    value.have_source=1;value.provider_epoch=3;value.audio_epoch=7;return value;
}
static QcmPlan base(uint32_t playing) {
    QcmPlan value={0};value.abi=QCM_ABI;value.size=sizeof(value);
    value.selected=1;value.event=QCM_HOLD;value.playing=playing;value.audio_inside=1;
    return value;
}
int main(void) {
    QraState previous=state();QraInput raw;QcmPlan selected;QraPlan plan;
    /* STOP must countermand an unobserved queued PLAY, despite already paused
     * observed state. The pending old PLAY is not acknowledged by paused state. */
    raw=input(0,QRA_PLAY,0);selected=base(0);
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_PAUSE&&plan.requires_io&&!plan.ack_transport);
    /* Latest PLAY counteracts queued PAUSE even while still observed Playing.
     * A plain HOLD alone never invents pause. */
    previous.playing=1;raw=input(1,QRA_PAUSE,0);selected=base(1);
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_PLAY&&plan.requires_io&&!plan.ack_transport);
    /* Muting and leaving the reference clip counteract pending PLAY too. */
    raw=input(0,QRA_PLAY,1);
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_PAUSE);
    raw=input(0,QRA_PLAY,0);selected.audio_inside=0;selected.pause_outside_clip=0;
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_PAUSE);
    /* A matching pending PAUSE coalesces; its actual observed state ACKs it. */
    selected=base(0);raw=input(0,QRA_PAUSE,0);
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_NONE&&plan.ack_transport);
    /* A different audio epoch cannot be interpreted as this player's pending
     * command. The last position/output remain held without a timeout Pause. */
    selected=base(1);raw=input(1,QRA_PAUSE,0);raw.transport_audio_epoch=6;
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_NONE&&!plan.ack_transport);
    raw=input(1,QRA_NONE,0);
    assert(ce_sntc_reference_plan_v1(&previous,&selected,&raw,&plan)==QRA_OK);
    assert(plan.transport_action==QRA_NONE&&!plan.seek_requested);
    return 0;
}
