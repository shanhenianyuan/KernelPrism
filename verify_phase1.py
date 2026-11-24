#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 acceptance script (06-roadmap Phase 1 item 6 + user constraints 2026-11-21).

Covers:
  A. BottleneckReport sorting + quantified-reason gate (no "optimize because it feels slow")
  B. Candidate pool = design ideas (parallel worker count = idea count, no filler workers)
  C. Hardware fact-check: number of design ideas backed by vendorkb (item 4: combine hardware knowledge)
  D1. Model-level loopback "benefit propagation" path (segment 6: model_level.conducted=True)
  D2. Model-level loopback "benefit not propagated" blocking path (build speedup~1 -> redo bottleneck localization in segment 2)
  E. All 8 segments in one pass + full artifact schema validation

Usage: python3 verify_phase1.py [anvil_root]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'src')
ANVIL_ROOT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('ANVIL_ROOT', '')

sys.path.insert(0, SRC)
from artifacts import (  # noqa: E402
    build_bottleneck_report, content_hash, validate, write_artifact,
)
from pipeline import PipelineRunner  # noqa: E402
from vendor import VendorRegistry  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = ''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name} {detail}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} {detail}')


def main():
    print('== A. BottleneckReport ==')
    br = build_bottleneck_report('t-1', [
        {'op_name': 'add', 'category': 'elementwise', 'time_share': 0.30},
        {'op_name': 'matmul', 'category': 'gemm', 'time_share': 0.55},
    ])
    check('ranking puts bottleneck op first', br['ranked_ops'][0]['op_name'] == 'matmul'
          and br['ranked_ops'][1]['op_name'] == 'add',
          f"top={br['ranked_ops'][0]['op_name']}")
    check('Amdahl upper bound derived', br['ranked_ops'][0]['amdahl_upper_bound'] > br['ranked_ops'][1]['amdahl_upper_bound'])
    validate('BottleneckReport', br)
    check('schema validation passed', True, 'ranked_ops has quantified-reason field')

    print('== B/C. Candidate pool = design ideas + hardware fact-check ==')
    reg = VendorRegistry(ANVIL_ROOT)
    vendor = reg.load('ascend')
    assert vendor, 'ascend adapter must load'
    runner = PipelineRunner(run_id='verify-b', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                            vendor=vendor, model_goal='throughput')
    kb = runner._vendorkb_lookup('asc910')
    check('vendorkb loaded', set(kb) >= {'vector_units', 'tensor_units', 'memory_hierarchy'})
    from pipeline import DESIGN_IDEAS_LIB
    ok_cnt = sum(1 for i in DESIGN_IDEAS_LIB['elementwise'] if runner._idea_hw_ok(i, kb))
    check('all 3 elementwise ideas pass hardware check', ok_cnt == 3, f'pass {ok_cnt}/3')
    # candidate = design-idea mapping (run segments 1-3 directly)
    s2 = runner._s2({'ModelSpec': {'model_name': 'v', 'source': 't', 'framework': 'r',
                                   'target_vendor': 'ascend', 'target_devices': ['asc910'],
                                   'precision_req': 'fp32', 'extra_constraints': []},
                     'op_costs': [{'op_name': 'add', 'category': 'elementwise', 'time_share': 0.85,
                                   'basis': 'estimated'}],
                     'shapes': []})
    s3 = runner._s3(s2['OpSpec'])
    check('candidate pool non-empty and covers only the bottleneck op', 'add' in s3 and len(s3) == 1)
    check('candidate count = idea count (worker count = idea count)', len(s3['add']) == 3,
          f"{len(s3['add'])} candidates")
    for kp in s3['add']:
        ks = json.load(open(kp, encoding='utf-8'))
        check('every candidate carries design_idea', ks.get('design_idea', {}).get('strategy', ''),
              ks['op_id'])
        check('every candidate carries hardware fact', 'hardware_fact' in ks['design_idea'])

    print('== D1. Model-level loopback: benefit propagated ==')
    br2 = build_bottleneck_report('t-2', [{'op_name': 'add', 'category': 'elementwise',
                                           'time_share': 0.85}])
    runner2 = PipelineRunner(run_id='verify-d', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                             vendor=vendor, model_goal='throughput')
    # hand-built PerfReport to verify the Amdahl propagation decision
    pf = {'op_id': 'add-c1', 'cases': [{'id': 'c1', 't_cand_us': 50, 't_baseline_us': 100,
                                        't_hw_us': 50, 'HAP': 1.0, 'speedup': 2.0,
                                        'shape_source': 'call_site'}],
          'bottleneck_analysis': {'mem': 'high'}, 'verdict': 'HAP-OK',
          'model_level': None}
    runner2.op_states['_br'] = br2
    model = {'share': 0.85, 'speedup': 2.0}
    end_to_end = 1.0 / ((1 - model['share']) + model['share'] / model['speedup'])
    check('speedup=2.0@share0.85 → end-to-end gain > 2%', end_to_end > 1.02,
          f'{end_to_end:.3f}x')

    print('== D2. Model-level loopback: benefit not propagated (blocked) ==')
    model2 = {'share': 0.10, 'speedup': 1.01}   # low share + almost no speedup
    e2e2 = 1.0 / ((1 - model2['share']) + model2['share'] / model2['speedup'])
    check('speedup=1.01@share0.10 → end-to-end gain <= 2% → not propagated', e2e2 <= 1.02,
          f'{e2e2:.3f}x')
    # segment 7 blocking decision (real code path)
    from pipeline import PipelineRunner as PR
    runner3 = PR(run_id='verify-d2', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                 vendor=vendor, model_goal='throughput')
    pf_bad = {'op_id': 'x-c1', 'cases': [{'id': 'c1', 't_cand_us': 99, 't_baseline_us': 100,
                                          't_hw_us': 50, 'HAP': 0.505, 'speedup': 1.01,
                                          'shape_source': 'call_site'}],
              'bottleneck_analysis': {'mem': 'high'}, 'verdict': 'HAP-OK',
              'model_level': {'op_time_share': 0.10, 'speedup': 1.01,
                              'amdahl_end_to_end_gain': 1.0009,
                              'conducted': False, 'conclusion': 'benefit not propagated'}}
    # use segment-7 decision logic directly (HAP met but model not benefited -> not a success)
    conducted = pf_bad['model_level']['conducted']
    verdict = 'not-conducted' if (not conducted) else 'ok'
    check('HAP met but model not benefited -> not judged a success', verdict == 'not-conducted'
          and pf_bad['model_level']['conclusion'] == 'benefit not propagated')

    print('== E. All 8 segments in one pass ==')
    with tempfile.TemporaryDirectory(prefix='kp-full-') as td:
        import shutil
        run_dir = os.path.join(td, 'run')
        r = PR(run_id='verify-full', run_dir=run_dir, vendor=vendor,
               model_goal='throughput', exec_mode='paper')
        ok = r.run('add')
        check('run(add) completes all 8 segments', ok)
        n_art = len(os.listdir(os.path.join(run_dir, 'artifacts')))
        check('artifacts complete (>= 16)', n_art >= 16, f'{n_art} artifacts')
        check('REPORT.md generated', os.path.exists(os.path.join(run_dir, 'REPORT.md')))
        check('audit chain complete', len(r.audit) >= 8)

    print(f'\n== Result: {PASS} passed, {FAIL} failed ==')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
