#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism — 全 GPU 厂商智能算子开发平台（anvil 的 app）

CLI 入口: kp run / kp vendor
输出约定: anvil_output/kp-runs/<run_id>/（用户输出铁律, 不落 /tmp）
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifacts import (  # noqa: E402
    load_artifact, validate, validate_BottleneckReport, write_artifact,
)
from pipeline import PipelineRunner  # noqa: E402
from vendor import VendorRegistry  # noqa: E402


def default_output_root() -> str:
    """遵循用户约定: 相对当前工作目录的 anvil_output/. """
    here = os.getcwd()
    candidate = os.path.join(here, 'anvil_output')
    # 向上找已有 anvil_output（用户约定在项目根）
    for _ in range(6):
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            break
        candidate = os.path.join(parent, 'anvil_output')
        here = parent
    return os.path.join(os.getcwd(), 'anvil_output')


def make_run_id(vendor: str, task: str) -> str:
    """run_id: kp-<vendor>-<task>-<YYYYMMDD>-<hex8>（03 §9）. """
    stamp = time.strftime('%Y%m%d')
    h = secrets.token_hex(4)
    return f"kp-{vendor}-{task}-{stamp}-{h}"


def cmd_vendor(args: argparse.Namespace) -> int:
    reg = VendorRegistry(args.anvil_root)
    if args.action == 'list':
        rows = reg.list_all()
        if not rows:
            print('(无已注册适配器)')
            return 0
        # 宽度对齐打印能力矩阵
        hdr = f"{'vendor':<12}{'version':<10}{'devices':<24}{'stages':<20}health"
        print(hdr)
        print('-' * len(hdr))
        for r in rows:
            stages = ','.join(r['stages_list']) if r['stages_list'] else '(通用兜底)'
            print(f"{r['vendor_id']:<12}{r['version']:<10}{','.join(r['devices'])[:22]:<24}"
                  f"{stages:<20}{r['health']}")
        return 0
    if args.action == 'add':
        v = reg.add(args.vendor_id)
        print(f"适配器已登记: {v['vendor_id']} v{v['version']}  "
              f"stages={','.join(v['stages_list']) or '(通用兜底)'} "
              f"devices={','.join(v['devices'])}")
        return 0
    print(f"未知 vendor 动作: {args.action}", file=sys.stderr)
    return 2


def cmd_run(args: argparse.Namespace) -> int:
    reg = VendorRegistry(args.anvil_root)
    vendor_id = args.vendor or 'ascend'
    vendor = reg.load(vendor_id)
    if vendor is None:
        print(f"适配器未找到: {vendor_id}（先 kp vendor add {vendor_id}）", file=sys.stderr)
        return 1

    # 模型级意图（03 §9 端到端意图 entry）: --model-goal '吞吐'
    model_goal = getattr(args, 'model_goal', None)

    run_root = Path(default_output_root()) / 'kp-runs'
    run_root.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id(vendor_id, args.task)
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    runner = PipelineRunner(
        run_id=run_id,
        run_dir=str(run_dir),
        vendor=vendor,
        model_goal=model_goal,
        exec_mode=args.exec_mode,
        budget_iters=getattr(args, 'budget_iters', 3),
        budget_minutes=getattr(args, 'budget_minutes', 60),
    )

    ok = runner.run(task=args.task)
    if not ok:
        return 1
    print(f"\n@DONE@ run_id={run_id} status=OK")
    print(f"产物目录: {run_dir}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog='kp',
        description='KernelPrism — 全 GPU 厂商智能算子开发平台 (anvil app)',
    )
    p.add_argument('--anvil-root', default=os.environ.get('ANVIL_ROOT', ''),
                   help='anvil 根目录（适配器定位）；缺省仓库自探测')
    sub = p.add_subparsers(dest='cmd', required=True)

    vp = sub.add_parser('vendor', help='厂商适配器管理')
    vsub = vp.add_subparsers(dest='action', required=True)
    vsub.add_parser('list', help='列出适配器与能力矩阵')
    addp = vsub.add_parser('add', help='登记适配器')
    addp.add_argument('vendor_id', nargs='?', default='ascend')

    rp = sub.add_parser('run', help='跑 8 段流水线')
    rp.add_argument('task', help='任务名（如 add / matmul / gqa）')
    rp.add_argument('--vendor', default='ascend', help='厂商适配器 id')
    rp.add_argument('--model-goal', default=None,
                    help='模型级目标（03 §9 端到端意图，如"吞吐"）；给出后启用模型级闭环门禁')
    rp.add_argument('--exec-mode', default='paper', choices=['paper', 'live'],
                    help='paper=纸面冒烟（无硬件也全 8 段走通）；live=真机执行（需 probe available）')
    rp.add_argument('--budget-iters', type=int, default=3, help='段7 进化迭代预算')
    rp.add_argument('--budget-minutes', type=float, default=60.0, help='全局分钟预算')

    args = p.parse_args(argv)
    if args.cmd == 'vendor':
        return cmd_vendor(args)
    if args.cmd == 'run':
        return cmd_run(args)
    return 2


if __name__ == '__main__':
    sys.exit(main())
