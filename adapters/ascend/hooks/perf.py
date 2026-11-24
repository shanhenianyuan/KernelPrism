#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend 适配器 v5_profile hook（04 §3 接口契约）：真实计时。

输入(stdin JSON payload):
  {kernel_source, build_artifact, workspace, run_id}

输出(stdout 最后一行 JSON):
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
        rep['detail'] = f'torch_npu 不可用: {e}'
        rep['duration'] = round(time.monotonic() - t0, 3)
        return rep

    try:
        n = torch.npu.device_count()
        rep['device']['count'] = n
        if n < 1:
            rep['detail'] = '未发现可用 NPU'
            rep['duration'] = round(time.monotonic() - t0, 3)
            return rep
        rep['device']['name'] = torch.npu.get_device_name(0)
    except Exception as e:  # noqa: BLE001
        rep['detail'] = f'NPU 枚举失败: {e}'
        rep['duration'] = round(time.monotonic() - t0, 3)
        return rep

    # 稳定计时三件套（借鉴 Kernel-Smith 评估协议，2026-11-23）：
    #   ① warm-up 预跑消除初始化抖动; ② 多轮采样取中位数 + 剔除离群; ③ 报告
    #   波动(median/std)供段6 门禁与优化段决策使用。
    # 被测基准 = torch.add(4096, fp16) 原生算子（当前适配器占位基准）。
    n_iter = 50        # 每轮迭代数
    warmup = 20        # warm-up 预跑（不计数）
    rounds = 7         # 采样轮数（独立计时, 取中位数）
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
        # 去离群（median ± 2·std 之外剔除），再取 median 为稳定值
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
            'detail': (f'add(4096,fp16) 稳定计时: median={per_iter_ms:.4f} ms/iter '
                       f'(warmup={warmup}, rounds={rounds}, 去离群 {n_outliers}/{n}, '
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
        rep['detail'] = f'profile 执行失败: {e}'
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
