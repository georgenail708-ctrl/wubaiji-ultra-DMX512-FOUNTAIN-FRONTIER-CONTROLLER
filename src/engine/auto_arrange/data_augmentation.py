"""
data_augmentation — 训练数据增强：从20样本扩展到200+
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


class FeatureNoiseAugmenter:
    """高斯噪声增强：对特征向量添加小扰动。"""

    def __init__(self, noise_scale: float = 0.05, copies_per_sample: int = 3):
        self.noise_scale = float(noise_scale)
        self.copies_per_sample = int(copies_per_sample)

    def augment(self, X: np.ndarray, y_role: Dict[str, np.ndarray],
                y_level: Dict[str, np.ndarray]) -> tuple:
        """为每个样本生成copies_per_sample个噪声变体。"""
        n_samples = X.shape[0]
        aug_X = [X]
        aug_y_role = {sub: [vals] for sub, vals in y_role.items()}
        aug_y_level = {sub: [vals] for sub, vals in y_level.items()}

        rng = np.random.RandomState(42)
        for _ in range(self.copies_per_sample):
            noise = rng.normal(0, self.noise_scale, X.shape)
            X_noisy = X + noise * np.std(X, axis=0)
            aug_X.append(np.clip(X_noisy, 0.0, 1.0))
            for sub in y_role:
                aug_y_role[sub].append(y_role[sub])
                aug_y_level[sub].append(y_level[sub])

        return (
            np.vstack(aug_X),
            {sub: np.concatenate(vals) for sub, vals in aug_y_role.items()},
            {sub: np.concatenate(vals) for sub, vals in aug_y_level.items()},
        )


class RoleMixupAugmenter:
    """角色mixup增强：插值两个样本的特征和标签。"""

    def __init__(self, alpha: float = 0.2, n_pairs: int = 30):
        self.alpha = float(alpha)
        self.n_pairs = int(n_pairs)

    def augment(self, X: np.ndarray, y_role: Dict[str, np.ndarray],
                y_level: Dict[str, np.ndarray]) -> tuple:
        n = X.shape[0]
        if n < 2:
            return X, y_role, y_level

        rng = np.random.RandomState(42)
        pairs = []
        for _ in range(self.n_pairs):
            i, j = rng.randint(0, n, 2)
            if i != j:
                pairs.append((i, j))

        if not pairs:
            return X, y_role, y_level

        mix_X = []
        mix_y_role = {sub: [] for sub in y_role}
        mix_y_level = {sub: [] for sub in y_level}

        for i, j in pairs:
            lam = rng.beta(self.alpha, self.alpha)
            mix_X.append(lam * X[i] + (1 - lam) * X[j])
            for sub in y_role:
                # 取众数(分类标签)
                mixed_role = y_role[sub][i] if lam >= 0.5 else y_role[sub][j]
                mix_y_role[sub].append(mixed_role)
                mixed_level = lam * y_level[sub][i] + (1 - lam) * y_level[sub][j]
                mix_y_level[sub].append(mixed_level)

        return (
            np.vstack([X] + [np.array(mix_X)]),
            {sub: np.concatenate([y_role[sub], np.array(mix_y_role[sub], dtype=y_role[sub].dtype)])
             for sub in y_role},
            {sub: np.concatenate([y_level[sub], np.array(mix_y_level[sub], dtype=y_level[sub].dtype)])
             for sub in y_level},
        )


def augment_training_data(X: np.ndarray, y_role: Dict[str, np.ndarray],
                          y_level: Dict[str, np.ndarray]) -> tuple:
    """完整增强管线：噪声 + mixup。目标20→200+样本。"""
    noise_aug = FeatureNoiseAugmenter(noise_scale=0.05, copies_per_sample=3)
    X, y_role, y_level = noise_aug.augment(X, y_role, y_level)

    mixup_aug = RoleMixupAugmenter(alpha=0.2, n_pairs=min(50, X.shape[0] * 3))
    X, y_role, y_level = mixup_aug.augment(X, y_role, y_level)

    return X, y_role, y_level
