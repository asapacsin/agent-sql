"""Statistics (docs/spec/BENCHMARK.md section 6): Wilson CI, paired bootstrap, exact McNemar, Holm."""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.stats import binomtest

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_mean_ci(values: Sequence[float], resamples: int = 10000, seed: int = 20261123,
                      alpha: float = 0.05) -> tuple[float, float, float]:
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return (float("nan"),) * 3
    rng = np.random.Generator(np.random.PCG64(seed))
    idx = rng.integers(0, x.size, size=(resamples, x.size))
    means = x[idx].mean(axis=1)
    return (float(x.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def paired_bootstrap_diff(a: Sequence[float], b: Sequence[float], resamples: int = 10000,
                          seed: int = 20261123) -> tuple[float, float, float]:
    """Mean of (a - b) over tasks, resampling tasks; a/b are per-task outcomes (e.g. mean success over runs)."""
    if len(a) != len(b):
        raise ValueError("paired samples must align by task")
    return bootstrap_mean_ci([x - y for x, y in zip(a, b, strict=False)], resamples, seed)


def ratio_of_means_ci(a: Sequence[float], b: Sequence[float], resamples: int = 10000,
                      seed: int = 20261123) -> tuple[float, float, float]:
    x, y = np.asarray(a, float), np.asarray(b, float)
    if x.size == 0 or y.sum() == 0:
        return (float("nan"),) * 3
    rng = np.random.Generator(np.random.PCG64(seed))
    idx = rng.integers(0, x.size, size=(resamples, x.size))
    with np.errstate(divide="ignore", invalid="ignore"):
        r = x[idx].mean(axis=1) / y[idx].mean(axis=1)
    r = r[np.isfinite(r)]
    return (float(x.mean() / y.mean()), float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975)))


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> dict:
    """Exact two-sided McNemar on paired binary outcomes (binomial test on discordant pairs)."""
    if len(a) != len(b):
        raise ValueError("paired samples must align by task")
    only_a = sum(1 for x, y in zip(a, b, strict=False) if x and not y)
    only_b = sum(1 for x, y in zip(a, b, strict=False) if y and not x)
    n = only_a + only_b
    p = 1.0 if n == 0 else float(binomtest(only_a, n, 0.5, alternative="two-sided").pvalue)
    return {"a_only": only_a, "b_only": only_b, "discordant": n, "p_value": p}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (monotone)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[k] = running
    return adjusted
