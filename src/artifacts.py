#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism artifact model (landing of the unified artifact model, 02 §3).

Cross-vendor contract: every inter-stage product is an immutable artifact defined
by a JSON schema (with content-addressed references). The platform does not parse
vendor-private formats; vendor-private details are encapsulated inside adapters.

This module also provides:
  1) schema definitions (validator functions)
  2) transactional persistence (write-artifact: run_id prefix + content hash
     suffix; write a temp file first, then rename, to prevent half-written files)
  3) BottleneckReport (stage-2 model-level bottleneck location; user constraint
     2026-11-21: end-to-end time share x Amdahl upper bound on gain, quantified
     selection rationale; "feels slow, so optimize" is forbidden)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Basic utilities
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$')


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"artifact schema violation: {msg}")


def validate_id(value: Any, field: str) -> None:
    _check(isinstance(value, str), f"{field} must be a string")
    _check(_SAFE_ID_RE.match(value), f"{field} contains illegal characters: {value!r}")


def content_hash(obj: Any) -> str:
    """SHA-256 of the canonicalized JSON serialization (content addressing)."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


# ---------------------------------------------------------------------------
# Artifact schemas (02 §3.2 core artifact schemas; JSON Schema draft 2020-12 style -> validator functions)
# ---------------------------------------------------------------------------


def validate_ModelSpec(m: Dict[str, Any]) -> None:
    _check(isinstance(m, dict) and 'model_name' in m, 'ModelSpec missing model_name')
    validate_id(m['model_name'], 'model_name')
    for f in ('source', 'framework', 'target_vendor'):
        _check(isinstance(m.get(f), str) and m.get(f), f'ModelSpec.{f} must be a non-empty string')
    _check(isinstance(m.get('target_devices', []), list), 'target_devices must be a list')
    _check('precision_req' in m, 'ModelSpec missing precision_req')


def validate_UnderstandingDoc(d: Dict[str, Any]) -> None:
    _check(isinstance(d, dict) and 'run_id' in d, 'UnderstandingDoc missing run_id')
    validate_id(d['run_id'], 'run_id')
    _check('math_definition' in d, 'UnderstandingDoc missing math_definition')
    op_list = d.get('op_list')
    _check(isinstance(op_list, list) and op_list, 'op_list must be a non-empty list')
    for op in op_list:
        for f in ('op_name', 'semantics', 'dataflow'):
            _check(isinstance(op.get(f), str) and op.get(f), f'UnderstandingDoc.op.{f} must be non-empty')
    _check(isinstance(d.get('sources', []), list), 'sources must be a list')


def validate_OpSpec(spec: Dict[str, Any]) -> None:
    _check(isinstance(spec, dict) and 'op_name' in spec, 'OpSpec missing op_name')
    validate_id(spec['op_name'], 'op_name')
    for f in ('category',):
        _check(isinstance(spec.get(f), str), f'OpSpec.{f} must be a string')
    # I/O spec
    _check(isinstance(spec.get('io'), dict) and spec['io'], 'OpSpec.io must be a non-empty dict')
    _check('complexity_level' in spec, 'OpSpec missing complexity_level')
    _check(spec['complexity_level'] in ('L1', 'L2', 'L3', 'L4', 'L5'),
           f"invalid complexity_level: {spec.get('complexity_level')}")
    # Perf expectation (stage-2 gate: non-empty)
    _check('perf_target' in spec, 'OpSpec missing perf_target (stage-2 gate: target T/HAP or "TBD")')
    _check(spec['perf_target'] in ('TBD',) or isinstance(spec['perf_target'], dict),
           'perf_target must be a dict (target T/HAP spec) or "TBD"')
    # Bottleneck selection rationale (user constraint 2026-11-21: quantified rationale; "feels slow, so optimize" is forbidden)
    if spec.get('role') == 'bottleneck':
        reason = spec.get('bottleneck_reason')
        _check(isinstance(reason, dict) and
               {'time_share', 'amdahl_upper_bound'} <= set(reason),
               'bottleneck op must carry bottleneck_reason{time_share, amdahl_upper_bound}')


def validate_BottleneckReport(r: Dict[str, Any]) -> None:
    _check(isinstance(r, dict) and 'run_id' in r, 'BottleneckReport missing run_id')
    _check(isinstance(r.get('inputs'), dict), 'BottleneckReport.inputs must be a dict')
    ranked = r.get('ranked_ops')
    _check(isinstance(ranked, list) and ranked, 'ranked_ops must be non-empty (bottleneck-location non-empty gate)')
    for op in ranked:
        for f in ('op_name', 'time_share', 'amdahl_upper_bound', 'basis'):
            _check(f in op, f'ranked_ops[{op.get("op_name")}] missing {f}')
        _check(isinstance(op['amdahl_upper_bound'], (int, float)), 'amdahl_upper_bound must be numeric')
    _check(isinstance(r.get('data_basis'), str), 'data_basis must be "measured"|"estimated"')
    _check(r['data_basis'] in ('measured', 'estimated'), f"invalid data_basis: {r['data_basis']}")


def validate_KernelSource(k: Dict[str, Any]) -> None:
    _check(isinstance(k, dict) and 'op_id' in k, 'KernelSource missing op_id')
    _check('language' in k and 'source_tree' in k, 'KernelSource missing language/source_tree')
    _check('build_script' in k, 'KernelSource missing build_script')
    # User constraint 2026-11-21: candidate = design idea
    idea = k.get('design_idea')
    _check(isinstance(idea, dict) and {'bottleneck', 'strategy', 'expected_gain', 'risk'} <= set(idea),
           'KernelSource must carry design_idea{bottleneck,strategy,expected_gain,risk} (candidate=design-idea gate)')
    _check(isinstance(k.get('generation_strategy'), str), 'generation_strategy must be a string')


def validate_BuildArtifact(b: Dict[str, Any]) -> None:
    _check(isinstance(b, dict) and 'op_id' in b, 'BuildArtifact missing op_id')
    _check('build_ok' in b and isinstance(b['build_ok'], bool), 'build_ok must be a bool')
    _check('toolchain' in b, 'BuildArtifact missing toolchain')


def validate_PrecisionReport(p: Dict[str, Any]) -> None:
    _check(isinstance(p, dict) and 'op_id' in p, 'PrecisionReport missing op_id')
    cases = p.get('cases')
    _check(isinstance(cases, list) and cases, 'cases must be a non-empty list')
    for c in cases:
        for f in ('id', 'max_abs_err', 'max_rel_err', 'pass'):
            _check(f in c, f'case missing {f}')
    _check('pass_rate' in p and isinstance(p['pass_rate'], (int, float)), 'pass_rate must be numeric')
    _check('verdict' in p, 'PrecisionReport missing verdict')
    # CoVeR verification chain (07 A7)
    _check(isinstance(p.get('verify_chain', []), list), 'verify_chain must be a list')


def validate_PerfReport(p: Dict[str, Any]) -> None:
    _check(isinstance(p, dict) and 'op_id' in p, 'PerfReport missing op_id')
    cases = p.get('cases')
    _check(isinstance(cases, list) and cases, 'cases must be a non-empty list')
    for c in cases:
        for f in ('id', 't_cand_us', 't_baseline_us', 't_hw_us', 'HAP', 'speedup'):
            _check(f in c, f'case[{c.get("id")}] missing {f}')
        # Stability evaluation (2026-11-23): if present, validate the value (float from round())
        if 't_cand_std_us' in c:
            _check(isinstance(c['t_cand_std_us'], (int, float)),
                   f"case[{c.get('id')}] t_cand_std_us must be numeric")
        if 'n_samples' in c:
            _check(isinstance(c['n_samples'], int) and c['n_samples'] >= 1,
                   f"case[{c.get('id')}] n_samples must be a positive integer")
    _check('bottleneck_analysis' in p and p['bottleneck_analysis'],
           'PerfReport missing bottleneck_analysis (stage-6 gate: bottleneck analysis must be non-empty)')
    # Stage-6 gate: real shape source annotation (live device sampling = live_measure)
    for c in cases:
        _check(c.get('shape_source') in ('call_site', 'graph', 'manual', 'live_measure'),
               f"case[{c.get('id')}] missing shape_source(call_site|graph|manual|live_measure)")
    # Gain threshold gate (2026-11-23, inspired by Kernel-Smith): validate structure if present
    gg = p.get('gain_gate')
    if gg is not None:
        _check(isinstance(gg, dict) and {'threshold_x', 'max_speedup', 'pass'} <= set(gg),
               'PerfReport.gain_gate must contain threshold_x/max_speedup/pass')
        _check(isinstance(gg['pass'], bool), 'PerfReport.gain_gate.pass must be a bool')


def validate_OptimizedKernel(o: Dict[str, Any]) -> None:
    _check(isinstance(o, dict) and 'op_id' in o, 'OptimizedKernel missing op_id')
    versions = o.get('versions')
    _check(isinstance(versions, list) and versions, 'versions must be a non-empty list')
    for v in versions:
        for f in ('v', 'kernel', 'precision', 'perf'):
            _check(f in v, f'version missing {f}')
    _check('final_v' in o, 'OptimizedKernel missing final_v')
    _check(isinstance(o.get('decision_log', []), list), 'decision_log must be a list')
    # Structured feedback chain (2026-11-23, inspired by Kernel-Smith): validate structure if present
    fc = o.get('feedback_chain')
    if fc is not None:
        _check(isinstance(fc, list), 'feedback_chain must be a list')
        for item in fc:
            _check(isinstance(item, dict) and 'source' in item and 'type' in item,
                   'each feedback_chain item must contain source/type')


def validate_RegistryEntry(e: Dict[str, Any]) -> None:
    _check(isinstance(e, dict) and 'op_id' in e, 'RegistryEntry missing op_id')
    for f in ('vendor', 'device', 'final_kernel_ref', 'complexity_level',
              'license', 'audit_signature', 'entry_meta'):
        _check(f in e, f'RegistryEntry missing {f}')
    meta = e['entry_meta']
    for f in ('author', 'run_id', 'date', 'status'):
        _check(f in meta, f'entry_meta missing {f}')
    # Gain gate (2026-11-23, inspired by Kernel-Smith): status enum covers rejection states
    _check(meta.get('status') in ('verified', 'quarantined', 'rejected-trivial-gain',
                                  'needs-review'),
           f"invalid entry_meta.status: {meta.get('status')}")


VALIDATORS = {
    'ModelSpec': validate_ModelSpec,
    'UnderstandingDoc': validate_UnderstandingDoc,
    'OpSpec': validate_OpSpec,
    'BottleneckReport': validate_BottleneckReport,
    'KernelSource': validate_KernelSource,
    'BuildArtifact': validate_BuildArtifact,
    'PrecisionReport': validate_PrecisionReport,
    'PerfReport': validate_PerfReport,
    'OptimizedKernel': validate_OptimizedKernel,
    'RegistryEntry': validate_RegistryEntry,
}


def validate(kind: str, obj: Any) -> None:
    fn = VALIDATORS.get(kind)
    if fn is None:
        raise ValueError(f"unknown artifact kind: {kind}")
    fn(obj)


# ---------------------------------------------------------------------------
# Transactional persistence (run_id prefix + content hash suffix; temp file first, then rename)
# ---------------------------------------------------------------------------


def artifact_path(run_dir: str, kind: str, obj: Any, tag: str = '') -> str:
    """Compute the artifact on-disk path. run_dir = <run_id>/artifacts.

    Naming: <kind>__<tag>__<hash>.json, hash = first 16 hex chars of content sha256.
    tag distinguishes multiple instances of the same kind (e.g. multiple candidate
    KernelSource: k1, k2, ...).
    """
    validate(kind, obj)
    h = content_hash(obj)
    safe_tag = re.sub(r'[^A-Za-z0-9_.\-]', '_', str(tag))
    fname = f"{kind}{'__' + safe_tag if safe_tag else ''}__{h}.json"
    return os.path.join(run_dir, 'artifacts', fname)


def write_artifact(run_dir: str, kind: str, obj: Any, tag: str = '') -> str:
    """Transactional artifact write: first write <fname>.tmp, then os.replace, to avoid half-written files."""
    validate(kind, obj)
    path = artifact_path(run_dir, kind, obj, tag)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return path


def load_artifact(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Stage-2 BottleneckReport construction (user constraint 2026-11-21)
# ---------------------------------------------------------------------------


def build_bottleneck_report(run_id: str, op_costs: List[Dict[str, Any]],
                            data_basis: str = 'estimated') -> Dict[str, Any]:
    """Build a BottleneckReport from a model-level time breakdown.

    op_costs: [{op_name, time_share (0..1 fraction of end-to-end time), category}]
    The Amdahl gain upper bound is computed as (assuming the op could be optimized
    to zero, a theoretical limit):
      upper = time_share. Real modules prorate by the optimization ratio; this
      upper bound is used for ranking.
    Returns: ranked_ops sorted by time_share x upper descending.
    """
    _check(data_basis in ('measured', 'estimated'), 'data_basis must be "measured"|"estimated"')
    total_share = sum(float(c['time_share']) for c in op_costs)
    _check(total_share > 0, 'sum of time_share must be > 0 (malformed end-to-end breakdown input)')
    _check(total_share <= 1.0 + 1e-6,
           f'sum of time_share {total_share:.3f} > 1, end-to-end breakdown is unreasonable')

    ranked = []
    for c in op_costs:
        share = float(c['time_share'])
        # Amdahl gain upper bound: op time share p; if fully eliminated, end-to-end speedup <= 1/(1-p)
        amdahl = share / max(1e-9, 1.0 - share)
        ranked.append({
            'op_name': c['op_name'],
            'category': c.get('category', 'unknown'),
            'time_share': round(share, 4),
            'amdahl_upper_bound': round(amdahl, 4),
            'basis': c.get('basis', data_basis),
        })
    ranked.sort(key=lambda r: (-r['amdahl_upper_bound'], -r['time_share']))

    return {
        'run_id': run_id,
        'inputs': {'op_costs': op_costs},
        'ranked_ops': ranked,
        'data_basis': data_basis,
        'note': ('End-to-end time breakdown + Amdahl projection: every op entering the '
                 'generation stage must carry a quantified selection rationale; '
                 '"feels slow, so optimize" is forbidden (user constraint 2026-11-21)'),
    }
