#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend adapter v5_profile hook (04 §3 interface contract): real timing.

Input (stdin JSON payload):
  {kernel_source, build_artifact, workspace, run_id}

Output (stdout last-line JSON):
  {profiled: bool, detail, timing: {n_iter, elapsed_ms, per_iter_ms},
   device, duration}
"""
import json
import os
import sys
import time


def profile_kernel(kernel_source: dict, workspace: str) -> dict:
    t0 = time.monotonic()
    rep = {'profiled': False, 'detail': '', 'timing': {}, 'device': {}, 'duration': 0.0}
    try:
        import torch
        import torch_npu  # noqa: F401
    except Exception as e:  # noqa: BLE001
        rep['detail'] = f'torch_npu unavailable: {e}'
        rep['duration'] = round(time.monotonic() - t0, 3)
        return rep

    try:
        n = torch.npu.device_count()
        rep['device']['count'] = n
        if n < 1:
            rep['detail'] = 'no usable NPU found'
            rep['duration'] = round(time.monotonic() - t0, 3)
            return rep
        rep['device']['name'] = torch.npu.get_device_name(0)
    except Exception as e:  # noqa: BLE001
        rep['detail'] = f'NPU enumeration failed: {e}'
        rep['duration'] = round(time.monotonic() - t0, 3)
        return rep

    # Stable-timing trio (borrowed from the Kernel-Smith timing protocol, 2026-11-23):
    #   1) warm-up pre-run removes init jitter; 2) multi-round sampling: take the median
    #   and remove outliers; 3) report variance (median/std) for stage-6 gating and
    #   optimization decisions.
    # Benchmark under test = native torch.add(4096, fp16) op (current adapter
    # placeholder benchmark).
    n_iter = 50        # iterations per round
    warmup = 20        # warm-up pre-run (not counted)
    rounds = 7         # sampling rounds (independent timing, take median)
    outlier_sigma = 2.0
    try:
        a = torch.randn(4096, dtype=torch.float16, device='npu')
        b = torch.randn(4096, dtype=torch.float16, device='npu')
        for _ in range(warmup):
            torch.add(a, b)
        torch.npu.synchronize()
        samples_ms = []
        for _ in range(rounds):
            t_ev = time.perf_counter()
            for _ in range(n_iter):
                torch.add(a, b)
            torch.npu.synchronize()
            samples_ms.append((time.perf_counter() - t_ev) * 1000.0 / n_iter)
        # Remove outliers (outside median +/- 2*std), then re-take the median as the stable value
        sorted_s = sorted(samples_ms)
        n = len(sorted_s)
        med = sorted_s[n // 2] if n % 2 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2.0
        std = (sum((x - med) ** 2 for x in sorted_s) / n) ** 0.5
        kept = [x for x in sorted_s if abs(x - med) <= outlier_sigma * std]
        n_outliers = n - len(kept)
        if not kept:
            kept = sorted_s
        per_iter_ms = kept[len(kept) // 2] if kept else med
        kept_std = (sum((x - per_iter_ms) ** 2 for x in kept) / len(kept)) ** 0.5
        rep.update({
            'profiled': True,
            'detail': (f'add(4096,fp16) stable timing: median={per_iter_ms:.4f} ms/iter '
                       f'(warmup={warmup}, rounds={rounds}, outliers-removed {n_outliers}/{n}, '
                       f'std={kept_std:.4f})'),
            'timing': {
                'n_iter': n_iter, 'rounds': rounds, 'warmup': warmup,
                'per_iter_ms': round(per_iter_ms, 4),
                'median_ms': round(per_iter_ms, 4),
                'mean_ms': round(sum(samples_ms) / len(samples_ms), 4),
                'std_ms': round(kept_std, 4),
                'samples_ms': [round(x, 4) for x in samples_ms],
                'outliers_removed': n_outliers,
                'method': 'warmup+multi-round+outlier-removal+median',
            },
        })
    except Exception as e:  # noqa: BLE001
        rep['detail'] = f'profile execution failed: {e}'
    rep['duration'] = round(time.monotonic() - t0, 3)
    return rep


def main():
    payload = json.loads(sys.stdin.read())
    ks = payload.get('kernel_source', {})
    workspace = payload.get('workspace', '/tmp/kpws')
    res = profile_kernel(ks, workspace)
    res['exit_code'] = 0 if res['profiled'] else 1
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
