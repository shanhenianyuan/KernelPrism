# KernelPrism — The Self-Evolving Capability Base for Heterogeneous GPU Kernel Development

> Software is no longer a static tool chained to preset rules. KernelPrism is a
> **capability base conceived by intelligence, ever-evolving, and
> self-creating**: it turns "hand-written kernels + trial-and-error tuning"
> into a **closed-loop 8-stage development-and-optimization chain** driven by an
> LLM, born from intelligence, growing on every run, and callable by **any**
> agent framework.
>
> One engine, every domestic GPU: **Ascend (AscendC) · Lingspeed (LUCA) ·
> Metax (MXMACA)** — one codebase, instant platform adoption.

KernelPrism is a **living capability base** for heterogeneous GPU kernel
development: an LLM-driven 8-stage closed loop —
model understanding → operator analysis → kernel generation → compilation →
precision verification → performance profiling → automated optimization →
operator registration — that **conceives, evolves, and ships kernels on its
own**. It unifies kernel-development capabilities scattered across vendor
toolchains, pools the strengths of each ecosystem while avoiding its
pitfalls, and emerges from the **anvil** intelligent native OS as a reusable
capability layer, consumable by **any agent framework** through a
machine-readable protocol.

---

## What It Is, What It Is Not

| KernelPrism | Static tooling |
|------|------|
| A platform **conceived by intelligence**: candidates are genuine design rationales, gated by a hardware knowledge base | Preset templates + guesswork heuristics, frozen at shipping time |
| **Self-evolving**: every run's real-hardware evidence feeds back into the candidate pool and the knowledge base | Static scripts that repeat the same blind search every time |
| **Self-innovating**: parallel workers explore multiple real design ideas and converge on the best | Single hard-coded strategy, one-shot |
| A capability base **callable by any agent framework** (anvil, Codex, Claude, ...) via `@DONE@ run_id=… status=OK` | A closed UI/CLI that only a human can drive |
| **Domestic-GPU native**: Ascend / Lingspeed / Metax share one engine | N vendor-specific, copy-pasted, siloed toolchains |

---

## Advanced Concepts

### 1. An 8-stage pipeline — every stage executable, every stage auditable

```
v0 Understand ─► v1 Analyze ─► v2 Generate ─► v3 Compile ─► v4 Verify
     │              │              │              │            │
     └──────────────┴──► v5 Profile ─► v6 Optimize ─► v7 Register
```

Every stage has an **explicit input/output artifact contract** (ModelSpec /
BottleneckReport / OpSpec / BuildArtifact / PrecisionReport / PerfReport /
OptimizedKernel / RegistryEntry), and the whole run is written to an
`audit.jsonl` machine-readable audit trail. Success, failure, and fallback of
any stage are all recorded — no black-box magic.

### 2. Bottleneck-driven optimization — rejecting "optimize because it feels slow"

v1 first performs **end-to-end time-share ratio × Amdahl upper-bound ordering**
for bottleneck localization (BottleneckReport). Operators that are not
bottlenecks are marked as "correctness baseline only" and skip the generation
stages. The candidate pool equals real design ideas (parallel-worker count =
idea count; no padding); every design idea must pass a fact-check against the
`vendorkb` hardware knowledge base — "combined with hardware knowledge" is a
gate, not a slogan.

### 3. Platform parameterization: one engine, N vendors

lingspeed (Lingspeed LUCA), metax (Metax MXMACA), and ascend (Ascend AscendC)
share the same generator (`adapters/_common/mxcagen.py`); platform differences
collapse into **configuration fields** (compiler_label: lscc / mxcc; runtime:
LUCA / MXMACA / CANN; device node; alignment constraints; perf baseline)
instead of copy-pasting N codebases. Same-origin twin-hardware facts such as
`lc_*` ↔ `mc_*` and `-x luca` ↔ `-x maca` are prefix-mapped by the v2 stage at
runtime, so they are natively compatible.

### 4. paper / live dual execution modes

- **paper**: the full 8 stages run with no hardware — compile = syntax check,
  verify = Python golden, perf = in-process benchmark, optimize = iterative
  search. Ideal for dev-time smoke regression and CI.
- **live**: real-hardware execution — once a probe detects a hardware
  toolchain, it automatically switches to real-machine compile / verify /
  benchmark and produces authentic data.

### 5. Cluster real-hardware fallback: no local toolchain? auto-deploy to k8s

When the local host lacks a vendor SDK, `cluster_backend.py` packages
"source → compile → run on hardware" into a probe pod and delivers it to a GPU
node on the k8s cluster (chroot into the host toolchain), deleting the pod as
soon as the run finishes (the occupy-then-delete hard constraint). Cluster
channel parameters (kubectl / kubeconfig / node / image) are all injected via
`KP_LIVE_*` environment variables — the repository carries **no cluster
credentials at all**. The paper success path stays byte-for-byte identical;
upgrading to live is zero-drift.

### 6. vendorkb: hardware knowledge as a first-class citizen

Every adapter ships its own `vendorkb/<chip>.json` hardware knowledge base
distilled from real-machine evidence: microarchitecture / ISA / alignment
constraints / toolchain identity / measured perf baseline. The v2 stage uses
these **facts** to steer generation and optimization, and the optimization
feedback chain (`feedback_chain`) carries real-hardware evidence instead of
model guesswork.

### 7. Minimal adapter contract: pure-declarative manifest + generic 8-stage fallback

Adding a new platform takes a single declarative `manifest.json` (device / DSL /
license / entrypoints); the 8-stage pipeline automatically runs the
platform-generic fallback. When vendor-specific logic is needed, thin wrapper
hooks (compile/verify/perf/probe) override it. The platform carries zero vendor
code; only adapter directories may host vendor-specific code — **License
isolation**.

### 8. Agent-agnostic capability base — callable by ANY agent framework

KernelPrism is not a UI, not a plugin, not a hardcoded rule book — it is a
**capability layer** that any agent framework (anvil, Codex, Claude Agent, an
OpenAI-compatible agent, CI pipelines — anything that can run a process) can
consume as a ready-made skill:

- **Machine-readable contract**: every run ends with a single
  `@DONE@ run_id=... status=OK` line; artifacts land in
  `anvil_output/kp-runs/<run_id>/` (artifacts + audit.jsonl + REPORT.md).
  Results are consumed unambiguously — no parsing brittle prose.
- **Zero runtime dependencies** (stdlib only) + **no cluster credentials in
  the repo** — safe to hand to any agent without secret provisioning.
- **Self-describing capability matrix** (`kp vendor list`): any agent can
  introspect what platforms, devices, and 8 stages are available, then act.
- **Drop-in orchestration**: an agent passes a task definition (op_costs /
  shapes / precision requirements), reads `@DONE@`, and continues — kernel
  development becomes one callable step in the agent's own loop.

It therefore serves as the **kernel-development capability base for the anvil
intelligent native OS** and, by the same contract, for any other agent
framework.

### 9. Self-evolving: every run hardens the base

Optimization is not a one-shot search — it is a **learning loop**:

- The candidate pool is regenerated per run as *real* design rationales, and
  the `feedback_chain` carries real-hardware evidence back into the next
  generation (structured, multi-source feedback: compiler output, precision
  deltas, perf measurements).
- `vendorkb/<chip>.json` is distilled from real-machine measurements — the
  knowledge base *grows* with each live closure instead of decaying into
  stale documentation.
- Selection is capped by quantified gates (Amdahl upper bound, gain
  threshold, stop-on-convergence), so evolution converges on the best
  verified kernel rather than wandering.

The base is **conceived by intelligence** (LLM-driven generation), **evolves
through evidence** (real-hardware feedback), and **innovates autonomously**
(parallel real design ideas + hardware-fact gates) — not a static tool of
preset rules.

### 10. stdlib-only + English-first

All code depends solely on the Python standard library — no third-party
runtime requirements; comments and docs are English-first, lowering the entry
bar for heterogeneous-GPU engineering teams.

---

## Real-World Evidence: Lingspeed 8-Stage live Hard-Machine Closure

All 3 cases of the `add` operator were compiled, verified, and benchmarked on
real hardware (2026-11-23, cluster probes occupy-then-delete, zero residue):

| Metric | Result |
|------|------|
| End-to-end 8-stage status | OK (audit t=262s, exit=live) |
| Precision (max_abs_err) | 4.768e-07 (real-machine verification chain PASS) |
| Auto-optimization evolution | 3 candidates → real compile → real verify → real benchmark, 3 evolution rounds |
| Perf evolution (speedup) | 11.4 → 12.5 → 14.4 |
| Probe discipline | pod deleted on completion, `NO_PROBE_PODS_LEFT` |

Real-machine baseline (written into vendorkb/lsca.json): per_iter_ms=0.024473,
rounds=100, max_abs_err=4.768e-07, bad=0.

---

## Quick Start

```bash
# 1. List registered adapters and the capability matrix
python3 src/kp.py vendor list

# 2. Register an adapter (e.g. ascend)
python3 src/kp.py vendor add ascend

# 3. Run the 8-stage pipeline — paper mode (no hardware needed, full run)
python3 src/kp.py run add --vendor ascend --exec-mode paper --budget-iters 2

# 4. Run the 8-stage pipeline — live mode (real hardware; needs a vendor
#    toolchain or a KP_LIVE_* cluster channel)
python3 src/kp.py run add --vendor lingspeed --exec-mode live

# 5. Model-level goal (throughput / latency): auto-enables model-level closure gate
python3 src/kp.py run gqa --vendor lingspeed --model-goal throughput
```

Output follows the `anvil_output/kp-runs/<run_id>/` convention: `audit.jsonl`
(stage-level audit) + `artifacts/` (all artifacts) + `REPORT.md`
(human-readable report).

## Live Cluster Fallback (optional)

```bash
export KP_LIVE_CLUSTER=1                 # explicit opt-in, prevents mis-targeting
export KP_KUBECTL=/path/to/kubectl       # kubectl executable
export KP_KUBECONFIG=/path/to/config     # kubeconfig
export KP_LIVE_NODE=gpu-node-2           # target GPU node (occupy-then-delete)
export KP_LIVE_IMAGE=repo/img@sha256:…   # image carrying the host toolchain
```

When unconfigured, live silently degrades to the local path — it never
mis-targets.

---

## Directory Layout

```
kernel-prism/
├── src/
│   ├── kp.py           # CLI: kp run / kp vendor (paper|live dual mode)
│   ├── pipeline.py     # 8-stage pipeline engine (gates/fallback/budget/model-level closure)
│   ├── vendor.py       # adapter registry (manifest validation/capability matrix/lifecycle)
│   └── artifacts.py    # artifact schemas + transactional persistence + BottleneckReport
├── adapters/
│   ├── _common/
│   │   ├── mxcagen.py        # platform-parameterized generator (shared: lingspeed/metax)
│   │   └── cluster_backend.py# cluster real-hardware fallback backend (KP_LIVE_* injection)
│   ├── ascend/   (manifest + vendorkb/asc910.json + hooks)
│   ├── lingspeed/(manifest + vendorkb/lsca.json + hooks)
│   └── metax/    (manifest + vendorkb/c550.json + hooks)
├── manifest/install.json     # anvil app registration manifest
├── verify_phase1.py          # Phase 1 acceptance (8 stages pass + full artifact validation)
└── src/SKILL.md              # developer skill document
```

## Adapter Matrix

| Vendor | Adapter | Device | DSL | Status |
|------|--------|--------|-----|------|
| Huawei Ascend | ascend | asc910 / asc950 | AscendC / Triton / TileLang / PyTo | Phase 1 minimal set |
| Lingspeed | lingspeed | LSC1000 (X710-M) | LUCA (`lscc -x luca`) | live hard-machine closure |
| Metax | metax | C550 | MXMACA (`mxcc -x maca`) | adapter ready, live pending |

New-platform onboarding: copy the minimal adapter skeleton → fill in
`manifest.json` → override thin wrapper hooks as needed → `kp vendor add <id>`
→ paper smoke → live hard-machine closure.

---

## License & Compliance

- The KernelPrism platform itself: zero vendor-proprietary content, Python
  stdlib only.
- Each vendor adapter must comply with its SDK license declaration
  (`license` field in manifest.json): LUCA SDK / MXMACA SDK / CANN OSL are all
  device-locked + non-redistributable, with explicit consent required at
  onboarding; the repository carries no vendor-restricted source code.

## Roadmap

- [x] Phase 1: 8-stage pipeline skeleton + paper smoke + ascend minimal adapter
- [x] Phase 2: lingspeed live real-machine 8-stage closure (3 evolution rounds 11.4→14.4)
- [ ] Phase 3: metax (c550) real-machine validation (pending cluster channel recovery)
- [ ] Wider operator library (matmul/gqa/softmax…), model-level benefit propagation
- [ ] Externalize platform-parameter config; per-platform probe targeting

---

**KernelPrism** — the capability layer of heterogeneous-GPU kernel development,
built for agents.
