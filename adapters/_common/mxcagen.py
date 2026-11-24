#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mxcagen.py — shared tooling module for the Metax/Lingspeed dual-card same-source adapters
====================================================
Gives the compile / verify / perf core hooks of the metax (Metax MXMACA) and
lingspeed (Lingspeed LUCA) adapters one shared set of low-level capabilities:
  1. locate_toolchain() —— locate the compiler (mxcc / lscc) + runtime (/opt/maca / /opt/luca-3.8.0)
  2. gen_add_cpp()       —— generate a single-file C++ source from kernel_source (template/mechanism)
                             (kernel + host driver, branched by mode compile/verify/perf)
  3. compile_cpp()       —— compile the single-file C++ with mxcc -x maca / lscc -x luca
  4. run_binary()        —— run the compiled artifact, parse via @DONE@ protocol + RESULT line
  5. emit()              —— hook output contract: trailing-JSON line on stdout

Design rationale (all verified empirically, 2026-11-23):
  - Dual-card same source: Lingspeed = Metax under a renamed prefix. lscc<->mxcc, lc_*<->mc_*,
    lc_runtime.h<->mc_runtime.h, `-x luca`<->`-x maca`, /opt/luca<->/opt/maca (verified table).
  - Lingspeed compile chain = single-file LUCA C++ -> `lscc -x luca src.cpp -o bin` -> run ./bin
  - LUCA runtime API (verified in gqa_bench.cpp): lc_runtime.h / lc_common.h,
    lcMalloc / lcFree / lcMemcpy(HostToDevice|DeviceToHost) / lcGetErrorString,
    lcSuccess; kernel launched with __global__ + <<<grid, blk, 0, 0>>> triple-angle brackets.
  - Metax isomorphic API (verified against official docs): mc_runtime.h, mcMalloc / mcFree / mcMemcpy,
    mcGetErrorString, mcSuccess; the `<<<>>>` triple-angle syntax is equivalent (compiled with mxcc -x maca).
  - d2h sync timing (perf): after warmup, a DeviceToHost memcpy acts as the sync point;
    clock_gettime(CLOCK_MONOTONIC) delta measured before/after (verified in gqa_bench bench_seq).
  - @DONE@ protocol: program-completion marker; hooks use it to confirm the run finished fully.
  - No torch: the verify golden side is a host float64 reference (refcheck.cpp style),
    compiled directly into the same single-file program for element-wise comparison.

Hooks are invoked by the pipeline as "subprocess + stdin JSON payload + trailing JSON line on stdout"
(src/pipeline.py _call_hook: cwd = hooks dir, timeout 600s).
This module is not meant to run directly (no main); but `python3 mxcagen.py -t <platform>` can self-check the toolchain.

stdlib-only, no third-party dependencies.
"""

import json
import os
import re
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Platform parameter table (dual-card same source: only the toolchain path /
# compiler name / -x language / device nodes / API prefix differ)
# ---------------------------------------------------------------------------
PLATFORMS = {
    'lingspeed': {
        'label': 'Lingspeed LUCA',
        'env_path': 'LUCA_PATH',
        'home_candidates': ['/opt/luca-3.8.0', '/opt/luca'],  # real link first (/opt/luca is dangling)
        'compiler_rel': os.path.join('lsgpu_llvm', 'bin', 'lscc'),
        'x_lang': 'luca',
        'dev_nodes': ['/dev/lscd'],          # crw------- 235,0 (no mknod needed)
        'version': '3.8.0',
        'runtime_lib': 'LUCA',
        'compiler_label': 'lscc',
        'api_map': {},                       # same-source original, nothing to remap
        'perf_baseline_url': 'https://www.lingspeed.com/benchmarks/',
    },
    'metax': {
        'label': 'Metax MXMACA',
        'env_path': 'MACA_PATH',
        'home_candidates': ['/opt/maca'],    # the real Metax software stack (verified by comparison)
        'compiler_rel': os.path.join('bin', 'mxcc'),  # pending real-hardware verification; same slot as lscc
        'x_lang': 'maca',
        'dev_nodes': ['/dev/mxc0', '/dev/metax_gpu', '/dev/mxc'],  # pending real-hardware verification
        'version': 'unknown',
        'runtime_lib': 'MXMACA',
        'compiler_label': 'mxcc',
        'api_map': {                         # Lingspeed prefix -> Metax prefix (same-source rename)
            'lc_runtime.h': 'mc_runtime.h',
            'lc_common.h': 'mc_common.h',
            'lcError_t': 'mcError_t',
            'lcSuccess': 'mcSuccess',
            'lcGetErrorString': 'mcGetErrorString',
            'lcMalloc': 'mcMalloc',
            'lcFree': 'mcFree',
            'lcMemcpyHostToDevice': 'mcMemcpyHostToDevice',
            'lcMemcpyDeviceToHost': 'mcMemcpyDeviceToHost',
            'lcMemcpy': 'mcMemcpy',
            'LCK(': 'MCK(',
        },
    },
}

# Template placeholders (avoid f-string brace-escaping hell; replaced one by one at generation time)
_T = {
    'N_ELEMS': '@@N_ELEMS@@',
    'BLK': '@@BLK@@',
    'GRID': '@@GRID@@',
    'WARM': '@@WARM@@',
    'SEGS': '@@SEGS@@',
    'SEG_ITERS': '@@SEG_ITERS@@',
    'MODE': '@@MODE@@',
    'KERNEL': '@@KERNEL_BODY@@',
    'VERIFY_BLOCK': '@@VERIFY_BLOCK@@',
    'PERF_BLOCK': '@@PERF_BLOCK@@',
    'OP_ID': '@@OP_ID@@',
}

# Single-file C++ master template: kernel definition + host driver (trimmed by mode).
# Baseline is the Lingspeed LUCA syntax (verified original); the metax platform gets the
# mc_* isomorphic version via api_map prefix replacement.
_ADD_TEMPLATE = r"""// Lingspeed LUCA / Metax MXMACA kernel — generated by kernel-prism adapters/_common/mxcagen.py
// platform=@@PLATFORM@@  op_id=@@OP_ID@@  mode=@@MODE@@  n=@@N_ELEMS@@  blk=@@BLK@@  grid=@@GRID@@
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <lc_runtime.h>
#include <lc_common.h>

#define LCK(expr, msg) do { \
    lcError_t _e_ = (expr); \
    if (_e_ != lcSuccess) { \
        fprintf(stderr, "LCK FAIL [%s]: %s\n", (msg), lcGetErrorString(_e_)); \
        return 1; \
    } \
} while (0)

__global__ void add_kernel(const float* a, const float* b, float* o, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) o[i] = a[i] + b[i];
}
@@KERNEL_BODY@@

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec / 1e6;
}

static int verify_add(const float* h_a, const float* h_b, const float* h_o,
                      int n, double* max_abs, double* max_rel) {
    // host float64 golden (refcheck.cpp style): element-wise a+b, outputs only the comparison metrics
    double ma = 0.0, mr = 0.0;
    int bad = 0;
    for (int i = 0; i < n; i++) {
        double ref = (double)h_a[i] + (double)h_b[i];
        double got = (double)h_o[i];
        double ae = fabs(got - ref);
        double rr = (fabs(ref) > 1e-30) ? (ae / fabs(ref)) : ae;
        if (ae > ma) ma = ae;
        if (rr > mr) mr = rr;
        /* only flag bad on a bounded number of sample points (avoid slowness at large n) */
        if (ae > 1e-4f && bad < 8) bad++;
    }
    *max_abs = ma;
    *max_rel = mr;
    return bad;
}

int main(void) {
    const int n = @@N_ELEMS@@;
    const int blk = @@BLK@@;
    const int grid = @@GRID@@;
    const int warm = @@WARM@@;
    const int segs = @@SEGS@@;
    const int seg_iters = @@SEG_ITERS@@;

    /* ---- host inputs (deterministic pseudo-random, avoid relying on rand variance) ---- */
    float *h_a = (float*)malloc((size_t)n * sizeof(float));
    float *h_b = (float*)malloc((size_t)n * sizeof(float));
    float *h_o = (float*)malloc((size_t)n * sizeof(float));
    float *sink = (float*)malloc(sizeof(float));
    if (!h_a || !h_b || !h_o || !sink) { fprintf(stderr, "host alloc failed\n"); return 1; }
    unsigned int seed = 20261123u;
    for (int i = 0; i < n; i++) {
        h_a[i] = ((float)(seed % 1000) - 500.0f) / 100.0f;
        seed = seed * 1103515245u + 12345u;
        h_b[i] = ((float)(seed % 1000) - 500.0f) / 100.0f;
        seed = seed * 1103515245u + 12345u;
    }

    /* ---- device side ---- */
    float *d_a = NULL, *d_b = NULL, *d_o = NULL;
    LCK(lcMalloc((void**)&d_a, (size_t)n * sizeof(float)), "malloc a");
    LCK(lcMalloc((void**)&d_b, (size_t)n * sizeof(float)), "malloc b");
    LCK(lcMalloc((void**)&d_o, (size_t)n * sizeof(float)), "malloc o");
    LCK(lcMemcpy(d_a, h_a, (size_t)n * sizeof(float), lcMemcpyHostToDevice), "cp a");
    LCK(lcMemcpy(d_b, h_b, (size_t)n * sizeof(float), lcMemcpyHostToDevice), "cp b");

    /* ---- warmup + sync point (verified d2h sync timing) ---- */
    for (int w = 0; w < warm; w++) add_kernel<<<grid, blk, 0, 0>>>(d_a, d_b, d_o, n);
    LCK(lcMemcpy(sink, d_o, sizeof(float), lcMemcpyDeviceToHost), "sync warm");

    /* ---- segmented timing: seg_iters launches per segment, d2h sync between segments ---- */
    double per_seg[@@SEGS@@];
    for (int s = 0; s < segs; s++) {
        double t0 = now_ms();
        for (int r = 0; r < seg_iters; r++)
            add_kernel<<<grid, blk, 0, 0>>>(d_a, d_b, d_o, n);
        LCK(lcMemcpy(sink, d_o, sizeof(float), lcMemcpyDeviceToHost), "sync");
        double t1 = now_ms();
        per_seg[s] = (t1 - t0) / (double)seg_iters;
    }

    /* ---- copy back + verify ---- */
    LCK(lcMemcpy(h_o, d_o, (size_t)n * sizeof(float), lcMemcpyDeviceToHost), "cp o");
    double max_abs = 0.0, max_rel = 0.0;
    int bad = verify_add(h_a, h_b, h_o, n, &max_abs, &max_rel);

    /* ---- summary (RESULT line for hook parsing; @DONE@ is the completion protocol) ---- */
    double avg_ms_all = 0.0;
    for (int s = 0; s < segs; s++) avg_ms_all += per_seg[s];
    avg_ms_all /= (double)segs;
    double std_ms = 0.0;
    if (segs > 1) {
        for (int s = 0; s < segs; s++) {
            double d = per_seg[s] - avg_ms_all;
            std_ms += d * d;
        }
        std_ms = sqrt(std_ms / (double)(segs - 1));
    }
    printf("RESULT per_iter_ms=%.6f std_ms=%.6f rounds=%d "
           "max_abs_err=%.6e max_rel_err=%.6e bad=%d\n",
           avg_ms_all, std_ms, segs * seg_iters, max_abs, max_rel, bad);
    printf("@DONE@ %s\n", "@@OP_ID@@");
    fflush(stdout);

    lcFree(d_a); lcFree(d_b); lcFree(d_o);
    free(h_a); free(h_b); free(h_o); free(sink);
    return 0;
}
"""


def platform(name: str) -> dict:
    """Get the platform parameter table; unknown platforms fall back to lingspeed with a warning."""
    if name not in PLATFORMS:
        sys.stderr.write('mxcagen: unknown platform %r, fallback lingspeed\n' % name)
        name = 'lingspeed'
    return PLATFORMS[name]


def _map_src(src: str, pf: dict) -> str:
    """Apply prefix replacement per the platform api_map (metax: lc_* -> mc_*; lingspeed untouched)."""
    for k, v in pf['api_map'].items():
        src = src.replace(k, v)
    return src


# ---------------------------------------------------------------------------
# Toolchain location
# ---------------------------------------------------------------------------
def locate_toolchain(name='lingspeed'):
    """Return dict {compiler, home, device_ok, found}; found=False when no compiler exists."""
    pf = platform(name)
    compiler = None
    home = None
    for cand in ([os.environ.get(pf['env_path']) or ''] + pf['home_candidates']):
        if not cand:
            continue
        c = os.path.join(cand, pf['compiler_rel'])
        if os.path.isfile(c) and os.access(c, os.X_OK):
            compiler = c
            home = cand
            break
    device_ok = any(os.path.exists(d) for d in pf['dev_nodes'])
    return {
        'compiler': compiler,
        'home': home,
        'device_ok': device_ok,
        'found': compiler is not None,
        'platform': name,
    }


def toolchain_summary(tc):
    pf = platform(tc.get('platform', 'lingspeed'))
    return {
        'compiler': (pf['x_lang'] + '-cc')[:10],
        'version': pf['version'],
        'path': tc.get('compiler') or 'not-found',
        'device': ','.join(pf['dev_nodes']) + (' (present)' if tc.get('device_ok') else ' (missing)'),
        'home': tc.get('home'),
    }


# ---------------------------------------------------------------------------
# C++ source generation
# ---------------------------------------------------------------------------
def _resolve_block(kernel_source):
    """Extract BLK from kernel_source[source_tree][mechanism]. Defaults to 256 when absent."""
    blk = 256
    try:
        st = kernel_source.get('source_tree') or {}
        mech = st.get('mechanism') or {}
        if isinstance(mech, dict):
            for k in ('blk', 'block', 'block_size', 'threads'):
                v = mech.get(k)
                if isinstance(v, int) and 32 <= v <= 1024:
                    blk = v
                    break
        elif isinstance(mech, str):
            m = re.search(r'blk[=:](\d+)', mech)
            if m:
                blk = int(m.group(1))
    except Exception:
        pass
    return max(32, min(1024, blk))


def _resolve_n(case_or_shape, default_n):
    """Resolve the total element count from a case dict ({'shape': [...]}) or a bare shape list."""
    if isinstance(case_or_shape, dict):
        shape = case_or_shape.get('shape') or []
    elif isinstance(case_or_shape, (list, tuple)):
        shape = list(case_or_shape)
    else:
        shape = []
    n = 1
    for s in shape:
        n *= int(s)
    return max(1, n) if shape else default_n


def gen_add_cpp(kernel_source, case, mode='compile', n=None, name='lingspeed'):
    """Generate the single-file C++ source text (parameterized by platform).

    kernel_source : _s2 artifact (source_tree={kind:'template', mechanism:...}, language reference)
    case          : {'id','shape','dtype'} or bare shape
    mode          : compile|verify|perf (same template skeleton; all emit RESULT output)
    n             : total element count; defaults to the product of case shape
    name          : platform — lingspeed (lc_* / -x luca) or metax (mc_* / -x maca)
    """
    pf = platform(name)
    n = n or _resolve_n(case, default_n=8192)
    blk = _resolve_block(kernel_source)
    grid = (n + blk - 1) // blk
    # Timing-scale adaptivity: small n gets more iterations, large n fewer (keep runtime within timeout)
    if n <= 1024:
        warm, segs, seg_iters = 200, 6, 200
    elif n <= 65536:
        warm, segs, seg_iters = 60, 6, 60
    elif n <= 1048576:
        warm, segs, seg_iters = 20, 5, 20
    else:
        warm, segs, seg_iters = 5, 4, 5

    src = _ADD_TEMPLATE
    repl = {
        _T['OP_ID']: str((kernel_source or {}).get('op_id') or 'add'),
        _T['MODE']: mode,
        _T['N_ELEMS']: str(n),
        _T['BLK']: str(blk),
        _T['GRID']: str(grid),
        _T['WARM']: str(warm),
        _T['SEGS']: str(segs),
        _T['SEG_ITERS']: str(seg_iters),
        _T['KERNEL']: '',
        '@@PLATFORM@@': name,
    }
    for k, v in repl.items():
        src = src.replace(k, v)
    return _map_src(src, pf)


# ---------------------------------------------------------------------------
# Compile / run / parse
# ---------------------------------------------------------------------------
def compile_cpp(compiler, src_text, out_bin, x_lang='luca', log_path=None, timeout=300):
    """Compile the single-file C++ with `$cc -x <lang> src.cpp -o bin`. Returns (exit_code, log_text)."""
    tmp_src = out_bin + '.src.cpp'
    with open(tmp_src, 'w', encoding='utf-8') as f:
        f.write(src_text)
    cmd = [compiler, '-x', x_lang, tmp_src, '-o', out_bin]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        log = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        log = ('compiler timeout after %ds' % timeout) + (e.stdout or b'').decode('utf-8', 'replace')
        r = None
    if log_path:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(log)
    return (r.returncode if r is not None else -1), log


def run_binary(bin_path, timeout=300):
    """Run the compiled artifact, capturing stdout/stderr. Returns (exit_code, stdout, stderr)."""
    try:
        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, '', 'binary timeout after %ds' % timeout
    except FileNotFoundError:
        return -2, '', 'binary not found: %s' % bin_path


RESULT_RE = re.compile(
    r'RESULT\s+per_iter_ms=([\d.eE+-]+)\s+std_ms=([\d.eE+-]+)\s+rounds=(\d+)\s+'
    r'max_abs_err=([\d.eE+-]+)\s+max_rel_err=([\d.eE+-]+)\s+bad=(\d+)')


def parse_output(stdout):
    """Parse program stdout: RESULT line + @DONE@ protocol. Returns a dict (fields may be absent)."""
    out = {'done': False, 'per_iter_ms': None, 'std_ms': None,
           'rounds': None, 'max_abs_err': None, 'max_rel_err': None, 'bad': None}
    if '@DONE@' in stdout:
        out['done'] = True
    m = RESULT_RE.search(stdout)
    if m:
        out['per_iter_ms'] = float(m.group(1))
        out['std_ms'] = float(m.group(2))
        out['rounds'] = int(m.group(3))
        out['max_abs_err'] = float(m.group(4))
        out['max_rel_err'] = float(m.group(5))
        out['bad'] = int(m.group(6))
    return out


# ---------------------------------------------------------------------------
# Shared hook shell
# ---------------------------------------------------------------------------
def load_payload():
    """Read the stdin JSON payload (_call_hook contract: {...payload, run_id})."""
    raw = sys.stdin.read() if not sys.stdin.isatty() else '{}'
    try:
        return json.loads(raw) or {}
    except json.JSONDecodeError:
        return {}


def emit(result):
    """Trailing-JSON line on stdout (_call_hook contract: splitlines()[-1] gets json.loads-ed)."""
    print(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


def my_platform(payload) -> str:
    """Infer the platform from the payload: explicit vendor_id wins; otherwise probe toolchain presence, then default to lingspeed."""
    vid = ''
    for key in ('vendor_id', 'vendor', 'platform'):
        v = payload.get(key)
        if isinstance(v, str) and v in PLATFORMS:
            vid = v
            break
    if vid:
        return vid
    for pid in PLATFORMS:
        tc = locate_toolchain(pid)
        if tc['found']:
            return pid
    return 'lingspeed'


def build_or_fail(payload, mode):
    """Shared failure path for compile/verify/perf: returns (False, result_dict) when the toolchain is missing."""
    pf_name = my_platform(payload)
    pf = platform(pf_name)
    tc = locate_toolchain(pf_name)
    if not tc['found']:
        return False, {
            'build_ok': False, 'exit_code': 1,
            'detail': '%s compiler not found (%s toolchain missing; candidates: %s)'
                      % (pf['x_lang'] + '-cc', pf['runtime_lib'],
                         ', '.join([x for x in pf['home_candidates']])),
            'platform': pf_name,
        }
    return True, tc


# ---------------------------------------------------------------------------
# Cluster real-hardware fallback (live mode: when no vendor toolchain is local,
# dispatch real-hardware compile/run to a k8s probe pod)
# Channel: adapters/_common/cluster_backend.py (node/image/credentials injected via
# KP_LIVE_* env vars; card released immediately after use)
# Trigger: only when cluster_backend.configured() (explicitly enabled by KP_LIVE_CLUSTER=1)
# The success path (local toolchain present) stays byte-identical -> zero drift in paper smoke tests.
# ---------------------------------------------------------------------------
try:
    import cluster_backend  # noqa: E402  (thin wrapper hooks already added _common to sys.path)
    _CB_OK = True
except Exception:
    cluster_backend = None  # type: ignore
    _CB_OK = False


def cluster_capable() -> bool:
    """Whether the cluster real-hardware channel is available (the live fallback condition when no local toolchain exists)."""
    return _CB_OK and cluster_backend is not None and cluster_backend.configured()


def _log_write(path, text):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text or '')
    except Exception:
        pass


def cluster_exec_compile(pf_name, src, workspace, op_id, t0, ts):
    """Cluster real-hardware compile (mode=compile smoke skeleton). Returns True once emitted."""
    t0 = t0 if t0 else time.monotonic()
    cb = cluster_backend
    try:
        r = cb.execute(src, mode='compile', tag=pf_name, wait_ready=300, settle=10.0)
    except Exception as e:
        _log_write(os.path.join(workspace, op_id + '.build.log'), 'cluster exec failed: %r' % e)
        emit({'build_ok': False, 'detail': 'cluster probe failed: %r' % e,
              'build_log_path': os.path.join(workspace, op_id + '.build.log'),
              'artifacts': [], 'toolchain': ts, 'env_snapshot': {'exec_mode': 'live'},
              'duration': round(time.monotonic() - t0, 3)})
        return True
    _log_write(os.path.join(workspace, op_id + '.build.log'), r.get('log', ''))
    if r.get('ok') and 'COMPILE_OK' in (r.get('log') or ''):
        host_ref = 'cluster://%s:/tmp/kp_live/%s/add' % (
            os.environ.get('KP_LIVE_NODE', '/'),
            r.get('workdir', ''))
        emit({'build_ok': True,
              'detail': 'lscc@%s compiled on real hardware (pod=%s) %s compiler %s'
                        % (r.get('pod', 'kp-probe'), r.get('pod', ''),
                           pf_label_short(pf_name), host_ref),
              'build_log_path': os.path.join(workspace, op_id + '.build.log'),
              'artifacts': [{'kind': 'kernel_pkg', 'ref': host_ref}],
              'toolchain': ts,
              'env_snapshot': {'exec_mode': 'live', 'cluster': os.environ.get('KP_LIVE_CLUSTER_NAME', ''),
                               'node': os.environ.get('KP_LIVE_NODE', ''),
                               'pod': r.get('pod', '')},
              'duration': round(time.monotonic() - t0, 3)})
        return True
    emit({'build_ok': False,
          'detail': 'cluster real-hardware compile failed exit=%s phase=%s tail=%s'
                    % (r.get('exit_code'), (r.get('detail') or '')[-80:],
                       (r.get('log') or '')[-300:]),
          'build_log_path': os.path.join(workspace, op_id + '.build.log'),
          'artifacts': [], 'toolchain': ts,
          'env_snapshot': {'exec_mode': 'live'},
          'duration': round(time.monotonic() - t0, 3)})
    return True


def cluster_exec_verify(pf_name, src, case, workspace, op_id, t0):
    """Cluster real-hardware verify (runs with RESULT parsing). Returns True once emitted."""
    cb = cluster_backend
    fail = lambda why: emit({'max_abs_err': 0.0, 'max_rel_err': 0.0, 'pass': False,  # noqa: E731
                             'detail': why})
    try:
        r = cb.execute(src, mode='perf', tag=pf_name, wait_ready=300, settle=10.0)
    except Exception as e:
        fail('cluster verify probe failed: %r' % e)
        return True
    _log_write(os.path.join(workspace, op_id + '.verify.cluster.log'), r.get('log', ''))
    if not r.get('ok'):
        fail('cluster real-hardware verify run failed exit=%s tail=%s'
             % (r.get('exit_code'), (r.get('log') or '')[-200:]))
        return True
    parsed = parse_output(r.get('log') or '')
    if not parsed['done'] or parsed['max_abs_err'] is None:
        fail('cluster verify has no RESULT line tail=%s' % (r.get('log') or '')[-200:])
        return True
    passed = parsed['max_abs_err'] <= 1e-5
    emit({'max_abs_err': parsed['max_abs_err'], 'max_rel_err': parsed['max_rel_err'],
          'pass': passed,
          'detail': 'cluster real-hardware verify@%s: case=%s abs=%.3e rel=%.3e %s'
                    % (r.get('pod', ''), case.get('id'), parsed['max_abs_err'],
                       parsed['max_rel_err'], 'PASS' if passed else 'FAIL')})
    return True


def cluster_exec_perf(pf_name, src, case, workspace, op_id, t0):
    """Cluster real-hardware perf (RESULT line -> timing). Returns True once emitted."""
    cb = cluster_backend
    fail = lambda why: emit({'timing': {'per_iter_ms': None, 'std_ms': None,  # noqa: E731
                                        'rounds': 0, 'samples_used': 0},
                             'detail': why})
    try:
        r = cb.execute(src, mode='perf', tag=pf_name, wait_ready=300, settle=10.0)
    except Exception as e:
        fail('cluster perf probe failed: %r' % e)
        return True
    _log_write(os.path.join(workspace, op_id + '.perf.cluster.log'), r.get('log', ''))
    if not r.get('ok'):
        fail('cluster real-hardware perf run failed exit=%s tail=%s'
             % (r.get('exit_code'), (r.get('log') or '')[-200:]))
        return True
    parsed = parse_output(r.get('log') or '')
    if not parsed['done'] or parsed['per_iter_ms'] is None:
        fail('cluster perf has no RESULT line tail=%s' % (r.get('log') or '')[-200:])
        return True
    emit({'timing': {'per_iter_ms': parsed['per_iter_ms'], 'std_ms': parsed['std_ms'] or 0.0,
                     'rounds': parsed['rounds'] or 0, 'samples_used': parsed['rounds'] or 0},
          'detail': 'cluster real-hardware perf@%s: case=%s per_iter=%.4f ms std=%.4f ms rounds=%d'
                    % (r.get('pod', ''), case.get('id'), parsed['per_iter_ms'],
                       parsed['std_ms'] or 0.0, parsed['rounds'] or 0)})
    return True


def pf_label_short(name):
    try:
        return platform(name).get('compiler_label', name)
    except Exception:
        return name


# ---------------------------------------------------------------------------
# The three hook bodies (shared by lingspeed/metax; thin wrapper hooks call run_* directly)
# The contract aligns field-by-field with the legacy lingspeed hooks/compile|verify|perf.py (zero drift);
# platform differences (compiler name / -x language / API prefix / device nodes / failure text)
# are all parameterized via my_platform(payload) -> platform(name).
# ---------------------------------------------------------------------------

def _load_kernel_def(build_artifact, workspace):
    """Restore the kernel definition from the compile archive; on failure return a minimal default (op_id)."""
    op_id = (build_artifact or {}).get('op_id') or 'add'
    kdef_path = os.path.join(workspace, op_id + '.kernel.json')
    if os.path.exists(kdef_path):
        try:
            with open(kdef_path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'op_id': op_id, 'source_tree': {'kind': 'template', 'mechanism': {}}}


def run_compile(payload=None, name=None):
    """v3_compile hook body: locate toolchain -> gen single-file C++ -> compile.

    Emits a BuildArtifact-compatible contract (same as the legacy lingspeed compile.py):
      {build_ok, detail, build_log_path, artifacts:[{kind,ref}],
       toolchain:{...}, env_snapshot, duration}
    """
    payload = payload if payload is not None else load_payload()
    pf_name = name or my_platform(payload)
    pf = platform(pf_name)
    kernel_source = payload.get('kernel_source') or {}
    workspace = payload.get('workspace') or '.'
    op_id = str(kernel_source.get('op_id') or 'add')
    t0 = time.monotonic()

    # ---- toolchain location (missing compiler -> explicit failure, no crash) ----
    tc = locate_toolchain(pf_name)
    ts = toolchain_summary(tc)
    log_path = os.path.join(workspace, op_id + '.build.log')
    os.makedirs(workspace, exist_ok=True)
    if not tc['found']:
        # ---- cluster real-hardware fallback (live: no local toolchain -> probe pod real-hardware compile) ----
        try:
            src_c = gen_add_cpp(kernel_source, case=None, mode='compile', name=pf_name)
        except Exception:
            src_c = None
        if src_c is not None and cluster_capable():
            cluster_exec_compile(pf_name, src_c, workspace, op_id, t0, ts)
            return
        emit({
            'build_ok': False,
            'detail': '%s not found; %s toolchain missing '
                      '(candidates: %s)' % (pf['compiler_label'], pf['runtime_lib'],
                                            ', '.join(x for x in pf['home_candidates'] if x)),
            'build_log_path': log_path,
            'artifacts': [], 'toolchain': ts, 'env_snapshot': {},
            'duration': round(time.monotonic() - t0, 3),
        })
        return

    # ---- generate single-file C++ (mode=compile smoke skeleton, includes RESULT output) ----
    try:
        src = gen_add_cpp(kernel_source, case=None, mode='compile', name=pf_name)
    except Exception as e:  # template generation failure -> explicit failure
        emit({'build_ok': False, 'detail': 'template gen failed: %r' % e,
              'build_log_path': log_path, 'artifacts': [], 'toolchain': ts,
              'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3)})
        return

    # ---- compile ----
    out_bin = os.path.join(workspace, op_id + '.bin')
    rc, clog = compile_cpp(tc['compiler'], src, out_bin, x_lang=pf['x_lang'],
                           log_path=log_path)
    if rc != 0 or not os.path.exists(out_bin):
        emit({
            'build_ok': False,
            'detail': '%s failed (exit=%s): %s' % (pf['compiler_label'], rc, (clog or '')[-400:]),
            'build_log_path': log_path, 'artifacts': [], 'toolchain': ts,
            'env_snapshot': {}, 'duration': round(time.monotonic() - t0, 3),
        })
        return

    # ---- archive the kernel definition (so verify/perf reuse the same kernel/mechanism) ----
    kdef_path = os.path.join(workspace, op_id + '.kernel.json')
    try:
        with open(kdef_path, 'w', encoding='utf-8') as f:
            json.dump(kernel_source, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

    emit({
        'build_ok': True,
        'detail': '%s single-file %s C++ compiled successfully: %s' % (pf['compiler_label'], pf['runtime_lib'],
                                                  os.path.basename(out_bin)),
        'build_log_path': log_path,
        'artifacts': [{'kind': 'kernel_pkg', 'ref': os.path.basename(out_bin)}],
        'toolchain': ts,
        'env_snapshot': {'exec_mode': 'live', 'device': ','.join(pf['dev_nodes'])},
        'duration': round(time.monotonic() - t0, 3),
    })


def run_verify(payload=None, name=None):
    """v4_verify hook body: gen golden-verify C++ -> compile -> run -> pass decision.

    Emits the same contract as the legacy lingspeed verify.py: {max_abs_err, max_rel_err, pass, detail}
    pass threshold: abs_err <= 1e-5 (fp32, aligned with the pipeline tolerance_config).
    """
    payload = payload if payload is not None else load_payload()
    pf_name = name or my_platform(payload)
    pf = platform(pf_name)
    ba = payload.get('build_artifact') or {}
    case = payload.get('case') or {}
    workspace = payload.get('workspace') or '.'
    t0 = time.monotonic()

    tc = locate_toolchain(pf_name)
    if not tc['found']:
        # ---- cluster real-hardware fallback (live: no local toolchain -> real-hardware verify) ----
        kernel_def = _load_kernel_def(ba, workspace)
        op_id = kernel_def.get('op_id') or 'add'
        try:
            src_v = gen_add_cpp(kernel_def, case, mode='verify', name=pf_name)
        except Exception:
            src_v = None
        if src_v is not None and cluster_capable():
            cluster_exec_verify(pf_name, src_v, case, workspace, op_id, t0)
            return
        emit({'max_abs_err': 0.0, 'max_rel_err': 0.0, 'pass': False,
              'detail': '%s not found (%s toolchain missing)'
                        % (pf['compiler_label'], pf['runtime_lib'])})
        return

    kernel_def = _load_kernel_def(ba, workspace)
    op_id = kernel_def.get('op_id') or 'add'

    # ---- generate the golden-verify program -> compile -> run ----
    try:
        src = gen_add_cpp(kernel_def, case, mode='verify', name=pf_name)
    except Exception as e:
        emit({'max_abs_err': 0.0, 'max_rel_err': 0.0, 'pass': False,
              'detail': 'template gen failed: %r' % e})
        return

    out_bin = os.path.join(workspace, op_id + '.verify.bin')
    rc, clog = compile_cpp(tc['compiler'], src, out_bin, x_lang=pf['x_lang'],
                           log_path=os.path.join(workspace, op_id + '.verify.build.log'))
    if rc != 0 or not os.path.exists(out_bin):
        emit({'max_abs_err': 0.0, 'max_rel_err': 0.0, 'pass': False,
              'detail': '%s(verify) failed exit=%s: %s'
                        % (pf['compiler_label'], rc, (clog or '')[-300:])})
        return

    xc, out, err = run_binary(out_bin)
    parsed = parse_output(out)
    if xc != 0 or not parsed['done'] or parsed['max_abs_err'] is None:
        emit({'max_abs_err': 0.0, 'max_rel_err': 0.0, 'pass': False,
              'detail': 'run failed exit=%s done=%s err=%s'
                        % (xc, parsed['done'], (err or '')[-300:])})
        return

    passed = parsed['max_abs_err'] <= 1e-5
    n = _resolve_n(case, default_n=0)
    emit({
        'max_abs_err': parsed['max_abs_err'],
        'max_rel_err': parsed['max_rel_err'],
        'pass': passed,
        'detail': '%s live verify: case=%s n=%d abs=%.3e rel=%.3e %s'
                  % (pf['runtime_lib'], case.get('id'), n, parsed['max_abs_err'],
                     parsed['max_rel_err'], 'PASS' if passed else 'FAIL'),
    })


def run_perf(payload=None, name=None):
    """v5_profile hook body: gen timing C++ -> compile -> run -> RESULT line parsing.

    Emits the same contract as the legacy lingspeed perf.py:
      {timing: {per_iter_ms, std_ms, rounds, samples_used}, detail}
    Consumed by the pipeline: per_iter_ms(ms->us), std_ms(->us), rounds|samples_used(->n_samples).
    """
    payload = payload if payload is not None else load_payload()
    pf_name = name or my_platform(payload)
    pf = platform(pf_name)
    ba = payload.get('build_artifact') or {}
    case = payload.get('case') or {}
    workspace = payload.get('workspace') or '.'
    t0 = time.monotonic()

    tc = locate_toolchain(pf_name)
    if not tc['found']:
        # ---- cluster real-hardware fallback (live: no local toolchain -> real-hardware perf) ----
        kernel_def = _load_kernel_def(ba, workspace)
        op_id = kernel_def.get('op_id') or 'add'
        try:
            src_p = gen_add_cpp(kernel_def, case, mode='perf', name=pf_name)
        except Exception:
            src_p = None
        if src_p is not None and cluster_capable():
            cluster_exec_perf(pf_name, src_p, case, workspace, op_id, t0)
            return
        emit({'timing': {'per_iter_ms': None, 'std_ms': None,
                         'rounds': 0, 'samples_used': 0},
              'detail': '%s not found (%s toolchain missing)'
                        % (pf['compiler_label'], pf['runtime_lib'])})
        return

    kernel_def = _load_kernel_def(ba, workspace)
    op_id = kernel_def.get('op_id') or 'add'

    try:
        src = gen_add_cpp(kernel_def, case, mode='perf', name=pf_name)
    except Exception as e:
        emit({'timing': {'per_iter_ms': None, 'std_ms': None,
                         'rounds': 0, 'samples_used': 0},
              'detail': 'template gen failed: %r' % e})
        return

    out_bin = os.path.join(workspace, op_id + '.perf.bin')
    rc, clog = compile_cpp(tc['compiler'], src, out_bin, x_lang=pf['x_lang'],
                           log_path=os.path.join(workspace, op_id + '.perf.build.log'))
    if rc != 0 or not os.path.exists(out_bin):
        emit({'timing': {'per_iter_ms': None, 'std_ms': None,
                         'rounds': 0, 'samples_used': 0},
              'detail': '%s(perf) failed exit=%s: %s'
                        % (pf['compiler_label'], rc, (clog or '')[-300:])})
        return

    xc, out, err = run_binary(out_bin)
    parsed = parse_output(out)
    if xc != 0 or not parsed['done'] or parsed['per_iter_ms'] is None:
        emit({'timing': {'per_iter_ms': None, 'std_ms': None,
                         'rounds': 0, 'samples_used': 0},
              'detail': 'run failed exit=%s done=%s err=%s'
                        % (xc, parsed['done'], (err or '')[-300:])})
        return

    emit({
        'timing': {
            'per_iter_ms': parsed['per_iter_ms'],
            'std_ms': parsed['std_ms'] or 0.0,
            'rounds': parsed['rounds'] or 0,
            'samples_used': parsed['rounds'] or 0,
        },
        'detail': '%s d2h-sync perf: case=%s per_iter=%.4f ms std=%.4f ms rounds=%d'
                  % (pf['runtime_lib'], case.get('id'), parsed['per_iter_ms'],
                     parsed['std_ms'] or 0.0, parsed['rounds'] or 0),
    })


# ---------------------------------------------------------------------------
# Self-check entry (--toolchain / -t [platform])
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    which = 'lingspeed'
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if args and args[0] in PLATFORMS:
        which = args[0]
    if '-t' in sys.argv or '--toolchain' in sys.argv:
        t = locate_toolchain(which)
        print(json.dumps(toolchain_summary(t), ensure_ascii=False, indent=2))
        sys.exit(0 if t['found'] else 1)
    print('mxcagen.py — shared tooling for metax/lingspeed hooks (import me; '
          'platforms: %s)' % ', '.join(sorted(PLATFORMS)))
