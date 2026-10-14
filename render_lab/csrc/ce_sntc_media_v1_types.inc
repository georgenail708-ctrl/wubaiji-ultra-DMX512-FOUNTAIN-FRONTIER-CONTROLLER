/* Independent source-only SNTC media policy; frozen research01 unchanged. */
enum { QCM_ABI=1, QCM_OK=0, QCM_BAD_ABI=1, QCM_BAD_RANGE=2, QCM_OLD_SAMPLE=3 };
enum { QCM_DETACHED=0, QCM_FIRST=1, QCM_HOLD=2, QCM_FORWARD=3, QCM_SEEK=4 };
typedef struct {
    uint32_t abi,size;
    uint64_t provider_epoch,audio_epoch,audio_history_epoch,video_epoch;
    double clip_start,clip_duration,audio_history_t;
    uint64_t audio_samples;
    uint32_t sample_rate,audio_ready,audio_history_known;
    uint32_t player_ready,player_playing,player_seekable,muted;
    int64_t player_ms;
    double video_anchor_t,video_fps,video_rate;
    int64_t video_anchor_frame,video_in,video_out;
    uint64_t video_total;
    uint32_t video_ready,video_direction,video_loop,video_published;
    uint32_t video_rate_parse_status;
    int32_t video_anchor_dir;
    uint32_t video_pending,video_failed,reserved;
    uint64_t video_published_epoch,video_pending_epoch;
    int64_t video_published_frame,video_pending_frame;
} QcmInput;
typedef struct {
    uint32_t abi,size,selected,playing,have_playing,have_video;
    uint64_t provider_epoch,selection_revision,jump_revision,position_revision,playing_revision,revision;
    uint64_t video_epoch,audio_epoch;
    double position;
    int64_t video_frame;
} QcmState;
typedef struct {
    uint32_t abi,size,event,selected;
    uint32_t apply_position,apply_transport,playing,audio_inside;
    uint32_t audio_has_samples,audio_compute,suppress_onset_pulse,apply_audio_ms;
    uint32_t video_decode,video_hold,player_seek_unavailable,pause_outside_clip;
    double timeline_dt,audio_t,feature_t;
    int64_t audio_ms,audio_sample,video_frame;
    int32_t video_play_dir;
    uint32_t reserved;
} QcmPlan;
