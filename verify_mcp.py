#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP smoke verification for KernelPrism's agent-agnostic capability surface.

Acts as a minimal MCP client over stdio (line-delimited JSON-RPC 2.0) and proves:
  1. initialize handshake returns protocol/capabilities/serverInfo
  2. tools/list exposes the KernelPrism tool set
  3. tools/call kp_vendor_list returns the adapter capability matrix
  4. tools/call kp_run drives the real 8-stage pipeline (paper mode, small budget)
     and returns machine-readable status (run_id / status / artifacts_dir)
  5. tools/call kp_audit_read returns the @DONE@-contract audit trail as JSON

Any MCP-capable agent framework (Claude Code, Codex, Cursor, Cline, Continue, ...)
goes through exactly the same frames this script sends.

Usage: python3 verify_mcp.py [--vendor lingspeed]
Exit code 0 = all checks passed; non-zero = FAIL=<n>.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
FAIL = 0


def check(name: str, cond: bool, detail: str = '') -> None:
    global FAIL
    if cond:
        print(f'PASS  {name}')
    else:
        FAIL += 1
        print(f'FAIL  {name}  {detail}')


def reply_line(proc) -> dict:
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError('MCP server closed stdout unexpectedly')
    return json.loads(line)


def rpc(proc, msg_id: int, method: str, params=None) -> dict:
    frame = {'jsonrpc': '2.0', 'id': msg_id, 'method': method}
    if params is not None:
        frame['params'] = params
    proc.stdin.write(json.dumps(frame) + '\n')
    proc.stdin.flush()
    return reply_line(proc)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vendor', default='lingspeed', help='vendor adapter for the kp_run probe')
    p.add_argument('--task', default='add', help='task for the kp_run probe')
    args = p.parse_args()

    cmd = [sys.executable, os.path.join('src', 'kp.py'), 'serve-mcp']
    proc = subprocess.Popen(cmd, cwd=REPO, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    try:
        # 1. initialize handshake
        t0 = time.time()
        r = rpc(proc, 1, 'initialize', {'protocolVersion': '2024-11-05'})
        check('initialize -> result', 'result' in r and 'error' not in r,
              f'got: {r}')
        if 'result' in r:
            res = r['result']
            check('initialize -> capabilities.tools', 'tools' in res.get('capabilities', {}))
            check('initialize -> serverInfo.name=kernelprism',
                  res.get('serverInfo', {}).get('name') == 'kernelprism')

        # 2. initialized notification (no reply expected; none should come)
        proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        proc.stdin.flush()

        # 3. tools/list
        r = rpc(proc, 2, 'tools/list')
        tools = r.get('result', {}).get('tools', [])
        names = [t.get('name') for t in tools]
        check('tools/list -> 3 tools', names == ['kp_vendor_list', 'kp_run', 'kp_audit_read'],
              f'got: {names}')
        kprun = next((t for t in tools if t.get('name') == 'kp_run'), None)
        check('kp_run inputSchema declares task/vendor/exec_mode',
              kprun is not None and
              set(kprun.get('inputSchema', {}).get('properties', {})) >=
              {'task', 'vendor', 'exec_mode'})

        # 4. tools/call kp_vendor_list
        r = rpc(proc, 3, 'tools/call',
                {'name': 'kp_vendor_list', 'arguments': {}})
        cand = r.get('result', {}) or {}
        text = ''
        for c in cand.get('content', []):
            if c.get('type') == 'text':
                text += c.get('text', '')
        check('kp_vendor_list -> isError=false', cand.get('isError') is False)
        vend = json.loads(text) if text else {}
        check(f'kp_vendor_list -> adapters contain {args.vendor}',
              any(a.get('vendor_id') == args.vendor for a in vend.get('adapters', [])),
              f'got: {[a.get("vendor_id") for a in vend.get("adapters", [])]}')

        # 5. tools/call kp_run (real 8-stage pipeline, paper mode, tiny budget)
        print(f'... kp_run probe: vendor={args.vendor} task={args.task} exec_mode=paper '
              f'budget_iters=1 (this runs the real pipeline; may take a while)')
        t0 = time.time()
        r = rpc(proc, 4, 'tools/call', {
            'name': 'kp_run',
            'arguments': {'task': args.task, 'vendor': args.vendor,
                          'exec_mode': 'paper', 'budget_iters': 1,
                          'budget_minutes': 20.0},
        })
        cand = r.get('result', {}) or {}
        text = ''
        for c in cand.get('content', []):
            if c.get('type') == 'text':
                text += c.get('text', '')
        run = json.loads(text) if text else {}
        elapsed = time.time() - t0
        check(f'kp_run -> status=OK (elapsed {elapsed:.0f}s)',
              run.get('status') == 'OK',
              f'got: {run}')
        run_id = run.get('run_id', '')

        # 6. tools/call kp_audit_read
        r = rpc(proc, 5, 'tools/call',
                {'name': 'kp_audit_read', 'arguments': {'run_id': run_id}})
        cand = r.get('result', {}) or {}
        text = ''
        for c in cand.get('content', []):
            if c.get('type') == 'text':
                text += c.get('text', '')
        audit = json.loads(text) if text else {}
        entries = audit.get('entries', [])
        check('kp_audit_read -> entries non-empty (8-stage audit trail)',
              len(entries) >= 8, f'count={len(entries)}')
        last_runtime = [e for e in entries if e.get('stage') == 'runtime']
        check('kp_audit_read -> completion marker (runtime level=ok, all 8 stages done)',
              bool(last_runtime) and last_runtime[-1].get('level') == 'ok'
              and 'all 8 stages completed' in str(last_runtime[-1].get('msg', '')),
              f'last runtime: {last_runtime[-1] if last_runtime else None}')

        # 7. ping keeps the channel alive
        r = rpc(proc, 6, 'ping')
        check('ping -> result={}', r.get('result') == {})

    finally:
        try:
            proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 100, 'method': 'shutdown'}) + '\n')
            proc.stdin.flush()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print('')
    if FAIL == 0:
        print('verify_mcp: ALL CHECKS PASSED (any MCP agent framework can drive KernelPrism)')
        return 0
    print(f'verify_mcp: FAIL={FAIL}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
