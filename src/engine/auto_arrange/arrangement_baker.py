"""
arrangement_baker — 离线整曲编曲烘焙器

把"自动编曲"从"边播放边实时决策"改为"导入歌曲时一次性把整首歌算完"。
产物是一份固定的 BakedArrangement（段落时间线 + 每段 ArrangementDecision），
之后每次播放都回放它 → 效果确定可复现。

烘焙流程：
  1. build_project_profile + learn_from_project + ArrangementDirector(seed)
  2. 用 AudioAnalyzer.frame_at(pos) 按固定步长扫整首歌，喂进一个**全新**的
     MusicStructureAnalyzer 实例（t_override=pos，离线快进、时序仍按真实时长展开），
     收集每个位置的 section_label / energy。
  3. 在段落边界处切分 → 每段构造 MusicCue → director.decide → 得该段决策。
  4. 组装成 BakedArrangement。

设计为纯逻辑 + 注入：analyzer 由外部传入（实时单例或测试桩），progress 回调可选。
在后台线程跑（导入即烘焙），不阻塞 UI。
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from .project_profile import build_project_profile
from .experience_table import learn_from_project
from .arrangement_director import ArrangementDirector, MusicCue, ArrangementDecision
from .baked_arrangement import (
    BakedArrangement, BakedSegment, audio_signature,
)

# 扫描步长（秒）：0.25s = 4Hz，足够定位段落边界，整首 4 分钟歌 ~960 帧，秒级完成。
_SCAN_STEP = 0.25
# 段落最短持续（秒）：短于此的段落噪点并入相邻段，避免决策抖动。
_MIN_SEGMENT_S = 2.0


def _section_at_frames(analyzer, duration: float,
                       progress: Optional[Callable[[float], None]] = None) -> List[tuple]:
    """离线扫整首歌，返回 [(t, section_label, energy), ...]（按 _SCAN_STEP 采样）。

    用一个全新 MSA 实例按时间顺序喂帧（t_override=pos），收集每帧段落判定。
    analyzer 必须已 load_audio_file 当前歌（_file_data 驻留）。
    """
    # 跨包导入 MSA：与代码库一致用绝对导入 + 多前缀兜底（app 把 src 加进 path，
    # 模块以顶层 audio.* 加载；打包/测试环境可能是 src.audio.*）。
    try:
        from audio.music_structure_analyzer import MusicStructureAnalyzer
    except ImportError:
        try:
            from src.audio.music_structure_analyzer import MusicStructureAnalyzer
        except ImportError:
            from ...audio.music_structure_analyzer import MusicStructureAnalyzer

    msa = MusicStructureAnalyzer()
    samples: List[tuple] = []
    if duration <= 0:
        return samples
    pos = 0.0
    last_pct = -1
    while pos < duration:
        try:
            frame = analyzer.frame_at(float(pos))
        except Exception:
            frame = None
        if frame is not None:
            try:
                ev = msa._on_frame(frame, t_override=pos)
            except Exception:
                ev = None
            if ev is not None:
                samples.append((
                    float(pos),
                    str(getattr(ev, "section_label", "") or ""),
                    float(getattr(ev, "rms", 0.0) or 0.0),
                ))
        if progress is not None:
            pct = int(100.0 * pos / max(duration, 1e-6))
            if pct != last_pct:
                last_pct = pct
                try:
                    progress(min(0.85, pos / max(duration, 1e-6) * 0.85))
                except Exception:
                    pass
        pos += _SCAN_STEP
    return samples


def _segments_from_samples(samples: List[tuple], duration: float) -> List[tuple]:
    """把逐帧段落标签压缩成 [(t_start, t_end, section, avg_energy), ...]。

    规则：连续同段落合并；过短段落（< _MIN_SEGMENT_S）并入前一段（噪点抑制）。
    """
    if not samples:
        return []
    raw: List[list] = []   # [t_start, t_end, section, [energies]]
    for (t, sec, energy) in samples:
        if raw and raw[-1][2] == sec:
            raw[-1][1] = t
            raw[-1][3].append(energy)
        else:
            raw.append([t, t, sec, [energy]])
    # 末段延伸到结尾
    if raw:
        raw[-1][1] = max(raw[-1][1], duration)

    # 合并过短段落到前一段
    merged: List[list] = []
    for seg in raw:
        dur = seg[1] - seg[0]
        if merged and dur < _MIN_SEGMENT_S:
            merged[-1][1] = seg[1]
            merged[-1][3].extend(seg[3])
        else:
            merged.append(seg)
    # 首段从 0 开始
    if merged:
        merged[0][0] = 0.0

    out = []
    for (t0, t1, sec, energies) in merged:
        avg_e = sum(energies) / len(energies) if energies else 0.5
        out.append((float(t0), float(t1), sec or "verse", float(avg_e)))
    return out


# 段落标签 → MusicCue.section 的规范键（MSA 的 SectionLabel 值 → 经验表段落键）
_SECTION_MAP = {
    "intro": "intro", "verse": "verse", "build": "build", "buildup": "build",
    "chorus": "chorus", "drop": "drop", "breakdown": "breakdown",
    "bridge": "breakdown", "outro": "outro", "unknown": "verse", "": "verse",
}


def bake_arrangement(
    project: Any,
    analyzer: Any,
    audio_path: str,
    seed: int = 0,
    bpm: float = 120.0,
    progress: Optional[Callable[[float], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    ml_manager: Any = None,
    bandit: Any = None,
) -> Optional[BakedArrangement]:
    """离线把整首歌算完，返回 BakedArrangement。失败返回 None。

    project   : 当前项目对象（建画像/学经验）
    analyzer  : AudioAnalyzer（须已 load_audio_file 当前歌；提供 frame_at + _file_duration）
    audio_path: 音频路径（写进方案做指纹绑定）
    seed      : 随机种子（"重新编排"换种子→不同变体；0=确定性）
    progress  : 0..1 进度回调
    log       : 文字日志回调（输出编曲依据）
    ml_manager: ArrangementModelManager（可选，ML增强决策）
    bandit    : KArmedBandit/UCBBandit（可选，RL在线学习）
    """
    def _log(msg: str):
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    try:
        duration = float(getattr(analyzer, "_file_duration", 0.0) or 0.0)
    except Exception:
        duration = 0.0
    if duration <= 0:
        _log("无法获取音频时长，烘焙中止（请确认音频已载入）")
        return None

    # 1) 画像 + 经验 + 总指挥
    try:
        profile = build_project_profile(project)
        learned = learn_from_project(project)
        director = ArrangementDirector(
            profile, learned, seed=seed,
            ml_manager=ml_manager,
            bandit=bandit,
        )
    except Exception as e:
        _log(f"烘焙启动失败：{e}")
        return None

    _log(f"开始离线编曲（seed={seed}）· 画像：{profile.summary_text()}")
    _log(f"   音频时长 {duration:.0f}s，扫描步长 {_SCAN_STEP}s")

    # 2) 离线扫段落
    samples = _section_at_frames(analyzer, duration, progress=progress)
    if not samples:
        _log("整曲扫描未取到任何分析帧，烘焙中止")
        return None
    seg_spans = _segments_from_samples(samples, duration)
    _log(f"段落划分：识别出 {len(seg_spans)} 段")

    # 3) 每段决策
    plan = BakedArrangement(
        audio_path=audio_path,
        audio_sig=audio_signature(audio_path),
        seed=int(seed),
        bpm=float(bpm),
        duration=duration,
        profile_summary=profile.summary_text(),
    )
    n = len(seg_spans)
    for i, (t0, t1, sec, energy) in enumerate(seg_spans):
        section = _SECTION_MAP.get(str(sec).lower(), "verse")
        # 乐句进度：段落末尾 → 接近 1（终止式渐慢用）
        phrase = (i + 1) / max(1, n)
        cue = MusicCue(section=section, energy=energy, bpm=bpm,
                       has_audio=True, phrase_progress=phrase)
        try:
            dec = director.decide(cue)
        except Exception as e:
            _log(f"   段 {i+1} 决策失败：{e}")
            dec = ArrangementDecision(section=section, energy=energy)
        plan.segments.append(BakedSegment(
            t_start=t0, t_end=t1, section=section, energy=energy, decision=dec))
        # 输出该段编曲依据
        _log(f"   ▸ 段{i+1} [{t0:.0f}-{t1:.0f}s] {section}（能量{energy:.2f}）：{dec.explain}")
        for sub, sd in dec.subsystems.items():
            if sd.role != "silent":
                role_cn = {"lead": "主奏", "support": "陪衬"}.get(sd.role, sd.role)
                tgt = f"{len(sd.group_ids)}台" if sd.group_ids else "全部"
                solo = " ·SOLO路径" if sd.solo_path else ""
                _log(f"       - {sub} {role_cn} preset={sd.preset_id} 目标={tgt} "
                     f"level={sd.level:.2f} speed={sd.speed:.2f}{solo}")
        if progress is not None:
            try:
                progress(0.85 + 0.15 * (i + 1) / max(1, n))
            except Exception:
                pass

    _log(f"编曲完成：{plan.summary_text()}")
    if progress is not None:
        try:
            progress(1.0)
        except Exception:
            pass
    return plan


# ══════════════════════════════════════════════════════════════════════════
#  声部制烘焙(L3)—— 深度音乐识别(L1) + 声部模型(L2) → BakedVoiceArrangement
# ══════════════════════════════════════════════════════════════════════════
def _voice_lib_path() -> Optional[str]:
    """标杆学到的声部弹性库路径(仓库根)。缺失时 load_voice_profiles 用内置默认。"""
    import os
    for cand in (".codex_voice_lib.json",
                 os.path.join(os.getcwd(), ".codex_voice_lib.json")):
        if os.path.isfile(cand):
            return cand
    return None


def bake_voice_arrangement(
    project: Any,
    audio_path: str,
    seed: int = 0,
    deep_analysis: Any = None,
    selected_fixture_ids: Optional[set] = None,
    progress: Optional[Callable[[float], None]] = None,
    log: Optional[Callable[[str], None]] = None,
):
    """声部制离线烘焙:整曲深度识别 → 逐段每声部决策 → 稀疏动作窗口方案。

    project       : 当前项目(建画像/枚举声部)
    audio_path    : 音频路径(指纹绑定)
    seed          : 随机种子(重新编排换变体)
    deep_analysis : 可注入已算好的 DeepAnalysis(测试/复用);None 则内部调 L1 analyze
    selected_fixture_ids : 选中设备集合;非空则只对选中设备编(scope_voices 过滤)。
    返回 BakedVoiceArrangement(失败 None)。
    """
    from .voice_model import (
        enumerate_voices, load_voice_profiles, arrange_voices, scope_voices,
    )
    from .baked_arrangement import BakedVoiceArrangement, VoiceClipWindow

    def _log(m: str):
        if log is not None:
            try:
                log(m)
            except Exception:
                pass

    # 1) 深度音乐识别(L1)
    if deep_analysis is None:
        try:
            from ...audio.deep_music_analysis import analyze as _deep_analyze
        except ImportError:
            try:
                from audio.deep_music_analysis import analyze as _deep_analyze
            except ImportError:
                from src.audio.deep_music_analysis import analyze as _deep_analyze
        _log("深度音乐识别中(整曲离线分析)…")
        try:
            deep_analysis = _deep_analyze(
                audio_path, seed=seed,
                progress=(lambda p, s="": progress(0.6 * float(p)) if progress else None),
                log=_log)
        except Exception as e:
            _log(f"深度识别失败：{e}")
            return None
    segments = list(getattr(deep_analysis, "segments", []) or [])
    if not segments:
        _log("未识别出任何段落,烘焙中止")
        return None

    # 2) 画像 + 枚举声部 + 弹性画像
    try:
        profile = build_project_profile(project)
        voices = enumerate_voices(profile)
        # ★ 只对选中设备编:scope_voices 把每个声部的组内成员限定到选中范围(空=全部)。
        voices = scope_voices(voices, selected_fixture_ids)
        profiles = load_voice_profiles(voices, _voice_lib_path())
    except Exception as e:
        _log(f"声部枚举失败：{e}")
        return None
    if not voices:
        _log("项目无可编排声部")
        return None
    _log(f"声部制编曲(seed={seed}) · {len(voices)}声部 · {profile.summary_text()}")
    _log(f"   数据驱动分段:{len(segments)}段")

    # 3) 全局降速:柔美曲整体放慢(用全曲能量分位中位 + bpm)
    energies = sorted(float(getattr(s, "energy_rank", 0.5) or 0.5) for s in segments)
    med_e = energies[len(energies) // 2] if energies else 0.5
    bpm = float(getattr(deep_analysis, "tempo", 120.0) or 120.0)
    # 低能慢曲 → soft<1;高能快曲 → soft~1
    global_soft = max(0.55, min(1.0, 0.6 + 0.4 * med_e + 0.0015 * (bpm - 120.0)))
    _log(f"   全局速度系数 soft={global_soft:.2f}(中位能量{med_e:.2f}, BPM{bpm:.0f})")

    # 4) 逐段每声部决策 → 稀疏动作窗口(只收 active 的)
    plan = BakedVoiceArrangement(
        audio_path=audio_path, audio_sig=audio_signature(audio_path),
        seed=int(seed), bpm=bpm, duration=float(getattr(deep_analysis, "duration", 0.0) or 0.0),
        profile_summary=profile.summary_text())
    plan.voice_meta = {v.key: {"device_type": v.device_type, "usage": v.usage,
                               "voice_class": v.voice_class,
                               "member_ids": [m for g in v.groups for m in (g.member_ids or [])]}
                       for v in voices}
    for v in voices:
        plan.voices.setdefault(v.key, [])

    # beat 时间(供打击型声部按拍切短动作):从 timeline.beat_mask + times 提取
    beat_times: List[float] = []
    try:
        tl = getattr(deep_analysis, "timeline", None)
        if tl is not None:
            import numpy as _np
            bm = getattr(tl, "beat_mask", None)
            tms = getattr(tl, "times", None)
            if bm is not None and tms is not None and len(bm) and len(tms):
                idx = _np.nonzero(_np.asarray(bm))[0]
                beat_times = [float(tms[i]) for i in idx if i < len(tms)]
    except Exception:
        beat_times = []

    # 打击型声部(泵突发/阀)切短动作的粒度类:matrix/motor/fountain_rgb 保持长clip
    _PERCUSSIVE_VC = {"pump", "valve"}

    def _subdivide(vc: str, d, t0: float, t1: float) -> List[tuple]:
        """按声部类型把 [t0,t1] 段切成动作窗口列表 [(a,b),...]。
        打击型(泵突发effect/阀)沿 beat 切 1~4s 短促追逐;其余整段一个长窗口。"""
        mech = str(getattr(d, "mechanism", "effect"))
        seg_len = t1 - t0
        # 静态保持(泵山形缓开)= 整段一个长窗口,不切
        if mech == "static" or vc not in _PERCUSSIVE_VC or seg_len < 2.5:
            return [(t0, t1)]
        # 打击型动态:用段内 beat 切;每 ~2 拍一个动作,夹到 1~4s
        seg_beats = [bt for bt in beat_times if t0 + 0.05 < bt < t1 - 0.05]
        if len(seg_beats) < 2:
            return [(t0, t1)]
        # 每 4 拍取一个切点(123BPM≈2s/动作,落在标杆短clip 1~4s带内,避免过切)
        cuts = [t0] + seg_beats[3::4] + [t1]
        cuts = sorted(set(round(c, 2) for c in cuts))
        wins = []
        for j in range(len(cuts) - 1):
            a, b = cuts[j], cuts[j + 1]
            if b - a < 0.6:          # 太短并入下一段
                continue
            wins.append((a, b))
        return wins or [(t0, t1)]

    n = len(segments)
    # 情感块:character 变了才换块序号(同块沿用 preset=连贯);prev_presets 记上块各声部 preset
    _prev_char = None
    _block_ord = -1
    _prev_presets: dict = {}
    for i, seg in enumerate(segments):
        _ch_now = str(getattr(seg, "character", ""))
        if _ch_now != _prev_char:
            _block_ord += 1
            _prev_char = _ch_now
        decs = arrange_voices(voices, profiles, seg, global_soft=global_soft,
                              block_ordinal=_block_ord, prev_presets=_prev_presets)
        # 更新 prev_presets(供下一段相邻块避重复)
        for _k, _d in decs.items():
            if getattr(_d, "preset_id", ""):
                _prev_presets[_k] = _d.preset_id
        t0 = float(getattr(seg, "t_start", 0.0) or 0.0)
        t1 = float(getattr(seg, "t_end", 0.0) or 0.0)
        char = str(getattr(seg, "character", ""))
        active_keys = [k for k, d in decs.items() if d.active]
        vc_of = {v.key: v.voice_class for v in voices}
        # ★设备间错开进入:给每个声部一个小的进场延迟(分层次,非齐刷刷同时起)。
        #   标杆实测仅13/81 clip同时起。按声部在本段active列表的次序错开 ~0.2s/个,
        #   主奏不延迟先入,陪衬依次跟进。延迟夹在段长10%内,不破坏段落对齐。
        seg_len = max(0.1, t1 - t0)
        order_in_seg = sorted(active_keys, key=lambda k: (decs[k].role != "lead", k))
        stagger_unit = min(0.25, seg_len * 0.06)
        stagger = {k: idx * stagger_unit for idx, k in enumerate(order_in_seg)}
        for k, d in decs.items():
            if not d.active:
                continue   # 留白:不出窗口(稀疏轨道)
            dly = stagger.get(k, 0.0)
            # 按声部粒度切短动作(打击型沿 beat,持续型整段)
            for (wa, wb) in _subdivide(vc_of.get(k, ""), d, t0, t1):
                wa2 = wa + dly if (wa <= t0 + 0.01) else wa   # 只对段首窗口错开进入
                if wb - wa2 < 0.5:
                    wa2 = wa
                dd = d.to_dict()
                dd["clip_dur"] = round(wb - wa2, 2)   # 窗口时长 = 切后实际
                plan.voices[k].append(VoiceClipWindow(
                    t_start=wa2, t_end=wb, character=char, decision=dd))
        # 编曲依据日志
        leads = [k for k, d in decs.items() if d.role == "lead"]
        _log(f"   ▸ 段{i+1} [{t0:.0f}-{t1:.0f}s] {char}：{len(active_keys)}声部 主奏={leads}")
        if progress is not None:
            try:
                progress(0.6 + 0.4 * (i + 1) / max(1, n))
            except Exception:
                pass

    # 去掉完全无动作的声部键(保持稀疏)
    plan.voices = {k: ws for k, ws in plan.voices.items() if ws}
    _log(f"声部编曲完成：{plan.summary_text()}")
    if progress is not None:
        try:
            progress(1.0)
        except Exception:
            pass
    return plan
