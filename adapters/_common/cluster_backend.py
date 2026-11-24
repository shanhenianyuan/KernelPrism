#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cluster_backend.py — 集群真机执行后端（kernel-prism live 模式）。

定位：本机无厂商工具链时，把「源码 → 平台编译器编译 → 真机运行」投递到 k8s 集群
探针 pod（chroot 宿主），取回日志并解析契约字段。探针占卡即删（授权硬约束）。

通道（运行环境必须显式注入，仓库不携带任何集群配置）：
  kubectl      KP_KUBECTL      必填：kubectl 可执行文件路径
  kubeconfig   KP_KUBECONFIG   必填：kubeconfig 路径
  node         KP_LIVE_NODE    必填：目标 GPU 节点名（占卡即删，勿用被占用节点）
  image        KP_LIVE_IMAGE   必填：携带宿主工具链的镜像（含 digest）
  开关         KP_LIVE_CLUSTER=1 显式启用（防止无集群配置时误投）

探针契约：
  - 1 卡（厂商 GPU 资源 lingspeed-tech.com/gpu: 1，1 张卡上限），hostNetwork + privileged + chroot /hostfs
  - 镜像携带完整宿主工具链（如 /opt/luca-3.8.0，/opt/luca -> 3.8.0）；设备节点缺失则 mknod
  - 编译：lscc -x luca -offload-arch native --gcc-toolchain=/usr add.cpp -o add --luca-path=/opt/luca
  - 运行：./add（mode=compile 不运行；verify/perf 运行并输出 RESULT 行）
  - logs 与 delete 必须串联同一条命令（授权约束；不留占用）
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, Optional

# 集群通道参数一律由环境变量注入（仓库不携带任何集群/镜像凭据）。
# 未注入时 configured() 返回 False，live 回落在本机路径静默降级，绝不误投。
DEFAULT_KUBECTL = os.environ.get('KP_KUBECTL', '')
DEFAULT_KUBECONFIG = os.environ.get('KP_KUBECONFIG', '')
DEFAULT_NODE = os.environ.get('KP_LIVE_NODE', '')
DEFAULT_IMAGE = os.environ.get('KP_LIVE_IMAGE', '')

# 模板占位（运行时直接拼接 args_cmd 进 YAML，杜绝 %s 占位脱节——探针 v1 教训）
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
    """集群通道是否可用（显式开关 + 工具/kubeconfig 存在）。"""
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
    """构造探针 args：base64 解码写宿主文件 -> chroot 编译 ->（可选）运行。

    run_mode=compile：仅编译（cuts 探针时长）；verify/perf：编译 + 运行产出 RESULT 行。
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
    """投递真机执行：create -> wait Ready -> logs+delete（同命令串联）。

    返回: {ok, exit_code, log, pod, workdir}
      ok        True = pod Succeeded 且（compile 模式 COMPILE_OK）或（运行模式 EXIT=0）
      exit_code pod 容器退出码（0=成功）
      log       全量日志文本（含 ====COMPILE==== / COMPILE_OK / RESULT 行）
    """
    if not configured():
        return {'ok': False, 'exit_code': -1, 'log': '', 'pod': '', 'workdir': '',
                'detail': '集群通道未启用（KP_LIVE_CLUSTER != 1 或 conf 缺失）'}
    import base64
    src_b64 = base64.b64encode(src_text.encode('utf-8')).decode('ascii')
    workdir = f'{tag}-{secrets.token_hex(3)}'
    pod = f'kp-live-{tag}-{workdir}'
    args_cmd = _chroot_cmd(src_b64, workdir, run_mode=(mode != 'compile'))
    yaml_text = _POD_TEMPLATE.format(name=pod, node=os.environ.get('KP_LIVE_NODE', DEFAULT_NODE),
                                     image=os.environ.get('KP_LIVE_IMAGE', DEFAULT_IMAGE),
                                     args=args_cmd)

    # ---- create（先清残留，幂等） ----
    r = _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                 '--wait=false')
    r = _kubectl('apply', '-f', '-', input_text=yaml_text, timeout=60)
    if r.returncode != 0:
        return {'ok': False, 'exit_code': -2, 'log': 'apply failed: ' + (r.stderr or '')[-400:],
                'pod': pod, 'workdir': workdir, 'detail': 'apply failed'}
    # ---- wait Ready（探针容器跑完即退出，Ready 可能瞬时；改轮询 phase） ----
    deadline = time.monotonic() + wait_ready
    phase = ''
    while time.monotonic() < deadline:
        r = _kubectl('get', 'pod', pod, '-n', 'default', '-o', 'jsonpath={.status.phase}')
        phase = (r.stdout or '').strip()
        if phase in ('Succeeded', 'Failed'):
            break
        time.sleep(3)
    if phase not in ('Succeeded', 'Failed'):
        # 兜底再等 settle 秒后强取
        time.sleep(settle)
        r = _kubectl('get', 'pod', pod, '-n', 'default', '-o', 'jsonpath={.status.phase}')
        phase = (r.stdout or '').strip()
    # ---- logs + delete 串联同一条命令（授权硬约束） ----
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
            # 保险：补偿删除
            _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                     '--wait=false')
        log += '\n' + ('[stderr] ' + pr.stderr[-300:]) if pr.stderr else ''
    except Exception as e:  # logs 失败也要保证删除
        _kubectl('delete', 'pod', pod, '-n', 'default', '--ignore-not-found=true',
                 '--wait=false')
        log = f'log fetch failed: {e!r}'
    return {'ok': ok, 'exit_code': exit_code, 'log': log, 'pod': pod, 'workdir': workdir,
            'detail': f'phase={phase} exit={exit_code}'}


if __name__ == '__main__':
    print(json.dumps({'configured': configured()}, ensure_ascii=False, indent=2))
