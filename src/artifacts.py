#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism 工件模型（02 §3 统一工件 AIrtifact Model 落地）。

跨厂商契约：所有段间产物 = JSON schema 定义的不可变工件（含 content-addressed
引用）。平台不解析厂商私有格式，厂商私有细节封装在 adapter 内。

本模块同时承担：
  1) schema 定义（校验函数）
  2) 事务性落盘（write-artifact: run_id 前缀 + content hash 后缀，先写临时
     文件再 rename，杜绝半成品）
  3) BottleneckReport（段2 模型级瓶颈定位，用户约束 2026-11-21：端到端耗时
     占比 × Amdahl 收益上界，量化选取理由，禁「觉得慢就优化」）
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
# 基础工具
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$')


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"artifact schema violation: {msg}")


def validate_id(value: Any, field: str) -> None:
    _check(isinstance(value, str), f"{field} 必须是字符串")
    _check(_SAFE_ID_RE.match(value), f"{field} 含非法字符: {value!r}")


def content_hash(obj: Any) -> str:
    """JSON 规范化序列化后的 sha256（content addressing）。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


# ---------------------------------------------------------------------------
# 工件 schema（02 §3.2 核心工件 schema，JSON Schema 草案 2020-12 风格 → 校验函数）
# ---------------------------------------------------------------------------


def validate_ModelSpec(m: Dict[str, Any]) -> None:
    _check(isinstance(m, dict) and 'model_name' in m, 'ModelSpec 缺少 model_name')
    validate_id(m['model_name'], 'model_name')
    for f in ('source', 'framework', 'target_vendor'):
        _check(isinstance(m.get(f), str) and m.get(f), f'ModelSpec.{f} 必须为非空字符串')
    _check(isinstance(m.get('target_devices', []), list), 'target_devices 必须为列表')
    _check('precision_req' in m, 'ModelSpec 缺少 precision_req')


def validate_UnderstandingDoc(d: Dict[str, Any]) -> None:
    _check(isinstance(d, dict) and 'run_id' in d, 'UnderstandingDoc 缺少 run_id')
    validate_id(d['run_id'], 'run_id')
    _check('math_definition' in d, 'UnderstandingDoc 缺少 math_definition')
    op_list = d.get('op_list')
    _check(isinstance(op_list, list) and op_list, 'op_list 必须为非空列表')
    for op in op_list:
        for f in ('op_name', 'semantics', 'dataflow'):
            _check(isinstance(op.get(f), str) and op.get(f), f'UnderstandingDoc.op.{f} 必须非空')
    _check(isinstance(d.get('sources', []), list), 'sources 必须为列表')


def validate_OpSpec(spec: Dict[str, Any]) -> None:
    _check(isinstance(spec, dict) and 'op_name' in spec, 'OpSpec 缺少 op_name')
    validate_id(spec['op_name'], 'op_name')
    for f in ('category',):
        _check(isinstance(spec.get(f), str), f'OpSpec.{f} 必须为字符串')
    # I/O 规格
    _check(isinstance(spec.get('io'), dict) and spec['io'], 'OpSpec.io 必须为非空 dict')
    _check('complexity_level' in spec, 'OpSpec 缺少 complexity_level')
    _check(spec['complexity_level'] in ('L1', 'L2', 'L3', 'L4', 'L5'),
           f"complexity_level 非法: {spec.get('complexity_level')}")
    # 性能预期（段2 门禁：非空）
    _check('perf_target' in spec, 'OpSpec 缺少 perf_target（段2 门禁：目标 T/HAP 或"待测"）')
    _check(spec['perf_target'] in ('待测', 'TBD') or isinstance(spec['perf_target'], dict),
           'perf_target 必须为 dict（目标 T/HAP 段）或"待测"')
    # 瓶颈选取理由（用户约束 2026-11-21：量化理由，禁「觉得慢就优化」）
    if spec.get('role') == 'bottleneck':
        reason = spec.get('bottleneck_reason')
        _check(isinstance(reason, dict) and
               {'time_share', 'amdahl_upper_bound'} <= set(reason),
               '瓶颈算子必须带 bottleneck_reason{time_share, amdahl_upper_bound}')


def validate_BottleneckReport(r: Dict[str, Any]) -> None:
    _check(isinstance(r, dict) and 'run_id' in r, 'BottleneckReport 缺少 run_id')
    _check(isinstance(r.get('inputs'), dict), 'BottleneckReport.inputs 必须为 dict')
    ranked = r.get('ranked_ops')
    _check(isinstance(ranked, list) and ranked, 'ranked_ops 必须非空（瓶颈定位非空门禁）')
    for op in ranked:
        for f in ('op_name', 'time_share', 'amdahl_upper_bound', 'basis'):
            _check(f in op, f'ranked_ops[{op.get("op_name")}] 缺少 {f}')
        _check(isinstance(op['amdahl_upper_bound'], (int, float)), 'amdahl_upper_bound 必须为数值')
    _check(isinstance(r.get('data_basis'), str), 'data_basis 必须为 "实测"|"估算"')
    _check(r['data_basis'] in ('实测', '估算'), f"data_basis 非法: {r['data_basis']}")


def validate_KernelSource(k: Dict[str, Any]) -> None:
    _check(isinstance(k, dict) and 'op_id' in k, 'KernelSource 缺少 op_id')
    _check('language' in k and 'source_tree' in k, 'KernelSource 缺少 language/source_tree')
    _check('build_script' in k, 'KernelSource 缺少 build_script')
    # 用户约束 2026-11-21：候选 = 设计思路
    idea = k.get('design_idea')
    _check(isinstance(idea, dict) and {'bottleneck', 'strategy', 'expected_gain', 'risk'} <= set(idea),
           'KernelSource 必须带 design_idea{bottleneck,strategy,expected_gain,risk}（候选=设计思路门禁）')
    _check(isinstance(k.get('generation_strategy'), str), 'generation_strategy 必须为字符串')


def validate_BuildArtifact(b: Dict[str, Any]) -> None:
    _check(isinstance(b, dict) and 'op_id' in b, 'BuildArtifact 缺少 op_id')
    _check('build_ok' in b and isinstance(b['build_ok'], bool), 'build_ok 必须为 bool')
    _check('toolchain' in b, 'BuildArtifact 缺少 toolchain')


def validate_PrecisionReport(p: Dict[str, Any]) -> None:
    _check(isinstance(p, dict) and 'op_id' in p, 'PrecisionReport 缺少 op_id')
    cases = p.get('cases')
    _check(isinstance(cases, list) and cases, 'cases 必须非空')
    for c in cases:
        for f in ('id', 'max_abs_err', 'max_rel_err', 'pass'):
            _check(f in c, f'case 缺少 {f}')
    _check('pass_rate' in p and isinstance(p['pass_rate'], (int, float)), 'pass_rate 必须为数值')
    _check('verdict' in p, 'PrecisionReport 缺少 verdict')
    # CoVeR 验证链（07 A7）
    _check(isinstance(p.get('verify_chain', []), list), 'verify_chain 必须为列表')


def validate_PerfReport(p: Dict[str, Any]) -> None:
    _check(isinstance(p, dict) and 'op_id' in p, 'PerfReport 缺少 op_id')
    cases = p.get('cases')
    _check(isinstance(cases, list) and cases, 'cases 必须非空')
    for c in cases:
        for f in ('id', 't_cand_us', 't_baseline_us', 't_hw_us', 'HAP', 'speedup'):
            _check(f in c, f'case[{c.get("id")}] 缺少 {f}')
        # 稳定评估（2026-11-23）：存在则校验数值（数值用 round() 产生的 float）
        if 't_cand_std_us' in c:
            _check(isinstance(c['t_cand_std_us'], (int, float)),
                   f"case[{c.get('id')}] t_cand_std_us 必须为数值")
        if 'n_samples' in c:
            _check(isinstance(c['n_samples'], int) and c['n_samples'] >= 1,
                   f"case[{c.get('id')}] n_samples 必须为正整数")
    _check('bottleneck_analysis' in p and p['bottleneck_analysis'],
           'PerfReport 缺少 bottleneck_analysis（段6 门禁：瓶颈分析非空）')
    # 段6 门禁：真实 shape 来源标注（live 真机采样 = live_measure）
    for c in cases:
        _check(c.get('shape_source') in ('call_site', 'graph', 'manual', 'live_measure'),
               f"case[{c.get('id')}] 缺少 shape_source(call_site|graph|manual|live_measure)")
    # 收益阈值门禁（2026-11-23 借鉴 Kernel-Smith）：存在则校验结构
    gg = p.get('gain_gate')
    if gg is not None:
        _check(isinstance(gg, dict) and {'threshold_x', 'max_speedup', 'pass'} <= set(gg),
               'PerfReport.gain_gate 必须含 threshold_x/max_speedup/pass')
        _check(isinstance(gg['pass'], bool), 'PerfReport.gain_gate.pass 必须为 bool')


def validate_OptimizedKernel(o: Dict[str, Any]) -> None:
    _check(isinstance(o, dict) and 'op_id' in o, 'OptimizedKernel 缺少 op_id')
    versions = o.get('versions')
    _check(isinstance(versions, list) and versions, 'versions 必须非空')
    for v in versions:
        for f in ('v', 'kernel', 'precision', 'perf'):
            _check(f in v, f'version 缺少 {f}')
    _check('final_v' in o, 'OptimizedKernel 缺少 final_v')
    _check(isinstance(o.get('decision_log', []), list), 'decision_log 必须为列表')
    # 结构化反馈链（2026-11-23 借鉴 Kernel-Smith）：存在则校验结构
    fc = o.get('feedback_chain')
    if fc is not None:
        _check(isinstance(fc, list), 'feedback_chain 必须为列表')
        for item in fc:
            _check(isinstance(item, dict) and 'source' in item and 'type' in item,
                   'feedback_chain 每项必须含 source/type')


def validate_RegistryEntry(e: Dict[str, Any]) -> None:
    _check(isinstance(e, dict) and 'op_id' in e, 'RegistryEntry 缺少 op_id')
    for f in ('vendor', 'device', 'final_kernel_ref', 'complexity_level',
              'license', 'audit_signature', 'entry_meta'):
        _check(f in e, f'RegistryEntry 缺少 {f}')
    meta = e['entry_meta']
    for f in ('author', 'run_id', 'date', 'status'):
        _check(f in meta, f'entry_meta 缺少 {f}')
    # 收益门禁（2026-11-23 借鉴 Kernel-Smith）：status 枚举覆盖拒绝态
    _check(meta.get('status') in ('verified', 'quarantined', 'rejected-trivial-gain',
                                  'needs-review'),
           f"entry_meta.status 非法: {meta.get('status')}")


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
        raise ValueError(f"未知工件类型: {kind}")
    fn(obj)


# ---------------------------------------------------------------------------
# 事务性落盘（run_id 前缀 + content hash 后缀；先临时文件后 rename）
# ---------------------------------------------------------------------------


def artifact_path(run_dir: str, kind: str, obj: Any, tag: str = '') -> str:
    """计算工件落盘路径。run_dir = <run_id>/artifacts。

    命名：<kind>__<tag>__<hash>.json，hash = 内容 sha256 前 16 位。
    tag 用于区分同 kind 多实例（如多候选 KernelSource: k1, k2, ...）。
    """
    validate(kind, obj)
    h = content_hash(obj)
    safe_tag = re.sub(r'[^A-Za-z0-9_.\-]', '_', str(tag))
    fname = f"{kind}{'__' + safe_tag if safe_tag else ''}__{h}.json"
    return os.path.join(run_dir, 'artifacts', fname)


def write_artifact(run_dir: str, kind: str, obj: Any, tag: str = '') -> str:
    """事务性写工件：先写 <fname>.tmp 再 os.replace，避免半成品。"""
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
# 段2 BottleneckReport 构造（用户约束 2026-11-21）
# ---------------------------------------------------------------------------


def build_bottleneck_report(run_id: str, op_costs: List[Dict[str, Any]],
                            data_basis: str = '估算') -> Dict[str, Any]:
    """从模型级耗时拆解构造 BottleneckReport。

    op_costs: [{op_name, time_share (0..1 端到端耗时占比), category}]
    折算出 Amdahl 收益上界（假设该算子优化到 0 理论上限）：
      upper = time_share。实际模块会按优化比例折算，这里的上界供排序。
    返回：ranked_ops 按 time_share × upper 降序。
    """
    _check(data_basis in ('实测', '估算'), 'data_basis 必须为 实测|估算')
    total_share = sum(float(c['time_share']) for c in op_costs)
    _check(total_share > 0, '耗时占比合计必须 > 0（端到端拆解输入异常）')
    _check(total_share <= 1.0 + 1e-6,
           f'耗时占比合计 {total_share:.3f} > 1，端到端拆解不合理')

    ranked = []
    for c in op_costs:
        share = float(c['time_share'])
        # Amdahl 收益上界：该算子耗时占比 p，假设完全消除，端到端加速 ≤ 1/(1-p)
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
        'note': ('端到端耗时拆解 + Amdahl 折算：进入生成段的算子必须有量化选取理由，'
                 '禁止「觉得慢就优化」（用户约束 2026-11-21）'),
    }
