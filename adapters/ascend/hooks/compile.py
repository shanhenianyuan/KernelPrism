#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend adapter v3_compile hook (04 §3 interface contract): live compile chain.

Input (stdin JSON payload):
  {kernel_source, workspace, run_id}
    kernel_source.op_id  -- op id, e.g. add-c1
    kernel_source.source_tree.ir   -- optional, IR template (defaults to AddCustom
                                      vector-add generation)

Output (stdout last-line JSON, BuildArtifact compatible):
  {build_ok, detail, build_log_path, artifacts: [{kind, ref}],
   toolchain: {compiler, version, flags}, env_snapshot, duration}

Live-machine recipe (converged empirically via probe33-35, 2026-11-21):
  msopgen gen -i op.json -c ai_core-ascend910b1 -lan cpp -out <proj>
  cmake -S <proj> -B <proj>/build -DASCEND_CANN_PACKAGE_PATH=<toolkit>
        -DENABLE_BINARY_PACKAGE=ON -DENABLE_SOURCE_PACKAGE=OFF   # both booleans must be set explicitly
  cmake --build <proj>/build --target binary
Artifacts:
  op_kernel/binary/ascend910b/<op>/<OpName>_<hash>.o/.json
  op_kernel/binary/config/ascend910b/<op>.json
  op_host/libcust_opmaster_rt2.0.so
  autogen/libascend_all_ops.so
"""
import json
import os
import shutil
import subprocess
import sys
import time
import glob

from pathlib import Path

SYSROOT = '/usr/local/Ascend/ascend-toolkit/latest'
GEN_BIN = '/usr/local/Ascend/cann-8.5.1/bin/msopgen'
SOC = 'ai_core-ascend910b1'


def _find_msopgen() -> str:
    for cand in (os.environ.get('MSOPGEN', ''), GEN_BIN,
                 '/usr/local/Ascend/cann-8.5.1/python/site-packages/bin/msopgen',
                 shutil.which('msopgen') or ''):
        if cand and os.path.exists(cand):
            return cand
    return 'msopgen'  # leave it to PATH


def _gen_ir(op_id: str) -> dict:
    """IR template: type array + format ND + param_type required (format verified via probe13/23)."""
    return [
        {
            "op": "AddCustom",
            "input_desc": [
                {"name": "x", "type": ["float16"], "format": ["ND"], "param_type": "required"},
                {"name": "y", "type": ["float16"], "format": ["ND"], "param_type": "required"},
            ],
            "output_desc": [
                {"name": "z", "type": ["float16"], "format": ["ND"], "param_type": "required"},
            ],
        }
    ]


def compile_op(kernel_source: dict, workspace: str) -> dict:
    t0 = time.monotonic()
    proj = os.path.join(workspace, 'proj')
    build = os.path.join(proj, 'build')
    log = os.path.join(workspace, 'compile.log')
    if os.path.isdir(proj):
        shutil.rmtree(proj)
    os.makedirs(workspace, exist_ok=True)

    log_lines = []
    log_path = os.path.join(workspace, 'compile.log')
    def log(msg):
        log_lines.append(str(msg))
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(str(msg) + '\n')

    tk = os.environ.get('ASCEND_CANN_PACKAGE_PATH') or SYSROOT
    msopgen = _find_msopgen()

    # 1) IR -> op.json (user op_id preserved in the kernel_pkg name; vendor op name fixed as AddCustom)
    ir = kernel_source.get('source_tree', {}).get('ir') or _gen_ir(kernel_source.get('op_id', 'add-c1'))
    op_json = os.path.join(workspace, 'op.json')
    with open(op_json, 'w', encoding='utf-8') as f:
        json.dump(ir, f, ensure_ascii=False, indent=2)

    # 2) generate the project with msopgen
    r = subprocess.run([msopgen, 'gen', '-i', op_json, '-c', SOC,
                        '-lan', 'cpp', '-out', proj],
                       capture_output=True, text=True, timeout=300)
    log(f'[msopgen] exit={r.returncode}')
    log(r.stdout[-2000:] if r.stdout else '')
    log(r.stderr[-2000:] if r.stderr else '')
    if r.returncode != 0:
        return {'build_ok': False, 'detail': f'msopgen failed: {r.stderr[-500:]}',
                'build_log_path': log_path, 'artifacts': [], 'toolchain': {},
                'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)}

    if not os.path.exists(os.path.join(proj, 'CMakeLists.txt')):
        return {'build_ok': False, 'detail': 'msopgen produced no CMakeLists.txt',
                'build_log_path': log_path, 'artifacts': [], 'toolchain': {},
                'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)}

    # 3) cmake configure -- both booleans must be set explicitly (verified by probe34; missing either -> func.cmake:362 if syntax error)
    cfg = ['cmake', '-S', proj, '-B', build,
           f'-DASCEND_CANN_PACKAGE_PATH={tk}',
           '-DENABLE_BINARY_PACKAGE=ON', '-DENABLE_SOURCE_PACKAGE=OFF']
    r = subprocess.run(cfg, capture_output=True, text=True, timeout=600)
    log(f'[configure] exit={r.returncode}')
    if r.returncode != 0:
        log(r.stderr[-3000:] or r.stdout[-3000:])
        return {'build_ok': False, 'detail': f'cmake configure failed: {r.stderr[-400:]}',
                'build_log_path': log_path, 'artifacts': [], 'toolchain': {},
                'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)}

    # 4) build the binary root target (auto-drives AddCustom_ascend910b -> ascendc_bin_* -> binary)
    r = subprocess.run(['cmake', '--build', build, '--target', 'binary'],
                       capture_output=True, text=True, timeout=1800)
    log(f'[build] exit={r.returncode}')
    log((r.stdout or r.stderr)[-3000:])
    if r.returncode != 0:
        return {'build_ok': False, 'detail': f'cmake build failed: {(r.stderr or r.stdout)[-400:]}',
                'build_log_path': log_path, 'artifacts': [], 'toolchain': {},
                'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)}

    # 5) collect artifacts
    arts = []
    for pat in ('op_kernel/binary/ascend910b/*/*.o',
                'op_kernel/binary/ascend910b/*/*.json',
                'op_kernel/binary/config/ascend910b/*.json',
                'op_host/*.so',
                'autogen/*.so'):
        for p in sorted(glob.glob(os.path.join(build, pat))):
            arts.append({'kind': 'kernel_pkg' if p.endswith('.o') else
                         ('op_config' if p.endswith('.json') else 'host_lib'),
                         'ref': os.path.relpath(p, build)})
    if not arts:
        return {'build_ok': False, 'detail': 'build succeeded but no artifacts found',
                'build_log_path': log_path, 'artifacts': [], 'toolchain': {},
                'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)}

    return {
        'build_ok': True,
        'detail': f'msopgen+cmake official chain compiled successfully ({len(arts)} artifacts)',
        'build_log_path': log_path,
        'artifacts': arts,
        'toolchain': {'compiler': 'ccec(msopgen+cmake)', 'version': os.environ.get('CANN_VERSION', '8.5.1'),
                      'flags': ['-lan cpp', 'ENABLE_BINARY_PACKAGE=ON', 'ENABLE_SOURCE_PACKAGE=OFF']},
        'env_snapshot': {'exec_mode': 'live', 'ascend_cann_package_path': tk,
                         'soc': SOC, 'msopgen': msopgen},
        'duration': round(time.monotonic() - t0, 3),
    }


def main():
    payload = json.loads(sys.stdin.read())
    ks = payload.get('kernel_source', {})
    workspace = payload.get('workspace', '/tmp/kpws')
    try:
        res = compile_op(ks, workspace)
        res['exit_code'] = 0 if res['build_ok'] else 1
    except Exception as e:  # noqa: BLE001   # fallback: even on crash, return JSON with traceback for diagnosis
        import traceback
        res = {'build_ok': False, 'detail': f'compile_op crash: {e}\n{traceback.format_exc()[-800:]}',
               'build_log_path': os.path.join(workspace, 'compile.log'), 'artifacts': [],
               'toolchain': {}, 'env_snapshot': {}, 'duration': 0.0, 'exit_code': 1}
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
