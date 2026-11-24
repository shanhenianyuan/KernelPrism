#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism — intelligent operator development platform for all GPU vendors (anvil app).

CLI entry: kp run / kp vendor
Output convention: anvil_output/kp-runs/<run_id>/ (user contract: never write under /tmp)
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
    """Per user convention: anvil_output/ relative to the current working directory. """
    here = os.getcwd()
    candidate = os.path.join(here, 'anvil_output')
    # Walk upward looking for an existing anvil_output (user convention: at project root)
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
    """run_id: kp-<vendor>-<task>-<YYYYMMDD>-<hex8> (spec 03 §9). """
    stamp = time.strftime('%Y%m%d')
    h = secrets.token_hex(4)
    return f"kp-{vendor}-{task}-{stamp}-{h}"


def cmd_vendor(args: argparse.Namespace) -> int:
    reg = VendorRegistry(args.anvil_root)
    if args.action == 'list':
        rows = reg.list_all()
        if not rows:
            print('(no registered adapters)')
            return 0
        # Print capability matrix with width-aligned columns
        hdr = f"{'vendor':<12}{'version':<10}{'devices':<24}{'stages':<20}health"
        print(hdr)
        print('-' * len(hdr))
        for r in rows:
            stages = ','.join(r['stages_list']) if r['stages_list'] else '(generic fallback)'
            print(f"{r['vendor_id']:<12}{r['version']:<10}{','.join(r['devices'])[:22]:<24}"
                  f"{stages:<20}{r['health']}")
        return 0
    if args.action == 'add':
        v = reg.add(args.vendor_id)
        print(f"Adapter registered: {v['vendor_id']} v{v['version']}  "
              f"stages={','.join(v['stages_list']) or '(generic fallback)'} "
              f"devices={','.join(v['devices'])}")
        return 0
    print(f"unknown vendor action: {args.action}", file=sys.stderr)
    return 2


def execute_run(vendor_id: str, task: str, exec_mode: str = 'paper',
                model_goal: str | None = None,
                budget_iters: int = 3, budget_minutes: float = 60.0,
                anvil_root: str = '') -> dict:
    """Run the 8-stage pipeline as a library call (no argv, no stdout side effects).

    This is the shared engine entry used by BOTH the `kp run` CLI and the MCP
    server — any agent framework can drive the same code path. Returns
    {'run_id', 'ok', 'run_dir'} so callers keep the @DONE@-contract semantics
    without parsing stdout.
    """
    reg = VendorRegistry(anvil_root)
    vendor = reg.load(vendor_id)
    if vendor is None:
        raise FileNotFoundError(
            f"adapter not found: {vendor_id} (run 'kp vendor add {vendor_id}' first)")

    run_root = Path(default_output_root()) / 'kp-runs'
    run_root.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id(vendor_id, task)
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'artifacts').mkdir(parents=True, exist_ok=True)

    runner = PipelineRunner(
        run_id=run_id,
        run_dir=str(run_dir),
        vendor=vendor,
        model_goal=model_goal,
        exec_mode=exec_mode,
        budget_iters=budget_iters,
        budget_minutes=budget_minutes,
    )
    ok = runner.run(task=task)
    return {'run_id': run_id, 'ok': ok, 'run_dir': str(run_dir)}


def cmd_run(args: argparse.Namespace) -> int:
    try:
        r = execute_run(
            vendor_id=args.vendor or 'ascend',
            task=args.task,
            exec_mode=args.exec_mode,
            model_goal=getattr(args, 'model_goal', None),
            budget_iters=getattr(args, 'budget_iters', 3),
            budget_minutes=getattr(args, 'budget_minutes', 60.0),
            anvil_root=args.anvil_root,
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not r['ok']:
        return 1
    print(f"\n@DONE@ run_id={r['run_id']} status=OK")
    print(f"Artifacts directory: {r['run_dir']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog='kp',
        description='KernelPrism — intelligent operator development platform for all GPU vendors (anvil app)',
    )
    p.add_argument('--anvil-root', default=os.environ.get('ANVIL_ROOT', ''),
                   help='anvil root dir (adapter discovery); auto-detected from the repo if omitted')
    sub = p.add_subparsers(dest='cmd', required=True)

    vp = sub.add_parser('vendor', help='manage vendor adapters')
    vsub = vp.add_subparsers(dest='action', required=True)
    vsub.add_parser('list', help='list adapters and capability matrix')
    addp = vsub.add_parser('add', help='register an adapter')
    addp.add_argument('vendor_id', nargs='?', default='ascend')

    rp = sub.add_parser('run', help='run the 8-stage pipeline')
    rp.add_argument('task', help='task name (e.g. add / matmul / gqa)')
    rp.add_argument('--vendor', default='ascend', help='vendor adapter id')
    rp.add_argument('--model-goal', default=None,
                    help='model-level goal (spec 03 §9 end-to-end intent, e.g. "throughput"); enables model-level closed-loop gating when set')
    rp.add_argument('--exec-mode', default='paper', choices=['paper', 'live'],
                    help='paper=paper smoke run (all 8 stages complete without hardware); live=real-device execution (requires probe available)')
    rp.add_argument('--budget-iters', type=int, default=3, help='stage-7 evolution iteration budget')
    rp.add_argument('--budget-minutes', type=float, default=60.0, help='global minute budget')

    mp = sub.add_parser('serve-mcp', help='run the MCP stdio server (agent-agnostic capability surface)')

    args = p.parse_args(argv)
    if args.cmd == 'vendor':
        return cmd_vendor(args)
    if args.cmd == 'run':
        return cmd_run(args)
    if args.cmd == 'serve-mcp':
        from mcp_server import serve  # noqa: PLC0415  (deferred: only when serving)
        return serve(args.anvil_root)
    return 2


if __name__ == '__main__':
    sys.exit(main())
