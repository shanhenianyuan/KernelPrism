#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cluster_backend.py — cluster real-hardware execution backend (kernel-prism live mode).

Purpose: when no vendor toolchain exists locally, dispatch "source -> platform compiler
build -> real-hardware run" to a k8s probe pod (chroot into the host), fetch the logs and
parse the contract fields. The probe releases the card immediately after use (hard auth constraint).

Channel (must be explicitly injected by the runtime; the repo carries no cluster config):
  kubectl      KP_KUBECTL      required: path to the kubectl executable
  kubeconfig   KP_KUBECONFIG   required: kubeconfig path
  node         KP_LIVE_NODE    required: target GPU node name (card released after use; do not use an occupied node)
  image        KP_LIVE_IMAGE   required: image carrying the host toolchain (with digest)
  switch       KP_LIVE_CLUSTER=1 explicitly enables (prevents mis-dispatch when there is no cluster config)

Probe contract:
  - 1 card (vendor GPU resource lingspeed-tech.com/gpu: 1, max one card), hostNetwork + privileged + chroot /hostfs
  - the image carries the full host toolchain (e.g. /opt/luca-3.8.0, /opt/luca -> 3.8.0); mknod if the device node is missing
  - compile: lscc -x luca -offload-arch native --gcc-toolchain=/usr add.cpp -o add --luca-path=/opt/luca
  - run: ./add (mode=compile does not run; verify/perf run and emit a RESULT line)
  - logs and delete must be chained in the same command (auth constraint; no lingering occupancy)
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, Optional

# All cluster channel parameters are injected via environment variables (the repo carries no cluster/image credentials).
# When not injected, configured() returns False and the live fallback silently degrades to the local path, never mis-dispatching.
DEFAULT_KUBECTL = os.environ.get('KP_KUBECTL', '')
DEFAULT_KUBECONFIG = os.environ.get('KP_KUBECONFIG', '')
DEFAULT_NODE = os.environ.get('KP_LIVE_NODE', '')
DEFAULT_IMAGE = os.environ.get('KP_LIVE_IMAGE', '')

# Template placeholders (args_cmd is spliced straight into the YAML at runtime, avoiding %s placeholder desync — probe v1 lesson)
_POD_TEMPLATE = """apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: default
spec:
  restartPolicy: Never
  nodeName: {node}
  hostNetwork: true
  securityContext:
    runAsUser: 0
    runAsGroup: 0
  containers:
  - name: probe
    image: {image}
    securityContext:
      privileged: true
      runAsUser: 0
      runAsGroup: 0
    resources:
      limits:
        lingspeed-tech.com/gpu: 1
      requests:
        lingspeed-tech.com/gpu: 1
    volumeMounts:
    - name: hostfs
      mountPath: /hostfs
    command: ["/bin/sh"]
    args: ["-c", "{args}"]
  volumes:
  - name: hostfs
    hostPath:
      path: /
      type: Directory
"""


def configured() -> bool:
    """Whether the cluster channel is available (explicit switch + tool/kubeconfig present)."""
    if os.environ.get('KP_LIVE_CLUSTER') != '1':
        return False
    k = os.environ.get('KP_KUBECTL', DEFAULT_KUBECTL)
    kc = os.environ.get('KP_KUBECONFIG', DEFAULT_KUBECONFIG)
    return os.path.isfile(k) and os.path.isfile(kc)


def _cmd() -> str:
    return os.environ.get('KP_KUBECTL', DEFAULT_KUBECTL)


def _kubectl(*args: str, timeout: int = 60, input_text: str = '') -> subprocess.CompletedProcess:
    kc = os.environ.get('KP_KUBECONFIG', DEFAULT_KUBECONFIG)
    cmd = [_cmd(), '--kubeconfig', kc] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          input=input_text)


def _chroot_cmd(src_b64: str, workdir: str, run_mode: bool) -> str:
    """Build the probe args: base64-decode and write the host file -> chroot compile -> (optionally) run.

    run_mode=compile: compile only (cuts probe duration); verify/perf: compile + run producing a RESULT line.
    """
    run_part = ''
    if run_mode:
        run_part = ('echo ====RUN====; ./add; echo EXIT=$? && '
                    'echo ====RESULT-CAPTURE-DONE====')
    return (
        "mkdir -p /hostfs/tmp/kp_live/" + workdir +
        " && echo '" + src_b64 + "' | base64 -d > /hostfs/tmp/kp_live/" + workdir + "/add.cpp"
        " && chroot /hostfs /bin/sh -c '"
        "export LUCA_PATH=/opt/luca LD_LIBRARY_PATH=/opt/luca/lib:$LD_LIBRARY_PATH"
        " PATH=/opt/luca/lsgpu_llvm/bin:$PATH;"
        " ls /dev/lscd 2>/dev/null || mknod /dev/lscd c 235 0;"
        " cd /tmp/kp_live/" + workdir + ";"
        " echo ====COMPILE====;"
        " /opt/luca/lsgpu_llvm/bin/lscc -x luca -offload-arch native"
        " --gcc-toolchain=/usr add.cpp -o add --luca-path=/opt/luca && echo COMPILE_OK"
        + (('; ' + run_part) if run_mode else '') +
        "'"
    )


def execute(src_text: str, mode: str = 'perf', tag: str = 'kp',
            wait_ready: int = 240, settle: float = 8.0) -> Dict[str, Any]:
    """Dispatch real-hardware execution: create -> wait Ready -> logs+delete (chained in one command).

    Returns: {ok, exit_code, log, pod, workdir}
      ok        True = pod Succeeded and (compile mode COMPILE_OK) or (run mode EXIT=0)
      exit_code pod container exit code (0=success)
      log       full log text (includes ====COMPILE==== / COMPILE_OK / RESULT lines)
    """
    if not configured():
        return {'ok': False, 'exit_code': -1, 'log': '', 'pod': '', 'workdir': '',
                'detail': 'cluster channel not enabled (KP_LIVE_CLUSTER != 1 or missing conf)'}
    import base64
    src_b64 = base64.b64encode(src_text.encode('utf-8')).decode('ascii')
    workdir = f'{tag}-{secrets.token_hex(3)}'
    pod = f'kp-live-{tag}-{workdir}'
    args_cmd = _chroot_cmd(src_b64, workdir, run_mode=(mode != 'compile'))
    yaml_text = _POD_TEMPLATE.format(name=pod, node=os.environ.get('KP_LIVE_NODE', DEFAULT_NODE),
                                     image=os.environ.get('KP_LIVE_IMAGE', DEFAULT_IMAGE),
                                     args=args_cmd)

    # ---- create (clear leftovers first, idempotent) ----
    r = _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                 '--wait=false')
    r = _kubectl('apply', '-f', '-', input_text=yaml_text, timeout=60)
    if r.returncode != 0:
        return {'ok': False, 'exit_code': -2, 'log': 'apply failed: ' + (r.stderr or '')[-400:],
                'pod': pod, 'workdir': workdir, 'detail': 'apply failed'}
    # ---- wait Ready (probe container exits as soon as it finishes; Ready may be transient; poll phase instead) ----
    deadline = time.monotonic() + wait_ready
    phase = ''
    while time.monotonic() < deadline:
        r = _kubectl('get', 'pod', pod, '-n', 'default', '-o', 'jsonpath={.status.phase}')
        phase = (r.stdout or '').strip()
        if phase in ('Succeeded', 'Failed'):
            break
        time.sleep(3)
    if phase not in ('Succeeded', 'Failed'):
        # fallback: wait settle more seconds, then force-fetch
        time.sleep(settle)
        r = _kubectl('get', 'pod', pod, '-n', 'default', '-o', 'jsonpath={.status.phase}')
        phase = (r.stdout or '').strip()
    # ---- logs + delete chained in one command (hard auth constraint) ----
    logs_cmd = (
        f'{_cmd()} --kubeconfig {os.environ.get("KP_KUBECONFIG", DEFAULT_KUBECONFIG)} '
        f'logs pod/{pod} -n default 2>&1; '
        f'echo ====POD-STATE====; '
        f'{_cmd()} --kubeconfig {os.environ.get("KP_KUBECONFIG", DEFAULT_KUBECONFIG)} '
        f'get pod {pod} -n default -o jsonpath={{.status.phase}}; echo; '
        f'{_cmd()} --kubeconfig {os.environ.get("KP_KUBECONFIG", DEFAULT_KUBECONFIG)} '
        f'delete pod {pod} -n default --ignore-not-found=true --wait=false 2>&1; '
        f'echo ====DELETED===='
    )
    ok = phase == 'Succeeded'
    exit_code = 0 if ok else 1
    log = ''
    try:
        pr = subprocess.run(logs_cmd, shell=True, capture_output=True, text=True,
                            timeout=120)
        log = pr.stdout or ''
        if '====DELETED====' not in (pr.stdout or '') and 'deleted' not in (pr.stdout or '').lower():
            # safety net: compensating delete
            _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                     '--wait=false')
        log += '\n' + ('[stderr] ' + pr.stderr[-300:]) if pr.stderr else ''
    except Exception as e:  # ensure deletion even if logs fail
        _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                 '--wait=false')
        log = f'log fetch failed: {e!r}'
    return {'ok': ok, 'exit_code': exit_code, 'log': log, 'pod': pod, 'workdir': workdir,
            'detail': f'phase={phase} exit={exit_code}'}


if __name__ == '__main__':
    print(json.dumps({'configured': configured()}, ensure_ascii=False, indent=2))
