"""Existing reference track IO driven by one CE-owned external media plan.

No host seconds/ms conversion, drift thresholds, timeline advance or numerical
fallback. QMediaPlayer commands are asynchronous, so this route is best-effort
and retains failed/pending seeks without claiming audible publication.
"""
import time

class SelectedReferenceAudio:
    def __init__(self, bridge, track, planner):
        self._bridge, self._track, self._planner = bridge, track, planner
        self._state = planner.new_state()
        planner.init(self._state)
        self._reference_state = planner.new_reference_state()
        self._source = None
        self._epoch = time.monotonic_ns()
        self._error = ''
        self._have_last_request = self._request_pending = self._have_play_start = False
        self._last_request_epoch = self._pending_epoch = self._last_io_ns = self._play_started_ns = 0
        self._last_target_ms = self._pending_target_ms = 0
        self._transport_pending = False
        self._transport_action = self._transport_epoch = 0

    def selected(self):
        from ...engine.timeline_sntc_source_guard import reference_current
        if not reference_current(self):
            raise RuntimeError('External reference audio owner/source changed')
        return self._bridge._receiver.native_snapshot().selected == 1

    def _raw_input(self, provider_epoch):
        track = self._track
        raw = vars(track)
        if (type(raw.get('_clip_start')) is not float or type(raw.get('_duration')) is not float
                or type(raw.get('_muted')) is not bool
                or (raw.get('_audio_path') is not None and type(raw.get('_audio_path')) is not str)):
            raise RuntimeError('Reference clip metadata is not owned raw data')
        # Existing loader owns these fields. Do not recreate or normalize timing.
        if raw.get('_audio_path') and not raw.get('_muted') and raw.get('_player') is None:
            track._ensure_player()
        player = raw.get('_player')
        signature = (raw.get('_audio_path'), player, raw.get('_clip_start'), raw.get('_duration'))
        if signature != self._source:
            self._source = signature
            self._epoch = time.monotonic_ns()
        source = self._planner.new_input()
        source.provider_epoch, source.audio_epoch = provider_epoch, self._epoch
        source.clip_start, source.clip_duration = raw['_clip_start'], raw['_duration']
        source.muted = raw['_muted']
        if player is not None:
            from PyQt6.QtMultimedia import QMediaPlayer
            if type(player) is not QMediaPlayer or any(name in vars(player) for name in
                    ('playbackState','isSeekable','position','setPosition','play','pause','thread','error','errorString')):
                raise RuntimeError('Foreign reference player supplier')
            source.player_ready = 1
            source.player_playing = player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            source.player_seekable = player.isSeekable()
            source.player_ms = player.position()
        return source, player

    def _raw_reference_input(self, source):
        raw = self._planner.new_reference_input()
        for name in ('provider_epoch','audio_epoch','player_ready','player_playing','player_seekable','muted','player_ms'):
            setattr(raw,name,getattr(source,name))
        raw.have_last_request,raw.request_pending,raw.have_play_start = (
            self._have_last_request,self._request_pending,self._have_play_start)
        raw.last_request_audio_epoch,raw.pending_audio_epoch = self._last_request_epoch,self._pending_epoch
        raw.last_io_ns,raw.play_started_ns = self._last_io_ns,self._play_started_ns
        raw.last_target_ms,raw.pending_target_ms = self._last_target_ms,self._pending_target_ms
        raw.transport_pending,raw.transport_action = self._transport_pending,self._transport_action
        raw.transport_audio_epoch = self._transport_epoch
        raw.now_ns = time.monotonic_ns()
        return raw

    def _player_still_owned(self, player, source):
        raw = vars(self._track)
        return (self._source is not None and self._source[1] is player and raw.get('_player') is player
            and type(raw.get('_clip_start')) is float and type(raw.get('_duration')) is float
            and type(raw.get('_muted')) is bool and raw.get('_muted') is (source.muted == 1)
            and (raw.get('_audio_path') is None or type(raw.get('_audio_path')) is str)
            and self._source == (raw.get('_audio_path'),player,raw.get('_clip_start'),raw.get('_duration')))

    def consume(self, view, provider_epoch):
        from PyQt6.QtCore import QThread
        from ...engine.timeline_sntc_source_guard import reference_current
        if not reference_current(self):
            raise RuntimeError('External reference audio owner/source changed')
        if QThread.currentThread() != self._track.thread():
            raise RuntimeError('Reference audio IO requires its actual Qt owner thread')
        if not self._bridge._view_still_owned(view, provider_epoch):
            return
        draft = self._planner.copy_state(self._state)
        if view.selected != 1:
            # Detach state only; existing local routes keep their own IO behavior.
            source = self._planner.new_input()
            source.provider_epoch = provider_epoch
            plan = self._planner.plan(draft, view, source)
            request = self._raw_reference_input(source)
            io = self._planner.reference_plan(self._reference_state,plan,request)
            if not self._bridge._view_still_owned(view,provider_epoch):
                return
            self._reference_state = self._planner.copy_reference_state(io.next)
            self._state, self._error = draft, ''
            return
        try:
            source, player = self._raw_input(provider_epoch)
            plan = self._planner.plan(draft, view, source)
            request = self._raw_reference_input(source)
            io = self._planner.reference_plan(self._reference_state,plan,request)
            if (not self._bridge._view_still_owned(view,provider_epoch)
                    or not self._player_still_owned(player,source)):
                return
            # Logical external run state is independent of asynchronous IO success.
            self._track._transport_playing = plan.playing == 1
            if io.seek_requested:
                player.setPosition(io.target_ms)
                self._last_target_ms = self._pending_target_ms = io.target_ms
                self._last_request_epoch = self._pending_epoch = source.audio_epoch
                self._last_io_ns = time.monotonic_ns()
                self._have_last_request = self._request_pending = True
            if (not self._bridge._view_still_owned(view,provider_epoch)
                    or not self._player_still_owned(player,source)):
                raise RuntimeError('Reference source changed during media IO')
            if io.seek_requested:
                self._track._last_requested_pos_ms = io.target_ms
            if io.transport_action == 1:
                player.play()
                self._play_started_ns,self._have_play_start = time.monotonic_ns(),True
            elif io.transport_action == 2:
                # Explicit STOP/mute/clip boundary; no timeout pause inference.
                player.pause()
            if io.transport_action:
                self._transport_pending,self._transport_action,self._transport_epoch = True,io.transport_action,source.audio_epoch
            if (not reference_current(self) or not self._player_still_owned(player,source)
                    or not self._bridge._view_still_owned(view,provider_epoch)):
                raise RuntimeError('Reference owner/source changed during media IO')
            if io.seek_unavailable:
                self._error = '参考音频尚不可定位，保持外部时间线与输出'
                return
            # Observed completion clears pending only after all requested IO
            # succeeds; partial request journals survive later transport failure.
            if io.ack_pending and not io.seek_requested:
                self._request_pending = False
            if io.ack_transport and not io.transport_action:
                self._transport_pending = False
            self._reference_state = self._planner.copy_reference_state(io.next)
            self._state, self._error = draft, ''
        except Exception as error:
            # No broad fallback or state acknowledgement after partial IO.
            self._error = (type(error).__name__ + ': ' + str(error))[:256]


def reference_selected(track):
    consumer = vars(track).get('_sntc_reference')
    if consumer is None:
        return False
    from ...engine.timeline_sntc_source_guard import reference_current
    if not reference_current(consumer) or vars(consumer).get('_track') is not track:
        raise RuntimeError('Foreign external reference audio consumer')
    return consumer.selected()
