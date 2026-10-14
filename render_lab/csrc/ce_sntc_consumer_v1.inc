/* Derived consumer decisions are CE math too. No host position/range/direction
 * comparison, extrapolation, elapsed-time calculation or numerical fallback. */
static int ce_sntc_view_valid(const QtcSnapshot* view) {
    return view&&view->abi==QTC_ABI&&view->size==sizeof(*view)
        &&view->enabled<=1&&view->selected<=1&&view->fresh<=1
        &&view->playing<=1&&view->have_playing<=1&&view->local_allowed<=1
        &&view->local_allowed==!view->fresh
        &&isfinite(view->position)&&view->position>=0.
        &&(view->wire_status<=QTC_JUMP||view->wire_status==QTC_NO_COMMAND)
        &&(!view->fresh||(view->enabled&&view->selected))
        &&(!view->selected||(view->enabled&&view->have_playing
            &&view->fps>=1&&view->fps<=255&&view->wire_status<=QTC_JUMP));
}

CE_API int32_t ce_sntc_consumer_plan_v1(const QtcSnapshot* previous,
    const QtcSnapshot* current,uint32_t have_previous,QtcConsumerPlan* out) {
    QtcConsumerPlan value;
    if(!out||have_previous>1||!ce_sntc_view_valid(current)
        ||(have_previous&&!ce_sntc_view_valid(previous)))return QTC_BAD_ABI;
    if(ce_sntc_overlap(current,sizeof(*current),out,sizeof(*out))
        ||(have_previous&&ce_sntc_overlap(previous,sizeof(*previous),out,sizeof(*out))))
        return QTC_BAD_ABI;
    memset(&value,0,sizeof(value));value.abi=QTC_ABI;value.size=sizeof(value);
    value.selected=current->selected;value.playing=current->playing;
    value.position=current->position;value.local_allowed=current->local_allowed;
    value.position_changed=!have_previous||current->position!=previous->position;
    value.selection_changed=!have_previous||current->selected!=previous->selected
        ||current->selection_revision!=previous->selection_revision;
    value.playing_changed=!have_previous||current->playing!=previous->playing
        ||current->playing_revision!=previous->playing_revision||value.selection_changed;
    value.discrete_seek=current->selected&&(!have_previous||!previous->selected
        ||value.selection_changed||current->jump_revision!=previous->jump_revision
        ||current->position<previous->position);
    *out=value;return QTC_OK;
}

CE_API uint32_t ce_sntc_consumer_plan_size_v1(void) {
    return sizeof(QtcConsumerPlan);
}

/* Resume local transport from the held position without host clock arithmetic.
 * raw_now is the caller's admitted monotonic clock read, not an elapsed value. */
CE_API int32_t ce_sntc_origin_v1(double raw_now,double position,double* out) {
    double value;
    if(!out||((uintptr_t)out&7u))return QTC_BAD_ABI;
    if(!isfinite(raw_now)||!isfinite(position)||position<0.)return QTC_BAD_RANGE;
    value=raw_now-position;
    if(!isfinite(value))return QTC_BAD_RANGE;
    *out=value;return QTC_OK;
}
