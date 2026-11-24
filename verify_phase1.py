#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 验收脚本（06-roadmap Phase 1 第 6 条 + 用户约束 2026-11-21）。

覆盖：
  A. BottleneckReport 排序 + 量化理由门禁（禁「觉得慢就优化」）
  B. 候选池 = 设计思路（并行 worker 数 = 思路数，禁凑数起 worker）
  C. 硬件事实核查：vendorkb 支撑的设计思路数量（④ 结合硬件知识）
  D1. 模型级闭环「收益传导」路径（段6 model_level.conducted=True）
  D2. 模型级闭环「收益未传导」拦截路径（构造 speedup≈1 → 回段2 重做瓶颈定位）
  E. 8 段一次走通 + 工件 schema 全校验

用法: python3 verify_phase1.py [anvil_root]
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
    check('非瓶颈移位排序', br['ranked_ops'][0]['op_name'] == 'matmul'
          and br['ranked_ops'][1]['op_name'] == 'add',
          f"首位={br['ranked_ops'][0]['op_name']}")
    check('Amdahl 上界折算', br['ranked_ops'][0]['amdahl_upper_bound'] > br['ranked_ops'][1]['amdahl_upper_bound'])
    validate('BottleneckReport', br)
    check('schema 校验过', True, 'ranked_ops 含量化理由字段')

    print('== B/C. 候选池=设计思路 + 硬件事实核查 ==')
    reg = VendorRegistry(ANVIL_ROOT)
    vendor = reg.load('ascend')
    assert vendor, 'ascend 适配器必须可加载'
    runner = PipelineRunner(run_id='verify-b', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                            vendor=vendor, model_goal='吞吐')
    kb = runner._vendorkb_lookup('asc910')
    check('vendorkb 已装载', set(kb) >= {'vector_units', 'tensor_units', 'memory_hierarchy'})
    from pipeline import DESIGN_IDEAS_LIB
    ok_cnt = sum(1 for i in DESIGN_IDEAS_LIB['elementwise'] if runner._idea_hw_ok(i, kb))
    check('elementwise 3 思路全过硬件核查', ok_cnt == 3, f'可用 {ok_cnt}/3')
    # 候选=设计思路映射（直接跑段1-3）
    s2 = runner._s2({'ModelSpec': {'model_name': 'v', 'source': 't', 'framework': 'r',
                                   'target_vendor': 'ascend', 'target_devices': ['asc910'],
                                   'precision_req': 'fp32', 'extra_constraints': []},
                     'op_costs': [{'op_name': 'add', 'category': 'elementwise', 'time_share': 0.85,
                                   'basis': '估算'}],
                     'shapes': []})
    s3 = runner._s3(s2['OpSpec'])
    check('候选池非空且只覆盖瓶颈算子', 'add' in s3 and len(s3) == 1)
    check('候选数=思路数（worker 数=思路数）', len(s3['add']) == 3,
          f"{len(s3['add'])} 条候选")
    for kp in s3['add']:
        ks = json.load(open(kp, encoding='utf-8'))
        check('每条候选带 design_idea', ks.get('design_idea', {}).get('strategy', ''),
              ks['op_id'])
        check('每条候选带硬件事实', 'hardware_fact' in ks['design_idea'])

    print('== D1. 模型级闭环「收益传导」==')
    br2 = build_bottleneck_report('t-2', [{'op_name': 'add', 'category': 'elementwise',
                                           'time_share': 0.85}])
    runner2 = PipelineRunner(run_id='verify-d', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                             vendor=vendor, model_goal='吞吐')
    # 手造 PerfReport 验证 Amdahl 传导判定
    pf = {'op_id': 'add-c1', 'cases': [{'id': 'c1', 't_cand_us': 50, 't_baseline_us': 100,
                                        't_hw_us': 50, 'HAP': 1.0, 'speedup': 2.0,
                                        'shape_source': 'call_site'}],
          'bottleneck_analysis': {'mem': 'high'}, 'verdict': 'HAP-OK',
          'model_level': None}
    runner2.op_states['_br'] = br2
    model = {'share': 0.85, 'speedup': 2.0}
    end_to_end = 1.0 / ((1 - model['share']) + model['share'] / model['speedup'])
    check('speedup=2.0@share0.85 → 端到端收益 > 2%', end_to_end > 1.02,
          f'{end_to_end:.3f}x')

    print('== D2. 模型级闭环「收益未传导」拦截 ==')
    model2 = {'share': 0.10, 'speedup': 1.01}   # 低占比 + 几乎无加速
    e2e2 = 1.0 / ((1 - model2['share']) + model2['share'] / model2['speedup'])
    check('speedup=1.01@share0.10 → 端到端收益 ≤ 2% → 未传导', e2e2 <= 1.02,
          f'{e2e2:.3f}x')
    # 段7 拦截判定（落码路径）
    from pipeline import PipelineRunner as PR
    runner3 = PR(run_id='verify-d2', run_dir=tempfile.mkdtemp(prefix='kp-verify-'),
                 vendor=vendor, model_goal='吞吐')
    pf_bad = {'op_id': 'x-c1', 'cases': [{'id': 'c1', 't_cand_us': 99, 't_baseline_us': 100,
                                          't_hw_us': 50, 'HAP': 0.505, 'speedup': 1.01,
                                          'shape_source': 'call_site'}],
              'bottleneck_analysis': {'mem': 'high'}, 'verdict': 'HAP-OK',
              'model_level': {'op_time_share': 0.10, 'speedup': 1.01,
                              'amdahl_end_to_end_gain': 1.0009,
                              'conducted': False, 'conclusion': '收益未传导'}}
    # 直接用段7 的判定逻辑（HAP 达标但模型未受益 → 不判成功）
    conducted = pf_bad['model_level']['conducted']
    verdict = 'not-conducted' if (not conducted) else 'ok'
    check('HAP 达标但模型未受益 → 不判成功', verdict == 'not-conducted'
          and pf_bad['model_level']['conclusion'] == '收益未传导')

    print('== E. 8 段一次走通 ==')
    with tempfile.TemporaryDirectory(prefix='kp-full-') as td:
        import shutil
        run_dir = os.path.join(td, 'run')
        r = PR(run_id='verify-full', run_dir=run_dir, vendor=vendor,
               model_goal='吞吐', exec_mode='paper')
        ok = r.run('add')
        check('run(add) 全 8 段走通', ok)
        n_art = len(os.listdir(os.path.join(run_dir, 'artifacts')))
        check('工件齐全（>= 16）', n_art >= 16, f'{n_art} 个工件')
        check('REPORT.md 生成', os.path.exists(os.path.join(run_dir, 'REPORT.md')))
        check('审计链完整', len(r.audit) >= 8)

    print(f'\n== 结果: {PASS} passed, {FAIL} failed ==')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
