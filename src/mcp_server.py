#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism MCP (Model Context Protocol) stdio server — the agent-agnostic capability surface.

Allows ANY agent framework (Claude Code, Codex, Cursor, Cline, Continue, anvil, ...)
to drive KernelPrism over line-delimited JSON-RPC 2.0 on stdin/stdout, per the MCP
spec, with zero anvil-specific glue.

Why MCP: it is the de-facto interoperable protocol spoken natively by every major
agent framework in 2025+. Exposing KernelPrism through MCP makes the "callable by any
agent framework" claim a protocol fact, not a README promise.

Zero third-party dependencies: this server is hand-rolled from the stdlib (json only),
preserving the platform's stdlib-only rule. Stdout carries protocol frames ALONE;
every diagnostic goes to stderr.

Exposed tools (each mirrors an existing `kp` CLI contract):
  kp_vendor_list  -> capability matrix of registered vendor adapters (`kp vendor list`)
  kp_run          -> run the 8-stage pipeline             (`kp run <task> --vendor ...`)
  kp_audit_read   -> machine-readable audit.jsonl of a run (the @DONE@-contract trace)

Run:   python3 src/kp.py serve-mcp
Smoke: python3 verify_mcp.py
"""
from __future__ import annotations

import json
import os
import sys

PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'kernelprism'
SERVER_VERSION = '1.0.0'

TOOLS = [
    {
        'name': 'kp_vendor_list',
        'description': 'List registered vendor adapters and their capability matrix '
                       '(equivalent of `kp vendor list`).',
        'inputSchema': {
            'type': 'object',
            'properties': {},
            'additionalProperties': False,
        },
    },
    {
        'name': 'kp_run',
        'description': 'Run the KernelPrism 8-stage pipeline '
                       '(understand -> analyze -> generate -> compile -> verify -> '
                       'profile -> optimize -> register) for a task on a vendor adapter. '
                       'Equivalent of `kp run <task> --vendor <v> --exec-mode <mode>`. '
                       'Returns run_id, status, and the artifacts directory; the '
                       'machine-readable audit trail is available via kp_audit_read.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'task': {'type': 'string',
                         'description': 'task name (e.g. add / matmul / gqa)'},
                'vendor': {'type': 'string',
                           'description': 'vendor adapter id (ascend / lingspeed / metax '
                                          'or any registered adapter)',
                           'default': 'ascend'},
                'exec_mode': {'type': 'string',
                              'enum': ['paper', 'live'],
                              'default': 'paper',
                              'description': 'paper = full 8 stages without hardware; '
                                             'live = real-device execution'},
                'model_goal': {'type': 'string',
                               'description': 'model-level goal (e.g. throughput)'},
                'budget_iters': {'type': 'integer', 'default': 3,
                                 'description': 'stage-7 evolution iteration budget'},
                'budget_minutes': {'type': 'number', 'default': 60.0,
                                   'description': 'global minute budget'},
            },
            'required': ['task'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'kp_audit_read',
        'description': 'Read a run\'s machine-readable audit trail (audit.jsonl) as JSON. '
                       'Pass run_id (returned by kp_run) or run_dir.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'run_id': {'type': 'string',
                           'description': 'run id from kp_run, e.g. kp-lingspeed-add-...'},
                'run_dir': {'type': 'string',
                            'description': 'absolute path of the run directory'},
            },
            'additionalProperties': False,
        },
    },
]


class _StderrProxy:
    """Drop-in sys.stdout replacement that routes print() calls to stderr.

    The pipeline prints progress to stdout; on the MCP channel stdout must stay
    reserved for protocol frames, so any tool execution prints are redirected here.
    """

    def write(self, s: str) -> int:
        sys.stderr.write(s)
        return len(s)

    def writelines(self, lines) -> None:
        sys.stderr.writelines(lines)

    def flush(self) -> None:
        sys.stderr.flush()


def _log(msg: str) -> None:
    sys.stderr.write(f'[kp-mcp] {msg}\n')
    sys.stderr.flush()


def _send(out, rid, result) -> None:
    frame = json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result},
                       ensure_ascii=False)
    out.write(frame + '\n')
    out.flush()


def _send_error(out, rid, code: int, message: str) -> None:
    frame = json.dumps({'jsonrpc': '2.0', 'id': rid,
                        'error': {'code': code, 'message': message}},
                       ensure_ascii=False)
    out.write(frame + '\n')
    out.flush()


def _text_result(data, is_error: bool) -> dict:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}


def _default_output_root() -> str:
    here = os.getcwd()
    candidate = os.path.join(here, 'anvil_output')
    for _ in range(6):
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            break
        candidate = os.path.join(parent, 'anvil_output')
        here = parent
    return os.path.join(os.getcwd(), 'anvil_output')


def _audit_path(arguments: dict) -> str:
    run_dir = arguments.get('run_dir', '')
    if not run_dir:
        run_id = arguments.get('run_id', '')
        if not run_id:
            raise ValueError('kp_audit_read requires run_id or run_dir')
        run_dir = os.path.join(_default_output_root(), 'kp-runs', run_id)
    audit = os.path.join(run_dir, 'audit.jsonl')
    if not os.path.exists(audit):
        raise FileNotFoundError(f'audit.jsonl not found: {audit}')
    return audit


def serve(anvil_root: str = '') -> int:
    """Blocking MCP stdio server loop. Returns process exit code."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from kp import execute_run  # noqa: PLC0415  (import here keeps serve() the single entry)
    from vendor import VendorRegistry  # noqa: PLC0415

    reg = VendorRegistry(anvil_root)
    real_out = sys.stdout
    proxy = _StderrProxy()

    def call_tool(name: str, arguments: dict) -> dict:
        if name == 'kp_vendor_list':
            rows = reg.list_all()
            slim = [
                {
                    'vendor_id': r.get('vendor_id'),
                    'version': r.get('version'),
                    'devices': r.get('devices', []),
                    'dsls': r.get('dsls', []),
                    'stages': r.get('stages_list', []),
                    'health': r.get('health', ''),
                }
                for r in rows
            ]
            return _text_result(
                {'count': len(slim), 'adapters': slim,
                 'hint': 'run a task with kp_run; audit trail via kp_audit_read'},
                False)

        if name == 'kp_run':
            task = arguments.get('task')
            if not task:
                raise ValueError('kp_run requires a "task" argument')
            # Redirect pipeline stdout to stderr so protocol frames stay pristine.
            old = sys.stdout
            sys.stdout = proxy
            try:
                r = execute_run(
                    vendor_id=arguments.get('vendor', 'ascend'),
                    task=task,
                    exec_mode=arguments.get('exec_mode', 'paper'),
                    model_goal=arguments.get('model_goal'),
                    budget_iters=int(arguments.get('budget_iters', 3)),
                    budget_minutes=float(arguments.get('budget_minutes', 60.0)),
                    anvil_root=anvil_root,
                )
            finally:
                sys.stdout = old
            status = 'OK' if r['ok'] else 'FAILED'
            payload = {
                'run_id': r['run_id'],
                'status': status,
                'artifacts_dir': r['run_dir'],
                'audit': 'kp_audit_read',
            }
            if not r['ok']:
                payload['note'] = 'pipeline did not complete; read audit.jsonl for stage errors'
            return _text_result(payload, is_error=not r['ok'])

        if name == 'kp_audit_read':
            audit = _audit_path(arguments)
            lines = []
            with open(audit, 'r', encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        lines.append(json.loads(ln))
            return _text_result(
                {'run_dir': os.path.dirname(audit), 'entries': lines,
                 'count': len(lines)}, False)

        raise ValueError(f'unknown tool: {name}')

    _log(f'serving KernelPrism MCP v{SERVER_VERSION} on stdio '
         f'(anvil_root={anvil_root or "auto-detect"})')

    for raw in sys.stdin:
        line = raw.rstrip('\n')
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f'ignoring malformed frame: {e}')
            continue
        if not isinstance(msg, dict) or 'id' not in msg:
            continue  # notification (e.g. initialized) — no reply
        rid = msg.get('id')
        method = msg.get('method')
        params = msg.get('params') or {}
        try:
            if method == 'initialize':
                result = {
                    'protocolVersion': params.get('protocolVersion', PROTOCOL_VERSION),
                    'capabilities': {'tools': {'listChanged': False}},
                    'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
                }
            elif method == 'notifications/initialized':
                result = {}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                result = call_tool(str(params.get('name', '')),
                                   params.get('arguments') or {})
            else:
                _send_error(real_out, rid, -32601, f'method not found: {method}')
                continue
            _send(real_out, rid, result)
        except Exception as e:  # noqa: BLE001  (report any handler failure to the peer)
            _log(f'handler error: {e!r}')
            _send_error(real_out, rid, -32603, str(e))
    return 0


if __name__ == '__main__':
    sys.exit(serve())
