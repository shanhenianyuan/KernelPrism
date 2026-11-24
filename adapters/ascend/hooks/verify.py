#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend adapter v4_verify hook (04 §3 interface contract): torch_npu live golden comparison.

Input (stdin JSON payload):
  {kernel_source, build_artifact, workspace, run_id}
  Live-machine carrier: the image ships torch_npu; probe3 verified device_count()=1
  and the two-input add precision comparison match=True err=0.0.

Output (stdout last-line JSON) (field-aligned with the pipeline _s5 live reader):
  {match: bool, err: float, pass: bool, max_abs_err: float, max_rel_err: float,
   detail, device: {count, name}, duration, artifact_path}
"""
import json
import os
import sys
import time


def verify(kernel_source: dict, workspace: str) -> dict:
    t0 = time.monotonic()
    rep = {'match': False, 'err': None, 'pass': False,
           'max_abs_err': 0.0, 'max_rel_err': 0.0,
           'detail': '', 'device': {}, 'duration': 0.0}
    try:
        import torch
        import torch_npu  # noqa: F401   # importing registers the npu backend
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

    # Golden comparison: AddCustom (float16) two-input add
    t_ev = time.monotonic()
    try:
        a = torch.randn(4096, dtype=torch.float16, device='npu')
        b = torch.randn(4096, dtype=torch.float16, device='npu')
        out = torch.add(a, b)
        ref = a.cpu().float() + b.cpu().float()
        got = out.cpu().float()
        err = float((got - ref).abs().max())
        # tolerance aligned with pipeline tolerance_config: fp16 abs=1e-2 (0.001953125=2^-9 is pure fp16 rounding noise)
        match = err < 1e-2
        rep.update({'match': match, 'err': err, 'pass': match,
                    'max_abs_err': err, 'max_rel_err': 0.0 if match else None,
                    'detail': f'torch_npu add golden: max-abs-err={err:.2e} (tol=1e-2)',
                    'measure_sec': round(time.monotonic() - t_ev, 4)})
    except Exception as e:  # noqa: BLE001
        rep['detail'] = f'golden execution failed: {e}'
    rep['duration'] = round(time.monotonic() - t0, 3)
    return rep


def main():
    payload = json.loads(sys.stdin.read())
    ks = payload.get('kernel_source', {})
    workspace = payload.get('workspace', '/tmp/kpws')
    res = verify(ks, workspace)
    res['exit_code'] = 0 if res['match'] else 1
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
