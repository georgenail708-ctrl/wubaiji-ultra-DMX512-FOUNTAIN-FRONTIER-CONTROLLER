"""
arrangement_learner — ML-enhanced arrangement engine

Five models that learn from existing .dmxt projects to enhance auto-arrangement:
  1. PCAMotorMapper (Ch10) — PCA降维: 46-dim audio features → 3-dim motor XYZ
  2. SegmentClusterer (Ch9) — K-means聚类: group similar segments, reuse templates
  3. RoleDecisionTree (Ch4) — 决策树: learn "what audio features → what role" rules
  4. LassoFeatureSelector (Ch11) — Lasso特征选择: find truly important features
  5. ArrangementModelManager — 总控: train/save/load/predict lifecycle

Pure core, no Qt/engine dependencies. Uses sklearn + numpy + scipy.
"""
from __future__ import annotations

import json
import os
import time
import glob
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Any

import numpy as np

# ★ R183 批次G：工程 JSON 统一入口（多前缀兜底，与 command_arrange/renderer.py 同款）
try:
    from ...core.models import parse_project_json
except ImportError:
    try:
        from core.models import parse_project_json
    except ImportError:
        from src.core.models import parse_project_json

# ── sklearn imports (lazy-verified at module load) ─────────────────────
try:
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.linear_model import LassoCV
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score
    from sklearn.metrics import silhouette_score
    import joblib
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False

# ── local imports ──────────────────────────────────────────────────────
from .experience_table import SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE, SUBSYSTEMS
from .experience_table import ROLE_LEAD, ROLE_SUPPORT, ROLE_SILENT


# ═══════════════════════════════════════════════════════════════════════
# Feature vector specification (46 dimensions)
# ═══════════════════════════════════════════════════════════════════════

FEATURE_NAMES = [
    # [0:4] Energy class
    "rms", "energy_rank", "rms_harm_perc_ratio", "energy_variance",
    # [4:7] Spectral class
    "centroid_norm", "flux_norm", "flatness",
    # [7:11] Pitch class
    "f0_midi_norm", "voiced_prob", "has_melody", "melody_range",
    # [11:15] Rhythm class
    "bpm_norm", "perc_density", "sub_onset", "bass_onset",
    # [15:19] Structure class
    "is_build", "is_drop", "rising", "section_type_encoded",
    # [19:22] Chord class
    "chord_root_norm", "chord_quality", "chord_tension",
    # [22:26] Context
    "beat_phase", "bar_position", "phrase_progress", "tempo_delta",
    # [26:32] History (prev-1, prev-2, prev-3 energy & role)
    "prev1_energy", "prev1_role", "prev2_energy", "prev2_role",
    "prev3_energy", "prev3_role",
    # [32:36] Global stats
    "segment_count", "duration", "novelty_score", "transition_strength",
    # [36:40] Interaction features
    "energy_x_perc", "centroid_x_flux", "melody_x_energy", "buildup_x_perc",
    # [40:46] Instrument presence (from MusicStructureAnalyzer)
    "instr_kick", "instr_snare", "instr_hihat",
    "instr_bass", "instr_melody", "instr_pad",
    # [46:60] Phase 3 新增特征 (2026-07-13)
    # [46:48] Key detection
    "key_major_score", "key_minor_score",
    # [48:50] Downbeat tracking
    "downbeat_distance", "downbeat_strength",
    # [50:53] 7-band FFT (MA3 standard subset)
    "band_sub_mid", "band_low_mid", "band_high_mid",
    # [53:55] Tonal clarity
    "tonal_centroid_shift", "spectral_contrast",
    # [55:58] Instrument confidence (aggregates)
    "has_drums", "has_bass_instr", "has_vocals",
    # [58:60] Structural context
    "time_to_section_end", "section_transition_probability",
]

N_FEATURES = len(FEATURE_NAMES)  # 60

# section → numeric encoding
SECTION_ENCODE = {
    "intro": 0.0, "verse": 0.17, "build": 0.33, "pre_chorus": 0.42,
    "chorus": 0.5, "drop": 0.67, "breakdown": 0.83, "outro": 1.0,
}

# section → typical energy level (用于从 preset_sets 构造训练样本)
SECTION_ENERGY = {
    "intro": 0.25, "verse": 0.45, "build": 0.70, "chorus": 0.90,
    "drop": 1.0, "breakdown": 0.30, "outro": 0.20,
}

# preset name → inferred level (burst/explosion=high, breath/fade=low)
_PRESET_LEVEL_HINTS = {
    "burst": 1.0, "explosion": 0.95, "strobe": 0.9, "chase": 0.8,
    "beat": 0.85, "tidal": 0.8, "spiral": 0.75, "joystick": 0.8,
    "running": 0.7, "sequential": 0.7, "rainbow": 0.8, "color": 0.75,
    "pulse": 0.65, "wave": 0.55, "energy": 0.6, "gradient": 0.5,
    "circle": 0.5, "sine": 0.4, "breath": 0.3, "fade": 0.2,
    "linear": 0.25, "pressure": 0.5, "height": 0.55,
}

# role → numeric
ROLE_ENCODE = {ROLE_SILENT: 0, ROLE_SUPPORT: 1, ROLE_LEAD: 2}
ROLE_DECODE = {0: ROLE_SILENT, 1: ROLE_SUPPORT, 2: ROLE_LEAD}


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ModelMetrics:
    """Training metrics for a single model."""
    model_name: str = ""
    trained: bool = False
    train_time_s: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class TrainingEvent:
    """A single training log entry."""
    timestamp: float = 0.0
    step: str = ""
    message: str = ""
    metrics: Optional[Dict[str, Any]] = None


@dataclass
class EnhancedDecision:
    """ML-enhanced decision overlay on top of rule-based ArrangementDecision."""
    # Per-subsystem overrides
    motor_xyz: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # PCA → motor
    template_id: int = -1              # KMeans cluster → template
    ml_role: Dict[str, str] = field(default_factory=dict)     # sub → role
    ml_level: Dict[str, float] = field(default_factory=dict)  # sub → level
    top_features: List[int] = field(default_factory=list)     # Lasso selected feature indices
    confidence: float = 0.0            # overall ML confidence

    def to_dict(self) -> dict:
        return {
            "motor_xyz": list(self.motor_xyz),
            "template_id": self.template_id,
            "ml_role": dict(self.ml_role),
            "ml_level": dict(self.ml_level),
            "top_features": list(self.top_features),
            "confidence": round(self.confidence, 3),
        }


# ═══════════════════════════════════════════════════════════════════════
# Feature Extractor
# ═══════════════════════════════════════════════════════════════════════

class FeatureExtractor:
    """Extract 46-dim feature vectors from music segments.

    Works with BakedSegment objects (from baked_arrangement) plus audio analysis
    data when available. Falls back to heuristic features from segment metadata
    when audio analysis is not available.
    """

    def extract(self, seg, prev_segs=None, timeline=None, bpm=120.0) -> np.ndarray:
        """Extract 46-dim feature vector from a segment.

        Args:
            seg: BakedSegment or dict with section/energy/t_start/t_end fields.
            prev_segs: list of previous segments for history features.
            timeline: optional DenseFeatureTimeline for detailed audio features.
            bpm: beats per minute.

        Returns:
            shape=(46,) float64 array.
        """
        prev_segs = prev_segs or []
        feats = np.zeros(N_FEATURES, dtype=np.float64)

        # ── extract segment-level fields ──
        section = str(getattr(seg, "section", "") or "")
        energy = float(getattr(seg, "energy", 0.5) or 0.5)
        t_start = float(getattr(seg, "t_start", 0.0) or 0.0)
        t_end = float(getattr(seg, "t_end", 0.0) or 0.0)
        duration = max(0.05, t_end - t_start)

        decision = getattr(seg, "decision", None)
        subs = getattr(decision, "subsystems", {}) if decision is not None else {}

        # [0:4] Energy class
        feats[0] = np.clip(energy, 0.0, 1.0)
        feats[1] = energy  # energy_rank (simplified: same as energy for now)
        feats[2] = 0.5  # rms_harm_perc_ratio default
        feats[3] = 0.0  # energy_variance default

        # [4:7] Spectral class (defaults when no audio analysis)
        feats[4] = energy * 0.8  # centroid_norm proxy
        feats[5] = max(0.0, min(1.0, energy * 1.2 - 0.1))  # flux_norm proxy
        feats[6] = 0.5  # flatness default

        # [7:11] Pitch class
        feats[7] = 0.5  # f0_midi_norm
        feats[8] = 0.3  # voiced_prob
        feats[9] = 1.0 if section in ("chorus", "drop") else 0.0  # has_melody
        feats[10] = 0.3  # melody_range

        # [11:15] Rhythm class
        feats[11] = np.clip(bpm / 200.0, 0.0, 1.0)  # bpm_norm
        feats[12] = 0.5  # perc_density
        feats[13] = 0.0  # sub_onset
        feats[14] = 0.0  # bass_onset

        # [15:19] Structure class
        feats[15] = 1.0 if section == "build" else 0.0
        feats[16] = 1.0 if section == "drop" else 0.0
        feats[17] = 1.0 if section in ("build", "pre_chorus") else 0.0
        feats[18] = SECTION_ENCODE.get(section, 0.5)

        # [19:22] Chord class
        feats[19] = 0.5  # chord_root_norm
        feats[20] = 0.0  # chord_quality (0=major, 1=minor)
        feats[21] = 0.3  # chord_tension

        # [22:26] Context
        feats[22] = 0.5  # beat_phase
        feats[23] = 0.5  # bar_position
        feats[24] = 0.5  # phrase_progress
        feats[25] = 0.0  # tempo_delta

        # [26:32] History features
        for i, pseg in enumerate(prev_segs[:3]):
            p_energy = float(getattr(pseg, "energy", 0.5) or 0.5)
            p_role_val = self._infer_role_val(pseg)
            feats[26 + i * 2] = p_energy
            feats[27 + i * 2] = p_role_val

        # [32:36] Global stats
        total_segs = max(1, len(prev_segs) + 1)
        feats[32] = np.clip(total_segs / 40.0, 0.0, 1.0)  # segment_count norm
        feats[33] = np.clip(duration / 60.0, 0.0, 1.0)  # duration norm
        feats[34] = 0.0  # novelty_score default
        feats[35] = 0.0  # transition_strength default

        # [36:40] Interaction features
        feats[36] = feats[0] * feats[12]  # energy × perc
        feats[37] = feats[4] * feats[5]   # centroid × flux
        feats[38] = feats[9] * feats[0]   # melody × energy
        feats[39] = feats[15] * feats[12]  # buildup × perc

        # [40:46] Instrument presence — from benchmark feat data or timeline
        bench_feat = getattr(seg, "_bench_feat", None) or {}
        if isinstance(seg, dict):
            bench_feat = seg.get("_bench_feat", {}) or {}
        feats[40] = float(bench_feat.get("kick", 0.0) or 0.0)
        feats[41] = float(bench_feat.get("snare", 0.0) or 0.0)
        feats[42] = float(bench_feat.get("hat", 0.0) or 0.0)
        feats[43] = float(bench_feat.get("bass", 0.0) or 0.0)
        feats[44] = float(bench_feat.get("melody", 0.0) or 0.0)
        feats[45] = float(bench_feat.get("pad", 0.0) or 0.0)

        # ── enrich from timeline if available ──
        if timeline is not None:
            self._enrich_from_timeline(feats, timeline, t_start, t_end, bpm)
        else:
            # 无timeline时的默认值
            feats[46] = 0.5  # key_major default
            feats[47] = 0.0  # key_minor default

        # ── fill from benchmark feat (overrides heuristic defaults) ──
        if bench_feat:
            if "percussive" in bench_feat:
                feats[12] = float(bench_feat["percussive"])
            if "voiced" in bench_feat:
                feats[8] = float(bench_feat["voiced"])
            if "centroid" in bench_feat:
                feats[4] = float(bench_feat["centroid"])

        return feats

    def _infer_role_val(self, seg) -> float:
        """Infer a composite role value from segment's decision."""
        dec = getattr(seg, "decision", None)
        if dec is None:
            return 1.0  # default: support
        subs = getattr(dec, "subsystems", {}) if dec is not None else {}
        if not subs:
            return 1.0
        roles = [ROLE_ENCODE.get(getattr(d, "role", ROLE_SILENT), 0)
                 for d in subs.values()]
        return float(np.mean(roles)) / 2.0 if roles else 1.0

    def _enrich_from_timeline(self, feats, timeline, t_start, t_end, bpm):
        """用 DenseFeatureTimeline 的真实 numpy 数组填充特征向量。

        DenseFeatureTimeline 是纯 dataclass（无查询方法），直接按时间索引 numpy 数组。
        对段内所有帧取均值，比单帧采样更鲁棒。
        """
        try:
            times = getattr(timeline, "times", None)
            if times is None or len(times) == 0:
                return
            # 找到段覆盖的帧范围
            idx_s = int(np.searchsorted(times, t_start))
            idx_e = int(np.searchsorted(times, t_end))
            a, b = max(0, idx_s), min(len(times), idx_e + 1)
            if b <= a + 1:
                return

            # ── 能量类 ──
            rms_arr = getattr(timeline, "rms", None)
            if rms_arr is not None and len(rms_arr) > b:
                feats[0] = float(np.clip(np.mean(rms_arr[a:b]), 0.0, 1.0))
            harm = getattr(timeline, "rms_harm", None)
            perc = getattr(timeline, "rms_perc", None)
            if harm is not None and perc is not None and len(harm) > b and len(perc) > b:
                h_mean = np.mean(harm[a:b])
                p_mean = np.mean(perc[a:b])
                feats[2] = float(np.clip(h_mean / max(p_mean, 1e-10), 0.1, 10.0) / 5.0)
            if rms_arr is not None and len(rms_arr) > b:
                feats[3] = float(np.clip(np.var(rms_arr[a:b]), 0.0, 0.1) * 10.0)

            # ── 频谱类 ──
            centroid_arr = getattr(timeline, "centroid", None)
            if centroid_arr is not None and len(centroid_arr) > b:
                feats[4] = float(np.clip(np.mean(centroid_arr[a:b]) / 4000.0, 0.0, 1.0))
            flux_arr = getattr(timeline, "flux", None)
            if flux_arr is not None and len(flux_arr) > b:
                feats[5] = float(np.clip(np.mean(flux_arr[a:b]) / 200.0, 0.0, 1.0))

            # ── 音高类 ──
            f0_arr = getattr(timeline, "f0_midi", None)
            vp_arr = getattr(timeline, "voiced_prob", None)
            if f0_arr is not None and len(f0_arr) > b:
                f0_seg = f0_arr[a:b]
                feats[7] = float(np.clip(np.mean(f0_seg) / 100.0, 0.0, 1.0))
                f0_valid = f0_seg[f0_seg > 0]
                feats[10] = float(np.clip(np.ptp(f0_valid) / 84.0, 0.0, 1.0)) if len(f0_valid) > 1 else 0.0
            if vp_arr is not None and len(vp_arr) > b:
                vp_mean = float(np.mean(vp_arr[a:b]))
                feats[8] = vp_mean
                feats[9] = 1.0 if vp_mean > 0.5 else 0.0

            # ── 节奏类（4频带 onset）──
            onset_arr = getattr(timeline, "onset_bands", None)
            if onset_arr is not None and onset_arr.shape[1] > b:
                feats[12] = float(np.clip(np.mean(onset_arr[3, a:b]), 0.0, 1.0))  # 高频=打击密度
                feats[13] = float(np.clip(np.mean(onset_arr[0, a:b]), 0.0, 1.0))  # sub
                feats[14] = float(np.clip(np.mean(onset_arr[1, a:b]), 0.0, 1.0))  # bass

            # ── 和弦类（chroma 12维 → root + quality + tension）──
            chroma_arr = getattr(timeline, "chroma", None)
            if chroma_arr is not None and chroma_arr.shape[1] > b:
                seg_chroma = np.mean(chroma_arr[:, a:b], axis=1)
                dominant_root = int(np.argmax(seg_chroma))
                feats[19] = (dominant_root + 1) / 12.0
                feats[20] = 1.0 if seg_chroma[3] > seg_chroma[4] else 0.0  # minor vs major
                feats[21] = float(np.clip(1.0 - np.max(seg_chroma) / max(np.sum(seg_chroma), 1e-9), 0.0, 1.0))

            # ── 上下文：beat_phase + phrase_progress ──
            beat_mask = getattr(timeline, "beat_mask", None)
            if beat_mask is not None and len(beat_mask) > b:
                beat_times = times[beat_mask]
                mid_t = (t_start + t_end) / 2.0
                prev_beats = beat_times[beat_times <= mid_t]
                if len(prev_beats) > 0:
                    last_beat = prev_beats[-1]
                    next_beats = beat_times[beat_times > mid_t]
                    next_beat = next_beats[0] if len(next_beats) > 0 else last_beat + 60.0 / max(bpm, 1.0)
                    beat_interval = max(next_beat - last_beat, 0.01)
                    feats[22] = float(np.clip((mid_t - last_beat) / beat_interval, 0.0, 1.0))
            feats[24] = float(np.clip(((t_start + t_end) / 2.0 - t_start) / max(t_end - t_start, 0.01), 0.0, 1.0))

            # ── Phase 3 new features (46:60) ──
            # Key detection
            key_major = getattr(timeline, "key_major_score", None)
            key_minor = getattr(timeline, "key_minor_score", None)
            if key_major is not None and len(key_major) > b:
                feats[46] = float(np.clip(np.mean(key_major[a:b]), 0.0, 1.0))
            else:
                feats[46] = 0.5
            if key_minor is not None and len(key_minor) > b:
                feats[47] = float(np.clip(np.mean(key_minor[a:b]), 0.0, 1.0))
            # Downbeat
            downbeat = getattr(timeline, "downbeat_mask", None)
            if downbeat is not None and len(downbeat) > b:
                seg_down = downbeat[a:b]
                n_beats = np.sum(seg_down)
                feats[49] = float(np.clip(n_beats / max(b - a, 1), 0.0, 1.0))
            # Sub-mid band (use onset_bands if available)
            onset_arr = getattr(timeline, "onset_bands", None)
            if onset_arr is not None and onset_arr.shape[1] > b:
                feats[50] = float(np.clip(np.mean(onset_arr[0, a:b]), 0.0, 1.0))  # sub
                feats[51] = float(np.clip(np.mean(onset_arr[1, a:b]), 0.0, 1.0))  # low-mid
                feats[52] = float(np.clip(np.mean(onset_arr[2, a:b]), 0.0, 1.0))  # high-mid
            # Tonal centroid shift
            centroid_arr = getattr(timeline, "centroid", None)
            if centroid_arr is not None and len(centroid_arr) > b + 1:
                centroid_diff = np.diff(centroid_arr[a:b])
                feats[53] = float(np.clip(np.mean(np.abs(centroid_diff)) / 200.0, 0.0, 1.0))
            # Instrument confidence (aggregates from existing features)
            feats[55] = float(np.clip(feats[40] + feats[41] + feats[42], 0.0, 1.0))  # has_drums
            feats[56] = float(np.clip(feats[43], 0.0, 1.0))                           # has_bass_instr
            feats[57] = float(np.clip(feats[8], 0.0, 1.0))                            # has_vocals
            # Structural context
            feats[58] = float(np.clip((t_end - (t_start + t_end) / 2) / max(t_end - t_start, 0.01), 0.0, 1.0))
            feats[59] = 0.5  # transition_probability default

        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Model 1: PCA Motor Mapper (Ch10)
# ═══════════════════════════════════════════════════════════════════════

class PCAMotorMapper:
    """PCA降维: 46-dim audio features → 3-dim motor XYZ mapping.

    The first 3 principal components are interpreted as motor_x, motor_y, motor_z
    (z = height/pressure). This directly drives motor effect engines.
    """

    def __init__(self, n_components: int = 3):
        self.n_components = int(n_components)
        self.pca: Optional[PCA] = None
        self.scaler: Optional[StandardScaler] = None
        self.explained_variance_ratio_: List[float] = []
        self.cumulative_variance_: float = 0.0

    def fit(self, X: np.ndarray) -> ModelMetrics:
        """Fit PCA on feature matrix X (n_samples, 46)."""
        t0 = time.perf_counter()
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        self.pca = PCA(n_components=min(self.n_components, X.shape[1]))
        self.pca.fit(X_scaled)
        self.explained_variance_ratio_ = list(self.pca.explained_variance_ratio_)
        self.cumulative_variance_ = float(np.sum(self.explained_variance_ratio_))
        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="PCA",
            trained=True,
            train_time_s=round(elapsed, 3),
            metrics={
                "explained_variance_ratio": [round(v, 4) for v in self.explained_variance_ratio_],
                "cumulative_variance": round(self.cumulative_variance_, 4),
                "n_components": self.n_components,
            },
            params={"n_components": self.n_components},
            note=f"前{self.n_components}个主成分解释了{self.cumulative_variance_:.1%}的方差",
        )

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Transform features to motor XYZ. Returns (n, 3)."""
        if self.scaler is None or self.pca is None:
            return np.zeros((X.shape[0], 3), dtype=np.float64)
        X_scaled = self.scaler.transform(X)
        return self.pca.transform(X_scaled)

    def predict_motor(self, X_single: np.ndarray) -> Tuple[float, float, float]:
        """Predict motor XYZ from a single feature vector (46,)."""
        xyz = self.transform(X_single.reshape(1, -1))[0]
        return (float(np.clip(xyz[0], -1.0, 1.0)),
                float(np.clip(xyz[1], -1.0, 1.0)),
                float(np.clip(xyz[2], -1.0, 1.0)))


# ═══════════════════════════════════════════════════════════════════════
# Model 2: Segment Clusterer (Ch9)
# ═══════════════════════════════════════════════════════════════════════

class SegmentClusterer:
    """K-means clustering: group similar segments, build template library.

    Each cluster center represents a "prototype segment" whose best arrangement
    (from training data) becomes the template for new segments in that cluster.
    """

    def __init__(self, n_clusters: int = 8):
        self.n_clusters = int(n_clusters)
        self.kmeans: Optional[KMeans] = None
        self.scaler: Optional[StandardScaler] = None
        self.template_library: Dict[int, Dict[str, Any]] = {}
        self.silhouette_: float = 0.0
        self.inertia_: float = 0.0

    def fit(self, X: np.ndarray, segment_labels: Optional[List[Dict]] = None) -> ModelMetrics:
        """Fit KMeans and build template library."""
        t0 = time.perf_counter()
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        nc = min(self.n_clusters, X.shape[0])
        self.kmeans = KMeans(n_clusters=nc, random_state=42, n_init=10)
        self.kmeans.fit(X_scaled)
        self.inertia_ = float(self.kmeans.inertia_)

        # silhouette score
        if nc >= 2 and X.shape[0] >= nc:
            labels = self.kmeans.labels_
            try:
                self.silhouette_ = float(silhouette_score(X_scaled, labels))
            except Exception:
                self.silhouette_ = 0.0
        else:
            self.silhouette_ = 0.0

        # build template library from segment labels if provided
        if segment_labels:
            self._build_templates(X_scaled, segment_labels)

        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="KMeans",
            trained=True,
            train_time_s=round(elapsed, 3),
            metrics={
                "n_clusters": nc,
                "silhouette": round(self.silhouette_, 4),
                "inertia": round(self.inertia_, 1),
            },
            params={"n_clusters": nc},
            note=f"最优 k={nc}, 轮廓系数={self.silhouette_:.3f}",
        )

    def _build_templates(self, X_scaled: np.ndarray, segment_labels: List[Dict]):
        """For each cluster, find the segment with best user rating as template."""
        labels = self.kmeans.labels_
        for cid in range(self.n_clusters):
            mask = labels == cid
            if not np.any(mask):
                continue
            indices = np.where(mask)[0]
            # pick the one closest to cluster center
            center = self.kmeans.cluster_centers_[cid]
            dists = np.linalg.norm(X_scaled[indices] - center, axis=1)
            best_idx = indices[int(np.argmin(dists))]
            if best_idx < len(segment_labels):
                self.template_library[cid] = dict(segment_labels[best_idx])

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict cluster IDs for feature vectors."""
        if self.scaler is None or self.kmeans is None:
            return np.zeros(X.shape[0], dtype=np.int32)
        X_scaled = self.scaler.transform(X)
        return self.kmeans.predict(X_scaled)

    def predict_template(self, X_single: np.ndarray) -> Tuple[int, Optional[Dict]]:
        """Predict which template to use for a single feature vector."""
        cid = int(self.predict(X_single.reshape(1, -1))[0])
        return cid, self.template_library.get(cid)

    def elbow_scores(self, X: np.ndarray, k_range: range = range(2, 13)) -> List[Dict]:
        """Compute inertia for k in range (for elbow plot)."""
        scores = []
        X_s = StandardScaler().fit_transform(X)
        for k in k_range:
            if k > X.shape[0]:
                break
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            km.fit(X_s)
            scores.append({"k": k, "inertia": float(km.inertia_)})
        return scores


# ═══════════════════════════════════════════════════════════════════════
# Model 2b: DBSCAN Segment Clusterer (alternate to KMeans)
# ═══════════════════════════════════════════════════════════════════════

class DBSegmentClusterer:
    """DBSCAN备选聚类：自动发现非凸簇，与KMeans比较轮廓系数后选优。"""

    def __init__(self, eps: float = 0.5, min_samples: int = 3):
        self.eps = float(eps)
        self.min_samples = int(min_samples)
        self.dbscan: Optional[Any] = None
        self.scaler: Optional[StandardScaler] = None
        self.labels_: Optional[np.ndarray] = None
        self.silhouette_: float = 0.0
        self.n_clusters_: int = 0
        self.template_library: Dict[int, Dict[str, Any]] = {}

    def fit(self, X: np.ndarray, segment_labels: Optional[List[Dict]] = None) -> ModelMetrics:
        t0 = time.perf_counter()
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        try:
            from sklearn.cluster import DBSCAN
            self.dbscan = DBSCAN(eps=self.eps, min_samples=self.min_samples)
            self.labels_ = self.dbscan.fit_predict(X_scaled)
        except Exception:
            self.labels_ = np.zeros(X.shape[0], dtype=np.int32) - 1

        unique_labels = set(self.labels_)
        self.n_clusters_ = len(unique_labels - {-1})
        # 轮廓系数 (仅对非噪声点)
        if self.n_clusters_ >= 2:
            mask = self.labels_ != -1
            if np.sum(mask) >= max(2, self.n_clusters_):
                try:
                    self.silhouette_ = float(silhouette_score(
                        X_scaled[mask], self.labels_[mask]))
                except Exception:
                    self.silhouette_ = 0.0

        if segment_labels and self.n_clusters_ > 0:
            self._build_templates(X_scaled, segment_labels)

        elapsed = time.perf_counter() - t0
        noise_ratio = np.sum(self.labels_ == -1) / max(1, len(self.labels_))
        return ModelMetrics(
            model_name="DBSCAN", trained=True, train_time_s=round(elapsed, 3),
            metrics={"n_clusters": self.n_clusters_, "silhouette": round(self.silhouette_, 4),
                     "noise_ratio": round(float(noise_ratio), 3),
                     "eps": self.eps, "min_samples": self.min_samples},
            params={"eps": self.eps, "min_samples": self.min_samples},
            note=f"DBSCAN: {self.n_clusters_}簇, 噪声{noise_ratio:.0%}, 轮廓系数{self.silhouette_:.3f}",
        )

    def _build_templates(self, X_scaled, segment_labels):
        for cid in set(self.labels_):
            if cid == -1:
                continue
            mask = self.labels_ == cid
            indices = np.where(mask)[0]
            center = np.mean(X_scaled[indices], axis=0)
            dists = np.linalg.norm(X_scaled[indices] - center, axis=1)
            best_idx = indices[int(np.argmin(dists))]
            if best_idx < len(segment_labels):
                self.template_library[cid] = dict(segment_labels[best_idx])

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.scaler is None or self.dbscan is None:
            return np.zeros(X.shape[0], dtype=np.int32) - 1
        X_scaled = self.scaler.transform(X)
        return self.dbscan.fit_predict(X_scaled)


# ═══════════════════════════════════════════════════════════════════════
# Model 3: Role Decision Tree (Ch4)
# ═══════════════════════════════════════════════════════════════════════

class RoleDecisionTree:
    """Decision tree classifier: predict subsystem role (lead/support/silent).

    Uses shallow trees (max_depth=3) for interpretability. One tree per subsystem.
    """

    def __init__(self, max_depth: int = 3, min_samples_split: int = 5):
        self.max_depth = int(max_depth)
        self.min_samples_split = int(min_samples_split)
        self.trees: Dict[str, DecisionTreeClassifier] = {}
        self.accuracy: Dict[str, float] = {}
        self.rules: Dict[str, List[str]] = {}

    def fit(self, X: np.ndarray, y_dict: Dict[str, np.ndarray]) -> ModelMetrics:
        """Fit one decision tree per subsystem.

        Args:
            X: feature matrix (n, 40).
            y_dict: {subsystem: role_labels (n,)} where labels are 0/1/2.
        """
        if not _SKLEARN_OK:
            return ModelMetrics(model_name="DecisionTree", trained=False,
                                note="sklearn not available")

        t0 = time.perf_counter()
        for sub in SUBSYSTEMS:
            y = y_dict.get(sub)
            if y is None or len(y) < 5:
                continue
            tree = DecisionTreeClassifier(
                max_depth=self.max_depth,
                min_samples_split=self.min_samples_split,
                random_state=42,
            )
            tree.fit(X, y)
            self.trees[sub] = tree

            # accuracy
            train_acc = float(tree.score(X, y))
            try:
                cv_scores = cross_val_score(tree, X, y, cv=min(5, len(y) // 3))
                cv_acc = float(np.mean(cv_scores))
            except Exception:
                cv_acc = train_acc
            self.accuracy[sub] = train_acc

            # extract rules
            self.rules[sub] = self._extract_rules(tree)

        elapsed = time.perf_counter() - t0
        avg_acc = np.mean(list(self.accuracy.values())) if self.accuracy else 0.0
        return ModelMetrics(
            model_name="DecisionTree",
            trained=bool(self.trees),
            train_time_s=round(elapsed, 3),
            metrics={
                "accuracy": {sub: round(v, 3) for sub, v in self.accuracy.items()},
                "avg_accuracy": round(float(avg_acc), 3),
                "n_trees": len(self.trees),
            },
            params={"max_depth": self.max_depth, "min_samples_split": self.min_samples_split},
            note=f"准确率 {avg_acc:.1%}, 学到 {sum(len(r) for r in self.rules.values())} 条规则",
        )

    def predict(self, X_single: np.ndarray) -> Dict[str, int]:
        """Predict role for each subsystem. Returns {sub: role_code}."""
        result = {}
        for sub, tree in self.trees.items():
            pred = int(tree.predict(X_single.reshape(1, -1))[0])
            result[sub] = pred
        return result

    def predict_role_str(self, X_single: np.ndarray) -> Dict[str, str]:
        """Predict role as string (lead/support/silent)."""
        codes = self.predict(X_single)
        return {sub: ROLE_DECODE.get(c, ROLE_SILENT) for sub, c in codes.items()}

    def _extract_rules(self, tree) -> List[str]:
        """Extract human-readable rules from a decision tree."""
        rules = []
        try:
            text = tree.tree_
            feature = text.feature
            threshold = text.threshold
            children_left = text.children_left
            children_right = text.children_right
            value = text.value

            def _traverse(node, depth, conditions):
                if children_left[node] == children_right[node]:  # leaf
                    vals = value[node][0]
                    pred = int(np.argmax(vals))
                    role_name = ROLE_DECODE.get(pred, "silent")
                    conf = float(vals[pred]) / max(1.0, float(np.sum(vals)))
                    cond_str = " 且 ".join(conditions) if conditions else "无条件"
                    rules.append(f"{cond_str} → {role_name} (置信度{conf:.0%})")
                    return
                fname = FEATURE_NAMES[feature[node]] if feature[node] < N_FEATURES else f"f{feature[node]}"
                th = threshold[node]
                left_cond = f"{fname} ≤ {th:.2f}"
                right_cond = f"{fname} > {th:.2f}"
                _traverse(children_left[node], depth + 1, conditions + [left_cond])
                _traverse(children_right[node], depth + 1, conditions + [right_cond])

            _traverse(0, 0, [])
        except Exception:
            pass
        return rules


# ═══════════════════════════════════════════════════════════════════════
# Model 3b: AdaBoost Role Ensemble (取代/补充单决策树)
# ═══════════════════════════════════════════════════════════════════════

class AdaBoostRoleEnsemble:
    """AdaBoost ensemble替代单决策树。每子系统一个AdaBoost，准确率目标80%+。"""

    def __init__(self, n_estimators: int = 50, max_depth: int = 2, learning_rate: float = 0.8):
        self.n_estimators = int(n_estimators)
        self.base_max_depth = int(max_depth)
        self.learning_rate = float(learning_rate)
        self.estimators: Dict[str, Any] = {}  # sub -> AdaBoostClassifier
        self.accuracy: Dict[str, float] = {}
        self.feature_importances: Dict[str, np.ndarray] = {}

    def fit(self, X: np.ndarray, y_dict: Dict[str, np.ndarray]) -> ModelMetrics:
        if not _SKLEARN_OK:
            return ModelMetrics(model_name="AdaBoost", trained=False, note="sklearn not available")
        t0 = time.perf_counter()
        from sklearn.ensemble import AdaBoostClassifier
        for sub in SUBSYSTEMS:
            y = y_dict.get(sub)
            if y is None or len(y) < 5:
                continue
            ada = AdaBoostClassifier(
                estimator=DecisionTreeClassifier(max_depth=self.base_max_depth, random_state=42),
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                random_state=42,
            )
            ada.fit(X, y)
            self.estimators[sub] = ada
            train_acc = float(ada.score(X, y))
            try:
                cv_scores = cross_val_score(ada, X, y, cv=min(5, len(y)//3))
                cv_acc = float(np.mean(cv_scores))
            except Exception:
                cv_acc = train_acc
            self.accuracy[sub] = train_acc
            self.feature_importances[sub] = ada.feature_importances_
        elapsed = time.perf_counter() - t0
        avg_acc = np.mean(list(self.accuracy.values())) if self.accuracy else 0.0
        return ModelMetrics(
            model_name="AdaBoost", trained=bool(self.estimators),
            train_time_s=round(elapsed, 3),
            metrics={"accuracy": {sub: round(v,3) for sub,v in self.accuracy.items()},
                     "avg_accuracy": round(float(avg_acc), 3),
                     "n_estimators": self.n_estimators, "n_subsystems": len(self.estimators)},
            params={"n_estimators": self.n_estimators, "max_depth": self.base_max_depth,
                    "learning_rate": self.learning_rate},
            note=f"AdaBoost准确率 {avg_acc:.1%}, {len(self.estimators)}个子系统",
        )

    def predict(self, X_single: np.ndarray) -> Dict[str, int]:
        result = {}
        for sub, ada in self.estimators.items():
            result[sub] = int(ada.predict(X_single.reshape(1, -1))[0])
        return result

    def predict_role_str(self, X_single: np.ndarray) -> Dict[str, str]:
        codes = self.predict(X_single)
        return {sub: ROLE_DECODE.get(c, ROLE_SILENT) for sub, c in codes.items()}


# ═══════════════════════════════════════════════════════════════════════
# Model 4: Lasso Feature Selector (Ch11)
# ═══════════════════════════════════════════════════════════════════════

class LassoFeatureSelector:
    """L1-regularized feature selection: find which audio features matter most.

    Fits LassoCV for each subsystem's level prediction. Non-zero coefficients
    indicate important features. The selected feature indices are the union
    across all subsystems.
    """

    def __init__(self, alpha: float = 0.1):
        self.alpha = float(alpha)
        self.models: Dict[str, LassoCV] = {}
        self.selected_features: List[int] = []
        self.feature_importance: Dict[str, float] = {}
        self.scaler: Optional[StandardScaler] = None

    def fit(self, X: np.ndarray, y_dict: Dict[str, np.ndarray]) -> ModelMetrics:
        """Fit LassoCV per subsystem, aggregate selected features."""
        if not _SKLEARN_OK:
            return ModelMetrics(model_name="Lasso", trained=False,
                                note="sklearn not available")

        t0 = time.perf_counter()
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        all_coefs = np.zeros(N_FEATURES, dtype=np.float64)
        n_subs = 0

        for sub in SUBSYSTEMS:
            y = y_dict.get(sub)
            if y is None or len(y) < 5:
                continue
            model = LassoCV(
                alphas=np.logspace(-3, 1, 20) if self.alpha <= 0 else [self.alpha],
                cv=min(5, len(y) // 3),
                random_state=42,
                max_iter=5000,
            )
            model.fit(X_scaled, y)
            self.models[sub] = model
            all_coefs += np.abs(model.coef_)
            n_subs += 1

        if n_subs > 0:
            all_coefs /= n_subs
            self.selected_features = sorted(
                [i for i in range(N_FEATURES) if all_coefs[i] > 1e-6],
                key=lambda i: -all_coefs[i],
            )
            for i in self.selected_features:
                self.feature_importance[FEATURE_NAMES[i]] = float(all_coefs[i])

        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="Lasso",
            trained=bool(self.models),
            train_time_s=round(elapsed, 3),
            metrics={
                "n_selected": len(self.selected_features),
                "selected_features": [FEATURE_NAMES[i] for i in self.selected_features[:10]],
                "alpha": self.alpha,
            },
            params={"alpha": self.alpha},
            note=f"从{N_FEATURES}个特征中选出{len(self.selected_features)}个关键特征",
        )

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Return X with only selected features."""
        if not self.selected_features or self.scaler is None:
            return X
        X_scaled = self.scaler.transform(X)
        return X_scaled[:, self.selected_features]

    def predict_level(self, X_single: np.ndarray) -> Dict[str, float]:
        """Predict level for each subsystem."""
        if self.scaler is None:
            return {sub: 0.5 for sub in SUBSYSTEMS}
        X_s = self.scaler.transform(X_single.reshape(1, -1))
        result = {}
        for sub, model in self.models.items():
            pred = float(model.predict(X_s)[0])
            result[sub] = float(np.clip(pred, 0.0, 1.0))
        return result


# ═══════════════════════════════════════════════════════════════════════
# Model Manager (总控)
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# Model 5: Preset Recommender (content-based filtering)
# ═══════════════════════════════════════════════════════════════════════

class PresetRecommender:
    """基于内容的预设推荐器：从 preset_sets 学习 section→preset 关联。

    构建 section×preset 共现矩阵，用 TF-IDF 风格加权：
    在一个 section 中频繁出现、但在其他 section 中罕见的 preset 获得高分。
    """

    def __init__(self):
        self._section_presets: Dict[str, Dict[str, float]] = {}  # section → {preset: weight}
        self._all_presets: List[str] = []
        self._trained = False

    def fit(self, preset_sets: Dict[str, List[Dict]]) -> ModelMetrics:
        """从 preset_sets 构建推荐矩阵。

        Args:
            preset_sets: 与 .dmxt 的 audio_bridge_state.preset_sets 同结构。
        """
        t0 = time.perf_counter()
        # 统计每个 preset 在所有 section 中的出现
        section_preset_count: Dict[str, Dict[str, int]] = {}
        preset_doc_count: Dict[str, int] = {}  # 多少个 section 用了这个 preset
        all_presets_set = set()

        track_to_sub = {"light": SUB_LIGHT, "pump": SUB_PUMP,
                        "motor": SUB_MOTOR, "valve": SUB_VALVE}
        for track_type, rows in preset_sets.items():
            sub = track_to_sub.get(track_type, track_type)
            for row in (rows or []):
                if not isinstance(row, dict):
                    continue
                section = str(row.get("section", "") or "")
                if section not in SECTION_ENCODE:
                    continue
                sec_presets = row.get("section_presets", []) or []
                bar_presets = row.get("bar_presets", []) or []
                all_pids = []
                for p in sec_presets + bar_presets:
                    pid = str(p.get("preset_id", "") if isinstance(p, dict) else str(p))
                    if pid:
                        all_pids.append(pid)
                        all_presets_set.add(pid)

                if section not in section_preset_count:
                    section_preset_count[section] = {}
                for pid in all_pids:
                    section_preset_count[section][pid] = section_preset_count[section].get(pid, 0) + 1
                    preset_doc_count[pid] = preset_doc_count.get(pid, 0) + 1

        # TF-IDF style: tf × idf
        n_sections = max(len(section_preset_count), 1)
        for section, pcounts in section_preset_count.items():
            total = max(sum(pcounts.values()), 1)
            self._section_presets[section] = {}
            for pid, count in pcounts.items():
                tf = count / total
                idf = np.log((n_sections + 1) / (preset_doc_count.get(pid, 1) + 1)) + 1
                self._section_presets[section][pid] = float(tf * idf)

        self._all_presets = sorted(all_presets_set)
        self._trained = True
        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="PresetRecommender",
            trained=True,
            train_time_s=round(elapsed, 3),
            metrics={"n_presets": len(self._all_presets), "n_sections": n_sections},
            note=f"学到 {len(self._all_presets)} 个 preset × {n_sections} 个段落的关联",
        )

    def recommend(self, section: str, top_k: int = 3) -> List[Tuple[str, float]]:
        """为指定 section 推荐 top-k 个 preset。

        Returns:
            [(preset_id, score), ...] 按得分降序。
        """
        if not self._trained or section not in self._section_presets:
            return []
        scored = sorted(self._section_presets[section].items(), key=lambda x: -x[1])
        return [(pid, round(score, 4)) for pid, score in scored[:top_k]]

    def recommend_for_subsystem(self, section: str, subsystem: str, top_k: int = 3) -> List[str]:
        """为指定 section+subsystem 推荐 preset id 列表。"""
        recs = self.recommend(section, top_k=top_k * 2)
        # 简单过滤：只返回可能属于该子系统的 preset
        # (实际应用中根据 preset_pools 精确过滤)
        return [pid for pid, _ in recs[:top_k]]


# ═══════════════════════════════════════════════════════════════════════
# Model 6: Speed Predictor (per-device-type linear regression)
# ═══════════════════════════════════════════════════════════════════════

class SpeedPredictor:
    """设备速度/级别预测器：从 FountainArranger 设备特征学习 speed→(energy,perc,mel) 映射。

    每个设备类型一个简单的线性回归模型，仅 3 个特征（energy, percussive, melodic），
    数据量小，不必用复杂模型。
    """

    # 来自 FountainArranger 的设备速度数据（对标 81 clip 实测）
    DEVICE_SPEED_DATA = {
        "数控": {"lo": 0, "mid": 25, "hi": 50, "peak": 259, "responds": "melody"},
        "泵":   {"lo": 0, "mid": 82, "hi": 310, "peak": 6087, "responds": "percussion"},
        "阀":   {"lo": 0, "mid": 0, "hi": 0, "peak": 96, "responds": "percussion"},
        "LED灯组": {"lo": 352, "mid": 1513, "hi": 2937, "peak": 8745, "responds": "neither"},
        "喷泉RGB": {"lo": 10, "mid": 53, "hi": 177, "peak": 300, "responds": "melody"},
        "气爆":  {"lo": 0, "mid": 0, "hi": 0, "peak": 0, "responds": "percussion"},
    }

    # 能量带 → 速度查找键
    ENERGY_BANDS = [(0.0, 0.30, "lo"), (0.30, 0.55, "mid"), (0.55, 0.75, "hi"), (0.75, 1.0, "peak")]

    def __init__(self):
        self._models: Dict[str, Dict[str, float]] = {}  # device → {coef_e, coef_p, coef_m, intercept}
        self._trained = False

    def fit(self, device_data: Optional[Dict] = None) -> ModelMetrics:
        """从设备速度数据拟合速度预测模型（GBDT+交互特征，线性回退）。

        Args:
            device_data: 可选覆盖 DEVICE_SPEED_DATA 的字典。
        """
        t0 = time.perf_counter()
        data = device_data or self.DEVICE_SPEED_DATA
        self._models.clear()

        for dev_name, speeds in data.items():
            X_dev, y_dev = [], []
            responds = speeds.get("responds", "neither")
            for elo, ehi, key in self.ENERGY_BANDS:
                energy_mid = (elo + ehi) / 2.0
                perc_val = 1.0 if responds == "percussion" else 0.3
                mel_val = 1.0 if responds == "melody" else 0.1
                speed = float(speeds.get(key, 0) or 0)
                if speed > 0 or key == "peak":
                    # ★ 新增交互特征: energy��, energy×perc, energy×melody, perc×melody
                    X_dev.append([energy_mid, perc_val, mel_val,
                                 energy_mid**2, energy_mid*perc_val,
                                 energy_mid*mel_val, perc_val*mel_val])
                    y_dev.append(speed)

            if len(X_dev) < 4:
                self._models[dev_name] = {"coef_e": 0.0, "coef_p": 0.0, "coef_m": 0.0,
                                           "coef_e2": 0.0, "coef_ep": 0.0, "coef_em": 0.0,
                                           "coef_pm": 0.0, "intercept": 50.0}
                continue

            X_arr = np.array(X_dev, dtype=np.float64)
            y_arr = np.array(y_dev, dtype=np.float64)
            try:
                from sklearn.ensemble import GradientBoostingRegressor
                gbr = GradientBoostingRegressor(n_estimators=100, max_depth=3,
                                                learning_rate=0.1, random_state=42)
                gbr.fit(X_arr, y_arr)
                self._models[dev_name] = {"_gbr": gbr, "_type": "gbr"}
            except Exception:
                # 线性回退
                A = np.column_stack([X_arr, np.ones(len(X_arr))])
                coefs, _, _, _ = np.linalg.lstsq(A, y_arr, rcond=None)
                self._models[dev_name] = {
                    "coef_e": float(coefs[0]), "coef_p": float(coefs[1]),
                    "coef_m": float(coefs[2]), "intercept": float(coefs[-1]),
                    "_type": "linear",
                }

        self._trained = True
        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="SpeedPredictor",
            trained=True,
            train_time_s=round(elapsed, 3),
            metrics={"n_devices": len(self._models)},
            note=f"为 {len(self._models)} 种设备类型学习了速度预测模型(GBDT+交互特征)",
        )

    def predict_speed(self, device_type: str, energy: float = 0.5,
                      percussive: float = 0.5, melodic: float = 0.1) -> float:
        """预测某设备类型在当前音乐条件下的速度。

        Returns:
            预测速度 (DMX units/sec)，≥ 0。
        """
        model = self._models.get(device_type)
        if model is None:
            # 尝试模糊匹配
            for key, m in self._models.items():
                if key in device_type or device_type in key:
                    model = m
                    break
        if model is None:
            return 50.0  # 安全默认值

        if model.get("_type") == "gbr":
            gbr = model["_gbr"]
            feats = np.array([[energy, percussive, melodic,
                              energy**2, energy*percussive,
                              energy*melodic, percussive*melodic]])
            speed = float(gbr.predict(feats)[0])
        else:
            speed = (model["coef_e"] * energy + model["coef_p"] * percussive +
                     model["coef_m"] * melodic + model["intercept"])
        return float(max(0.0, speed))

    def predict_level(self, device_type: str, energy: float = 0.5) -> float:
        """预测某设备类型的输出强度 (0..1)。基于能量的简单线性映射。"""
        speeds = self.DEVICE_SPEED_DATA.get(device_type, {})
        peak = float(speeds.get("peak", 1000) or 1000)
        speed = self.predict_speed(device_type, energy)
        return float(np.clip(speed / max(peak, 1.0), 0.0, 1.0))


# ═══════════════════════════════════════════════════════════════════════
# Model 8: Stacking Arrangement Ensemble (元学习器)
# ═══════════════════════════════════════════════════════════════════════

class StackingArrangementEnsemble:
    """Stacking元学习器：组合6个基模型的预测，学习最优融合权重。"""

    def __init__(self):
        self.meta_role: Dict[str, Any] = {}   # sub -> LogisticRegressionCV
        self.meta_level: Dict[str, Any] = {}  # sub -> RidgeCV
        self._trained = False

    def fit(self, base_predictions: Dict[str, np.ndarray],
            y_role: Dict[str, np.ndarray],
            y_level: Dict[str, np.ndarray]) -> ModelMetrics:
        """用基模型预测作为元特征，训练元学习器。
        base_predictions: {model_name: (n_samples, n_outputs)} — 每个基模型的预测输出
        """
        if not _SKLEARN_OK:
            return ModelMetrics(model_name="Stacking", trained=False)
        t0 = time.perf_counter()
        # 拼接所有基模型预测 → meta特征矩阵
        meta_X_list = []
        for name, preds in base_predictions.items():
            if preds.ndim == 1:
                preds = preds.reshape(-1, 1)
            meta_X_list.append(preds)
        if not meta_X_list:
            return ModelMetrics(model_name="Stacking", trained=False)
        meta_X = np.column_stack(meta_X_list)

        from sklearn.linear_model import LogisticRegressionCV, RidgeCV
        for sub in SUBSYSTEMS:
            yr = y_role.get(sub)
            yl = y_level.get(sub)
            if yr is not None and len(yr) >= 5:
                lr = LogisticRegressionCV(cv=min(5, len(yr)//3), random_state=42, max_iter=1000)
                lr.fit(meta_X, yr)
                self.meta_role[sub] = lr
            if yl is not None and len(yl) >= 5:
                ridge = RidgeCV(alphas=[0.1, 1.0, 10.0])
                ridge.fit(meta_X, yl)
                self.meta_level[sub] = ridge
        self._trained = True
        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="Stacking", trained=True, train_time_s=round(elapsed, 3),
            metrics={"n_meta_features": meta_X.shape[1], "n_role_models": len(self.meta_role),
                     "n_level_models": len(self.meta_level)},
            note=f"Stacking: {meta_X.shape[1]}维元特征, {len(self.meta_role)}个role模型",
        )

    def predict(self, meta_X: np.ndarray) -> Dict[str, Any]:
        result = {"role": {}, "level": {}}
        for sub, lr in self.meta_role.items():
            result["role"][sub] = ROLE_DECODE.get(int(lr.predict(meta_X)[0]), ROLE_SILENT)
        for sub, ridge in self.meta_level.items():
            result["level"][sub] = float(np.clip(ridge.predict(meta_X)[0], 0.0, 1.0))
        return result

    def is_trained(self) -> bool:
        return self._trained


# ═══════════════════════════════════════════════════════════════════════
# Model 9: NumpyMLP (轻量MLP基模型)
# ═══════════════════════════════════════════════════════════════════════

class NumpyMLP:
    """NumPy实现的轻量MLP: 60→32→16→4, ReLU+Sigmoid, Adam优化器。
    作为Stacking ensemble的补充基模型，非替代品。
    """

    def __init__(self, layer_sizes=None, lr: float = 0.001, l2: float = 1e-4):
        if layer_sizes is None:
            layer_sizes = [60, 32, 16, 4]
        self.layer_sizes = list(layer_sizes)
        self.lr = float(lr)
        self.l2 = float(l2)
        self.weights: List[np.ndarray] = []
        self.biases: List[np.ndarray] = []
        self._trained = False
        # Adam 状态
        self._m_w: List[np.ndarray] = []
        self._v_w: List[np.ndarray] = []
        self._m_b: List[np.ndarray] = []
        self._v_b: List[np.ndarray] = []
        self._t: int = 0

    def _init_params(self, input_dim: int):
        rng = np.random.RandomState(42)
        sizes = [input_dim] + self.layer_sizes[1:]
        self.weights = []
        self.biases = []
        self._m_w = []
        self._v_w = []
        self._m_b = []
        self._v_b = []
        for i in range(len(sizes) - 1):
            # He初始化
            w = rng.randn(sizes[i], sizes[i+1]) * np.sqrt(2.0 / sizes[i])
            b = np.zeros(sizes[i+1])
            self.weights.append(w)
            self.biases.append(b)
            self._m_w.append(np.zeros_like(w))
            self._v_w.append(np.zeros_like(w))
            self._m_b.append(np.zeros_like(b))
            self._v_b.append(np.zeros_like(b))
        self._t = 0

    def _forward(self, X: np.ndarray) -> List[np.ndarray]:
        activations = [X]
        for i in range(len(self.weights) - 1):
            z = activations[-1] @ self.weights[i] + self.biases[i]
            a = np.maximum(0, z)  # ReLU
            activations.append(a)
        # 最后一层
        z = activations[-1] @ self.weights[-1] + self.biases[-1]
        a = 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))  # Sigmoid
        activations.append(a)
        return activations

    def fit(self, X: np.ndarray, y_level: Dict[str, np.ndarray],
            epochs: int = 200, batch_size: int = 32, patience: int = 20) -> ModelMetrics:
        """训练MLP预测level值。"""
        t0 = time.perf_counter()
        n_samples = X.shape[0]
        if n_samples < batch_size:
            return ModelMetrics(model_name="NumpyMLP", trained=False, note="样本不足")

        # 使用第一个子系统的y作为训练目标
        first_sub = SUBSYSTEMS[0]
        y = y_level.get(first_sub)
        if y is None:
            return ModelMetrics(model_name="NumpyMLP", trained=False, note="无训练标签")

        self._init_params(X.shape[1])
        best_loss = float('inf')
        best_weights = None
        best_biases = None
        no_improve = 0

        for epoch in range(epochs):
            # Mini-batch
            idx = np.random.permutation(n_samples)
            total_loss = 0.0
            for start in range(0, n_samples, batch_size):
                batch_idx = idx[start:start + batch_size]
                Xb = X[batch_idx]
                yb = y[batch_idx].reshape(-1, 1)

                # Forward
                acts = self._forward(Xb)
                pred = acts[-1]

                # Loss: MSE + L2
                diff = pred - yb
                mse = np.mean(diff ** 2)
                l2_loss = self.l2 * sum(np.sum(w**2) for w in self.weights)
                total_loss += mse + l2_loss

                # Backward
                grad = 2.0 * diff / len(batch_idx) * pred * (1.0 - pred)  # Sigmoid grad
                for i in range(len(self.weights) - 1, -1, -1):
                    dw = acts[i].T @ grad / acts[i].shape[0] + self.l2 * self.weights[i]
                    db = np.mean(grad, axis=0)
                    # Adam update
                    self._t += 1
                    beta1, beta2 = 0.9, 0.999
                    self._m_w[i] = beta1 * self._m_w[i] + (1 - beta1) * dw
                    self._v_w[i] = beta2 * self._v_w[i] + (1 - beta2) * dw**2
                    self._m_b[i] = beta1 * self._m_b[i] + (1 - beta1) * db
                    self._v_b[i] = beta2 * self._v_b[i] + (1 - beta2) * db**2
                    m_hat_w = self._m_w[i] / (1 - beta1**self._t)
                    v_hat_w = self._v_w[i] / (1 - beta2**self._t)
                    m_hat_b = self._m_b[i] / (1 - beta1**self._t)
                    v_hat_b = self._v_b[i] / (1 - beta2**self._t)
                    self.weights[i] -= self.lr * m_hat_w / (np.sqrt(v_hat_w) + 1e-8)
                    self.biases[i] -= self.lr * m_hat_b / (np.sqrt(v_hat_b) + 1e-8)
                    # 传播梯度到前一层
                    if i > 0:
                        grad = grad @ self.weights[i].T * (acts[i] > 0)  # ReLU grad

            avg_loss = total_loss / max(1, n_samples // batch_size)
            if avg_loss < best_loss:
                best_loss = avg_loss
                best_weights = [w.copy() for w in self.weights]
                best_biases = [b.copy() for b in self.biases]
                no_improve = 0
            else:
                no_improve += 1
            if no_improve >= patience:
                break

        if best_weights is not None:
            self.weights = best_weights
            self.biases = best_biases
        self._trained = True
        elapsed = time.perf_counter() - t0
        return ModelMetrics(
            model_name="NumpyMLP", trained=True, train_time_s=round(elapsed, 3),
            metrics={"final_loss": round(float(best_loss), 6), "epochs": epoch + 1},
            params={"layer_sizes": self.layer_sizes, "lr": self.lr, "l2": self.l2},
            note=f"MLP训练{epoch+1}轮, loss={best_loss:.4f}",
        )

    def predict(self, X_single: np.ndarray) -> float:
        if not self._trained:
            return 0.5
        acts = self._forward(X_single.reshape(1, -1))
        return float(np.clip(acts[-1][0, 0], 0.0, 1.0))

    def is_trained(self) -> bool:
        return self._trained


class ArrangementModelManager:
    """Manages all ML model lifecycles: train → save → load → predict.

    Usage:
        mgr = ArrangementModelManager()
        mgr.train_all(X, y_role, y_level, progress_cb)
        mgr.save("ml_models/arrangement_model.joblib")

        # Inference
        decision = mgr.predict(feature_vector)
    """

    MODEL_DIR = "ml_models"

    def __init__(self):
        self.pca_mapper = PCAMotorMapper(n_components=3)
        self.clusterer = SegmentClusterer(n_clusters=8)
        self.role_tree = RoleDecisionTree(max_depth=3, min_samples_split=5)
        self.lasso_selector = LassoFeatureSelector(alpha=0.1)
        self.preset_recommender = PresetRecommender()
        self.speed_predictor = SpeedPredictor()
        self.adaboost = AdaBoostRoleEnsemble(n_estimators=50)
        self.dbscan_clusterer = DBSegmentClusterer()
        self.stacking = StackingArrangementEnsemble()
        self.mlp = NumpyMLP()
        self.feature_extractor = FeatureExtractor()
        self.training_log: List[TrainingEvent] = []
        self._trained = False
        self._scaler = StandardScaler()

    # ── Training ──────────────────────────────────────────────────────

    def train_all(
        self,
        X: np.ndarray,
        y_role: Dict[str, np.ndarray],
        y_level: Dict[str, np.ndarray],
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        segment_labels: Optional[List[Dict]] = None,
    ) -> Dict[str, ModelMetrics]:
        """Train all 4 models. Returns metrics per model.

        Args:
            X: (n_samples, 46) feature matrix.
            y_role: {subsystem: role_labels (n,)}.
            y_level: {subsystem: level_values (n,)}.
            progress_callback: (step_name, percent, message) → None.
            segment_labels: optional segment metadata for template building.

        Returns:
            {model_name: ModelMetrics}.
        """
        if not _SKLEARN_OK:
            self._log("error", "sklearn 未安装，无法训练")
            return {}

        self.training_log.clear()
        results: Dict[str, ModelMetrics] = {}

        steps = [
            ("PCA", lambda: self.pca_mapper.fit(X)),
            ("KMeans", lambda: self.clusterer.fit(X, segment_labels)),
            ("DecisionTree", lambda: self.role_tree.fit(X, y_role)),
            ("Lasso", lambda: self.lasso_selector.fit(X, y_level)),
            ("AdaBoost", lambda: self.adaboost.fit(X, y_role)),
            ("DBSCAN", lambda: self.dbscan_clusterer.fit(X, segment_labels)),
            ("NumpyMLP", lambda: self.mlp.fit(X, y_level)),
        ]
        # PresetRecommender + SpeedPredictor 由 extract_training_data 独立训练，
        # 不依赖 X/y_role/y_level，无需在此重复。

        for i, (name, fn) in enumerate(steps):
            pct = (i + 1) / len(steps)
            self._log("train_start", f"正在训练 {name}…")
            if progress_callback:
                progress_callback(name, pct, f"训练 {name} ({i + 1}/{len(steps)})…")
            try:
                metrics = fn()
                results[name] = metrics
                self._log("train_done", f"{name} 完成: {metrics.note}",
                          metrics=metrics.metrics)
            except Exception as e:
                self._log("train_error", f"{name} 失败: {e}")
                results[name] = ModelMetrics(model_name=name, note=str(e))

        # Stacking: 需要等所有基模型训练完后收集预测
        try:
            base_preds = self._collect_base_predictions(X, y_role, y_level)
            stacking_metrics = self.stacking.fit(base_preds, y_role, y_level)
            results["Stacking"] = stacking_metrics
            self._log("train_done", f"Stacking 完成: {stacking_metrics.note}",
                      metrics=stacking_metrics.metrics)
        except Exception as e:
            self._log("train_error", f"Stacking 失败: {e}")
            results["Stacking"] = ModelMetrics(model_name="Stacking", note=str(e))

        self._trained = any(m.trained for m in results.values())
        if progress_callback:
            progress_callback("done", 1.0, f"全部训练完成 ({len(results)} 模型)")
        return results

    def _collect_base_predictions(
        self, X: np.ndarray, y_role: Dict[str, np.ndarray], y_level: Dict[str, np.ndarray],
    ) -> Dict[str, np.ndarray]:
        """收集所有基模型在训练集上的预测，用于Stacking元学习。

        Returns:
            {model_name: pred_array} where pred_array is (n_samples, n_outputs).
        """
        base_preds: Dict[str, np.ndarray] = {}
        n = X.shape[0]

        # PCA → motor XYZ (3维)
        if self.pca_mapper.pca is not None:
            pca_xyz = self.pca_mapper.transform(X)
            base_preds["pca"] = pca_xyz

        # KMeans → cluster ID (1维)
        if self.clusterer.kmeans is not None:
            km_preds = self.clusterer.predict(X).reshape(-1, 1)
            base_preds["kmeans"] = km_preds

        # DBSCAN → cluster ID (1维)
        if self.dbscan_clusterer.dbscan is not None:
            db_preds = self.dbscan_clusterer.predict(X).reshape(-1, 1)
            base_preds["dbscan"] = db_preds

        # Role Trees → role per subsystem (n_subsystems维)
        if self.role_tree.trees:
            role_preds = []
            for sub in SUBSYSTEMS:
                if sub in self.role_tree.trees:
                    preds = np.array([self.role_tree.trees[sub].predict(X[i].reshape(1, -1))[0]
                                      for i in range(n)])
                    role_preds.append(preds)
                else:
                    role_preds.append(np.zeros(n))
            base_preds["roletree"] = np.column_stack(role_preds)

        # AdaBoost → role per subsystem (n_subsystems维)
        if self.adaboost.estimators:
            ada_preds = []
            for sub in SUBSYSTEMS:
                if sub in self.adaboost.estimators:
                    preds = np.array([self.adaboost.estimators[sub].predict(X[i].reshape(1, -1))[0]
                                      for i in range(n)])
                    ada_preds.append(preds)
                else:
                    ada_preds.append(np.zeros(n))
            base_preds["adaboost"] = np.column_stack(ada_preds)

        # MLP → level (1维)
        if self.mlp.is_trained():
            mlp_preds = np.array([self.mlp.predict(X[i]) for i in range(n)])
            base_preds["mlp"] = mlp_preds.reshape(-1, 1)

        return base_preds

    # ── Inference ─────────────────────────────────────────────────────

    def predict(self, seg, prev_segs=None, timeline=None, bpm=120.0) -> EnhancedDecision:
        """Run full ML pipeline on a segment → EnhancedDecision.

        Args:
            seg: BakedSegment or dict with section/energy/t_start/t_end.
            prev_segs: previous segments for history features.
            timeline: optional DenseFeatureTimeline.
            bpm: beats per minute.

        Returns:
            EnhancedDecision with ML predictions.
        """
        X = self.feature_extractor.extract(seg, prev_segs, timeline, bpm)
        result = EnhancedDecision()

        if self.pca_mapper.pca is not None:
            result.motor_xyz = self.pca_mapper.predict_motor(X)

        # KMeans template with DBSCAN fallback
        if self.clusterer.kmeans is not None:
            cid, template = self.clusterer.predict_template(X)
            result.template_id = cid
        elif self.dbscan_clusterer.dbscan is not None:
            db_preds = self.dbscan_clusterer.predict(X.reshape(1, -1))
            result.template_id = int(db_preds[0]) if db_preds[0] >= 0 else -1

        # AdaBoost role prediction (higher priority if trained)
        if self.adaboost.estimators:
            result.ml_role = self.adaboost.predict_role_str(X)
        elif self.role_tree.trees:
            result.ml_role = self.role_tree.predict_role_str(X)

        # Lasso level + MLP level (stacked if both available)
        if self.lasso_selector.models:
            result.ml_level = self.lasso_selector.predict_level(X)
            result.top_features = list(self.lasso_selector.selected_features)

        # MLP level as additional signal (used in stacking ensemble)
        if self.mlp.is_trained():
            mlp_level = self.mlp.predict(X)
            # Blend: if both lasso and MLP are available, take weighted average
            if result.ml_level and self.stacking.is_trained():
                # Stacking 已在训练时学习了最优融合，但推理时需构造meta_X
                pass  # stacking predict requires all base model outputs on X
            elif result.ml_level:
                # 简单加权平均: MLP作为补充
                for sub in SUBSYSTEMS:
                    if sub in result.ml_level:
                        result.ml_level[sub] = 0.7 * result.ml_level[sub] + 0.3 * mlp_level

        # confidence: 按"实际训练好的模型数 / 模型总数"加权(去重,分母用实际模型数)
        trained_flags = [
            1 if self.pca_mapper.pca is not None else 0,
            1 if self.clusterer.kmeans is not None or self.dbscan_clusterer.dbscan is not None else 0,
            1 if self.role_tree.trees else 0,
            1 if self.adaboost.estimators else 0,
            1 if self.lasso_selector.models else 0,
            1 if self.mlp.is_trained() else 0,
            1 if self.stacking.is_trained() else 0,
        ]
        n_trained = sum(trained_flags)
        result.confidence = n_trained / max(1, len(trained_flags))

        return result

    # ── Persistence ───────────────────────────────────────────────────

    def save(self, path: str = "") -> str:
        """Save all models to a joblib file. Returns the path used."""
        if not path:
            os.makedirs(self.MODEL_DIR, exist_ok=True)
            path = os.path.join(self.MODEL_DIR, "arrangement_model.joblib")
        else:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
        data = {
            "pca_mapper": self.pca_mapper,
            "clusterer": self.clusterer,
            "role_tree": self.role_tree,
            "lasso_selector": self.lasso_selector,
            "preset_recommender": self.preset_recommender,
            "speed_predictor": self.speed_predictor,
            "adaboost": self.adaboost,
            "dbscan_clusterer": self.dbscan_clusterer,
            "stacking": self.stacking,
            "mlp": self.mlp,
            "training_log": self.training_log,
            "_trained": self._trained,
        }
        joblib.dump(data, path)
        return path

    def load(self, path: str = "") -> bool:
        """Load all models from a joblib file. Returns success."""
        if not path:
            path = os.path.join(self.MODEL_DIR, "arrangement_model.joblib")
        if not os.path.isfile(path):
            return False
        try:
            data = joblib.load(path)
            self.pca_mapper = data.get("pca_mapper", self.pca_mapper)
            self.clusterer = data.get("clusterer", self.clusterer)
            self.role_tree = data.get("role_tree", self.role_tree)
            self.lasso_selector = data.get("lasso_selector", self.lasso_selector)
            self.preset_recommender = data.get("preset_recommender", self.preset_recommender)
            self.speed_predictor = data.get("speed_predictor", self.speed_predictor)
            self.adaboost = data.get("adaboost", self.adaboost)
            self.dbscan_clusterer = data.get("dbscan_clusterer", self.dbscan_clusterer)
            self.stacking = data.get("stacking", self.stacking)
            self.mlp = data.get("mlp", self.mlp)
            self.training_log = data.get("training_log", [])
            self._trained = data.get("_trained", False)
            return True
        except Exception:
            return False

    def is_trained(self) -> bool:
        return self._trained

    # ── Training data extraction ──────────────────────────────────────

    def extract_training_data(
        self, dmxt_paths: List[str],
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
    ) -> Tuple[Optional[np.ndarray], Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        """Scan .dmxt files, extract (X, y_role, y_level) training pairs.

        Returns:
            (X, y_role_dict, y_level_dict) or (None, {}, {}) on failure.
        """
        all_X = []
        all_y_role: Dict[str, List[int]] = {sub: [] for sub in SUBSYSTEMS}
        all_y_level: Dict[str, List[float]] = {sub: [] for sub in SUBSYSTEMS}
        all_seg_labels: List[Dict] = []
        all_preset_sets: Dict[str, List[Dict]] = {}  # 收集 preset_sets 供 PresetRecommender

        for pi, dpath in enumerate(dmxt_paths):
            if progress_callback:
                pct = (pi + 0.5) / max(1, len(dmxt_paths))
                progress_callback("extract", pct,
                                  f"扫描项目 {pi + 1}/{len(dmxt_paths)}: {os.path.basename(dpath)}")

            segments = self._extract_segments_from_project(dpath)
            # 同时收集 preset_sets 供 PresetRecommender 训练
            # ★ R183 批次G：.dmxt 大工程解析走统一入口（见 _load_project_json）
            try:
                pdata = _load_project_json(dpath)
                ps = (pdata.get("audio_bridge_state", {}) or {}).get("preset_sets", {}) or {}
                for tt, rows in ps.items():
                    if tt not in all_preset_sets:
                        all_preset_sets[tt] = []
                    all_preset_sets[tt].extend(rows)
            except Exception:
                pass

            if not segments:
                continue

            for si, seg in enumerate(segments):
                prev = segments[max(0, si - 3):si]
                X = self.feature_extractor.extract(seg, prev)
                all_X.append(X)
                all_seg_labels.append(seg)

                dec = seg.get("decision", {})
                subs = dec.get("subsystems", {})
                for sub in SUBSYSTEMS:
                    sd = subs.get(sub, {})
                    role_str = sd.get("role", ROLE_SILENT)
                    all_y_role[sub].append(ROLE_ENCODE.get(role_str, 0))
                    all_y_level[sub].append(float(sd.get("level", 0.0) or 0.0))

        if not all_X:
            return None, {}, {}

        X = np.array(all_X, dtype=np.float64)
        y_role = {sub: np.array(vals, dtype=np.int32) for sub, vals in all_y_role.items()}
        y_level = {sub: np.array(vals, dtype=np.float64) for sub, vals in all_y_level.items()}

        # 用收集到的 preset_sets 训练 PresetRecommender
        if all_preset_sets:
            try:
                self.preset_recommender.fit(all_preset_sets)
                self._log("train_done", f"PresetRecommender: {self.preset_recommender._all_presets}")
            except Exception:
                pass

        # SpeedPredictor 可以用内置数据直接训练
        try:
            self.speed_predictor.fit()
            self._log("train_done", "SpeedPredictor 已就绪")
        except Exception:
            pass

        return X, y_role, y_level

    def _extract_segments_from_project(self, dpath: str) -> List[Dict]:
        """Extract segment metadata from a .dmxt project or benchmark JSON file.

        Priority order:
          (1) benchmark_caselib (standalone JSON with "cases" key — 20 rich segments)
          (2) preset_sets (音频桥接器的 section→preset 映射 — 真实存在的用户数据)
          (3) baked_arrangement_data (离线编排产物 — 运行时才有)
          (4) clips heuristic (从时间线 clip 粗提取)
        """
        segments: List[Dict] = []

        # Try loading as JSON project（R183 批次G：.dmxt 走统一入口 _load_project_json）
        try:
            data = _load_project_json(dpath)
        except Exception:
            try:
                data = dict(np.load(dpath, allow_pickle=True).items())
            except Exception:
                return segments

        if not isinstance(data, dict):
            return segments

        # ── Path 1: benchmark_caselib（独立 JSON 或内嵌）──
        caselib = data.get("cases", None)
        if caselib and isinstance(caselib, list) and len(caselib) > 0:
            # 检测是否为 benchmark 格式（有 feat + voices）
            if isinstance(caselib[0], dict) and "feat" in caselib[0]:
                segments = self._segments_from_benchmark(caselib)
                if segments:
                    return segments
        # 也检查内嵌的 benchmark_caselib 键
        caselib2 = data.get("benchmark_caselib", None)
        if caselib2 and isinstance(caselib2, list):
            segments = self._segments_from_benchmark(caselib2)
            if segments:
                return segments

        # ── Path 2: preset_sets（最可靠的真实用户数据）──
        abs_state = data.get("audio_bridge_state", {}) or {}
        preset_sets = abs_state.get("preset_sets", {}) or {}
        if preset_sets:
            segments = self._segments_from_preset_sets(preset_sets)
            if segments:
                return segments

        # ── Path 2: benchmark_caselib ──
        caselib = data.get("benchmark_caselib", None)
        if caselib and isinstance(caselib, list):
            segments = self._segments_from_benchmark(caselib)
            if segments:
                return segments

        # ── Path 3: baked_arrangement_data (修复键路径) ──
        baked = data.get("baked_arrangement_data", {}) if isinstance(data, dict) else {}
        if isinstance(baked, dict):
            # ★ 先读 current.segments (BakedArrangementStore包装层), 回退直接读
            cur = baked.get("current", {}) or {}
            segs_raw = cur.get("segments", []) if isinstance(cur, dict) else []
            if not segs_raw:
                segs_raw = baked.get("segments", [])
        else:
            segs_raw = []
        if segs_raw:
            for s in segs_raw:
                if isinstance(s, dict):
                    segments.append({
                        "t_start": float(s.get("t_start", 0) or 0),
                        "t_end": float(s.get("t_end", 0) or 0),
                        "section": str(s.get("section", "")),
                        "energy": float(s.get("energy", 0.5) or 0.5),
                        "decision": s.get("decision", {}),
                    })
            return segments

        # ── Path 4: heuristic from timeline clips ──
        clips = data.get("clips", []) if isinstance(data, dict) else []
        if clips:
            sorted_clips = sorted(
                [c for c in clips if isinstance(c, dict)],
                key=lambda c: float(c.get("start_time", 0) or 0),
            )
            for c in sorted_clips:
                segments.append({
                    "t_start": float(c.get("start_time", 0) or 0),
                    "t_end": float(c.get("end_time", 0) or 0),
                    "section": "verse",
                    "energy": 0.5,
                    "decision": {},
                })
            return segments

        # ── Path 5: 从 audio_path 加载音频快速分析 ──
        cur_dict = (baked.get("current", {}) or {}) if isinstance(baked, dict) else {}
        audio_path = data.get("audio_path", "") or cur_dict.get("audio_path", "")
        if audio_path and os.path.isfile(audio_path) and not segments:
            try:
                segments = ArrangementModelManager._quick_audio_segments(audio_path)
                if segments:
                    return segments
            except Exception:
                pass

        return segments

    @staticmethod
    def _quick_audio_segments(audio_path: str) -> List[Dict]:
        """从音频文件快速提取段信息（不需要完整烘焙）。"""
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=180)
        if len(y) < sr:
            return []
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if tempo > 0 else 120.0
        n_segs = max(4, min(8, len(beats) // 16 if len(beats) > 16 else 4))
        seg_dur = len(y) / sr / n_segs
        segments = []
        for i in range(n_segs):
            t0 = i * seg_dur
            t1 = (i + 1) * seg_dur
            mid = int((t0 + t1) / 2 * sr)
            lo = max(0, mid - 2048)
            hi = min(len(y), mid + 2048)
            energy = float(np.clip(np.mean(np.abs(y[lo:hi])) * 5, 0.0, 1.0))
            if i == 0:
                section = "intro"
            elif i == n_segs - 1:
                section = "outro"
            elif energy > 0.7:
                section = "chorus"
            elif energy > 0.50:
                section = "build"
            else:
                section = "verse"
            segments.append({
                "t_start": t0, "t_end": t1, "section": section,
                "energy": energy, "decision": {},
            })
        return segments

    # section → typical duration in seconds (用于 preset_sets 无真实时间时)
    # 基于典型流行歌曲结构: 4-8 bar intro, 8-16 bar verse, 8 bar chorus @120bpm
    _SECTION_TYPICAL_DURATION_S = {
        "intro": 12.0, "verse": 20.0, "build": 10.0, "pre_chorus": 8.0,
        "chorus": 16.0, "drop": 16.0, "breakdown": 10.0, "outro": 12.0,
    }

    @staticmethod
    def _segments_from_preset_sets(preset_sets: Dict) -> List[Dict]:
        """从 audio_bridge_state.preset_sets 构造训练样本。

        每个 section×subsystem 行 → 一个训练样本。
        section_presets 非空 → role=lead，否则 role=silent。
        level 从 preset 名称推断。
        """
        segments = []
        # preset_sets: {"light": [...], "pump": [...], ...}
        track_to_sub = {
            "light": SUB_LIGHT, "pump": SUB_PUMP,
            "motor": SUB_MOTOR, "valve": SUB_VALVE,
        }
        for track_type, rows in preset_sets.items():
            sub = track_to_sub.get(track_type, track_type)
            if sub not in SUBSYSTEMS:
                continue
            for row in (rows or []):
                if not isinstance(row, dict):
                    continue
                section = str(row.get("section", "") or "")
                if section not in SECTION_ENCODE:
                    continue
                energy = SECTION_ENERGY.get(section, 0.5)

                sec_presets = row.get("section_presets", []) or []
                bar_presets = row.get("bar_presets", []) or []
                all_presets = sec_presets + bar_presets

                # 推断 role 和 level
                if all_presets:
                    role = ROLE_LEAD
                    # 从 preset 名称推断 level
                    levels = []
                    for p in all_presets:
                        pid = str(p.get("preset_id", "") if isinstance(p, dict) else str(p)).lower()
                        for hint, val in _PRESET_LEVEL_HINTS.items():
                            if hint in pid:
                                levels.append(val)
                                break
                        else:
                            levels.append(0.6)  # 默认中等强度
                    level = float(np.mean(levels)) if levels else 0.6
                    pid = str(all_presets[0].get("preset_id", "") if isinstance(all_presets[0], dict) else str(all_presets[0]))
                else:
                    role = ROLE_SILENT
                    level = 0.0
                    pid = ""

                # 构造决策（只对当前子系统有值，其余留白）
                decision = {"subsystems": {}}
                for s in SUBSYSTEMS:
                    if s == sub:
                        decision["subsystems"][s] = {
                            "role": role, "level": level,
                            "preset_id": pid, "group_ids": [],
                        }
                    else:
                        decision["subsystems"][s] = {
                            "role": ROLE_SILENT, "level": 0.0,
                            "preset_id": "", "group_ids": [],
                        }

                # ★ 修复: 使用段落典型时长代替固定5秒占位
                dur = ArrangementModelManager._SECTION_TYPICAL_DURATION_S.get(section, 8.0)
                segments.append({
                    "t_start": 0.0,
                    "t_end": dur,
                    "section": section,
                    "energy": energy,
                    "decision": decision,
                })
        return segments

    @staticmethod
    def _segments_from_benchmark(caselib: List[Dict]) -> List[Dict]:
        """从 benchmark_caselib.json 格式的案例库构造训练样本。

        每个案例有 feat (audio features: e/perc/voiced/centroid/kick/hat)
        + voices (device params: rate/topo/seq/mph)。
        将 feat 数据嵌入 segment，供 FeatureExtractor 使用真实值。
        """
        segments = []
        voice_to_sub = {
            "motor": SUB_MOTOR, "pump": SUB_PUMP,
            "valve": SUB_VALVE, "fountain_rgb": SUB_LIGHT,
            "matrix_rgb": SUB_LIGHT, "function": SUB_VALVE,
        }
        for case in (caselib or []):
            if not isinstance(case, dict):
                continue
            feat = case.get("feat", {}) or {}
            voices = case.get("voices", {}) or {}
            energy = float(feat.get("e", 0.5) or 0.5)
            perc = float(feat.get("perc", 0.5) or 0.5)
            voiced = float(feat.get("voiced", 0.0) or 0.0)
            centroid = float(feat.get("centroid", 0.5) or 0.5)
            kick = float(feat.get("kick", 0.0) or 0.0)
            hat = float(feat.get("hat", 0.0) or 0.0)

            decision = {"subsystems": {}}
            for s in SUBSYSTEMS:
                decision["subsystems"][s] = {"role": ROLE_SILENT, "level": 0.0,
                                              "preset_id": "", "group_ids": []}

            for vkey, vparams in voices.items():
                sub = voice_to_sub.get(vkey)
                if sub is None:
                    continue
                rate = float(vparams.get("rate", 0) or 0)
                topo = str(vparams.get("topo", "") or "")
                # 有 rate > 0 表示该子系统活跃
                if rate > 0:
                    decision["subsystems"][sub] = {
                        "role": ROLE_LEAD if rate > 500 else ROLE_SUPPORT,
                        "level": float(np.clip(rate / 3000.0, 0.0, 1.0)),
                        "preset_id": f"bench_{vkey}_{topo}",
                        "group_ids": [],
                    }

            segments.append({
                "t_start": float(case.get("t0", 0) or 0),
                "t_end": float(case.get("t1", 5) or 5),
                "section": "verse",
                "energy": energy,
                "decision": decision,
                # 嵌入 benchmark feat 数据供 FeatureExtractor 使用
                "_bench_feat": {
                    "percussive": perc,
                    "voiced": voiced,
                    "centroid": centroid,
                    "kick": kick,
                    "hat": hat,
                },
            })
        return segments

    # ── Logging ───────────────────────────────────────────────────────

    def _log(self, step: str, message: str, metrics=None):
        self.training_log.append(TrainingEvent(
            timestamp=time.time(),
            step=step,
            message=message,
            metrics=metrics,
        ))

    def get_log_messages(self) -> List[str]:
        """Return training log as formatted strings."""
        out = []
        for ev in self.training_log:
            ts = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
            out.append(f"[{ts}] {ev.message}")
        return out

    # ── Project scanning ──────────────────────────────────────────────

    @staticmethod
    def scan_projects(directory: str = ".") -> List[str]:
        """Find all .dmxt project files in directory."""
        pattern = os.path.join(directory, "*.dmxt")
        return sorted(glob.glob(pattern))

    @staticmethod
    def load_project_summary(dpath: str) -> Dict[str, Any]:
        """Load a project file and return summary (name, segments count, etc.).

        ★ 多源计数: baked_arrangement_data(current.segments) + preset_sets + clips。
        修复了键路径不匹配 (current 包装层) 和忽略非烘焙源的问题。
        """
        try:
            data = _load_project_json(dpath)
        except Exception:
            try:
                data = dict(np.load(dpath, allow_pickle=True).items())
            except Exception:
                return {"path": dpath, "name": os.path.basename(dpath), "segments": 0, "error": True}

        if not isinstance(data, dict):
            return {"path": dpath, "name": os.path.basename(dpath), "segments": 0, "error": True}

        total_segs = 0
        # 源1: baked_arrangement_data — 先读 current.segments, 回退直接读
        baked = data.get("baked_arrangement_data", {}) or {}
        if isinstance(baked, dict):
            cur = baked.get("current", {}) or {}
            segs = cur.get("segments", []) if isinstance(cur, dict) else []
            if not segs:
                segs = baked.get("segments", [])
            total_segs += len(segs) if isinstance(segs, list) else 0
            audio_sig = (cur.get("audio_sig", "") if isinstance(cur, dict) else ""
                        or baked.get("audio_sig", ""))
        else:
            audio_sig = ""

        # 源2: preset_sets 行数
        abs_state = data.get("audio_bridge_state", {}) or {}
        if isinstance(abs_state, dict):
            preset_sets = abs_state.get("preset_sets", {}) or {}
            for rows in (preset_sets.values() if isinstance(preset_sets, dict) else []):
                total_segs += len(rows) if isinstance(rows, list) else 0

        # 源3: clips
        clips = data.get("clips", []) or []
        total_segs += len(clips) if isinstance(clips, list) else 0

        return {
            "path": dpath, "name": os.path.basename(dpath),
            "segments": total_segs, "audio_sig": audio_sig, "error": False,
        }


# ═══ R183 批次G：工程 JSON 统一读取（模块级帮手）═══════════════════
def _load_project_json(dpath: str):
    """R183 批次G：.dmxt 工程 JSON 统一读取（orjson 优先，NaN/Infinity 回落 stdlib）。"""
    with open(dpath, "rb") as fh:
        raw = fh.read()
    try:
        return parse_project_json(raw)
    except Exception:
        return json.loads(raw)
