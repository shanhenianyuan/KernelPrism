---
name: kernel-prism
description: "Use for GPU kernel dev across vendors: 8-stage pipeline (model understanding → operator analysis → kernel generation → compilation → accuracy verification → performance analysis → auto-optimization → operator registration) as an anvil app; vendor adapters incl. ascend-cannbot (CANNBot) and lingspeed (lscc -x luca)."
version: 2.1.0
author: anvil
license: proprietary
metadata:
  hermes:
    tags: [KernelPrism, 8-stage-pipeline, vendor-spi, cannbot, ascend, lingspeed, lscc, luca, kernel, gqa, gpu-trace]
    related_skills: [k8s-gpu-npu-inventory, serving-llms-vllm, anvil-app-integration]
---

# KernelPrism — Intelligent Operator Development Platform for All GPU Vendors (an anvil app)

KernelPrism is an operator development capability module built on the anvil intelligent base: it provides capability only and is an anvil app (base = hermes compute kernel + opencode kernel components + python runtime), loosely coupling each vendor's GPU operator development capability via adapters (vendor adapter / SPI) and uniformly orchestrating it into an **8-stage pipeline**:

```
model understanding → operator analysis → kernel generation → compilation → accuracy verification → performance analysis → auto-optimization → operator registration
```

Architecture philosophy, design docs, and quick start: repo root `README.md` (8-stage pipeline / platform parameterization / paper-live dual modes / cluster real-hardware fallback / vendorkb).

## Implementation (Phase 1 skeleton, 2026-11-21)

Code location: `apps/kernel-prism/` (anvil app form, modeled on argus)

```
apps/kernel-prism/
├── manifest/install.json        # anvil app registration manifest
├── adapters/ascend/             # Ascend adapter minimal set (04 SPI §2 contract)
│   ├── manifest.json            #   adapter declaration (vendor_id/version/device/DSL/license)
│   ├── vendorkb/asc910.json     #   hardware knowledge base (A12: microarchitecture/ISA/alignment constraints)
│   └── hooks/probe.py|status.py #   device probe + capability status (live/paper detection)
└── src/
    ├── kp.py                    # CLI: kp run / kp vendor
    ├── artifacts.py             # artifact schema + transactional persistence + BottleneckReport
    ├── vendor.py                # adapter registry (manifest validation/capability matrix/lifecycle)
    └── pipeline.py              # 8-stage pipeline engine (gates/fallback/budget/model-level closed loop)
```

**Four user constraints (2026-11-21) implemented in code**:
- ① bottleneck analysis before generation → `build_bottleneck_report` (end-to-end time share × Amdahl upper-bound ranking; "optimize because it feels slow" is forbidden; non-bottleneck operators are marked "correctness baseline only" and do not enter the generation stage)
- ② bottleneck-driven candidate pool + parallel design ideas → `DESIGN_IDEAS_LIB` provides ≥2 mechanism-level design ideas per operator category, **parallel workers = number of ideas**, and ideas must pass vendorkb hardware-fact checks (`_idea_hw_ok`); if insufficient, downgrade and mark "insufficient ideas"
- ③ operator speedup propagation to model level → stage 6 `model_level` (Amdahl conversion; if not propagated → "benefit not propagated" back to stage 2) + stage 7 model-level guidance (HAP met but model not benefited → not judged successful)
- ④ incorporate hardware knowledge → `vendorkb/` hardware knowledge base (asc910.json); stage 2 attaches it to `hardware_context`, stage 3 checks ideas against hardware facts

## CLI Usage (Phase 1)

```bash
export KERNEL_PRISM_ROOT=$PWD      # repo root (one level above this SKILL.md's directory)
cd $PWD/..                          # output follows anvil_output/ convention

# vendor adapter management
python3 $KERNEL_PRISM_ROOT/src/kp.py vendor list
python3 $KERNEL_PRISM_ROOT/src/kp.py vendor add ascend

# run the 8-stage pipeline (paper = no-hardware smoke; live = real hardware)
python3 $KERNEL_PRISM_ROOT/src/kp.py run add --vendor ascend --target smoke
python3 $KERNEL_PRISM_ROOT/src/kp.py run add --vendor ascend --model-goal throughput
```

Output directory: `anvil_output/kp-runs/<run_id>/` (run_id = `kp-<vendor>-<task>-<date>-<hex>`; audit log: audit.jsonl + artifacts: artifacts/ + REPORT.md).

## Vendor Adapter Navigation

| Vendor | Adapter | Status | Navigation |
|------|--------|------|------|
| Ascend | ascend-cannbot | Phase 1 minimal set (manifest+vendorkb+probe) | `adapters/ascend/` + `references/ascend-cannbot.md` |
| Lingspeed | lingspeed | existing multi-round on-card verification | §A below + `references/luca_toolchain.md` |
| Future GPU | vendor-N | SPI contract ready | `references/8-stage-pipeline.md` |

(§A Lingspeed LINGSPEED workflow, 8-stage gate discipline, on-card pod four-piece kit, red lines, deliverable conventions — same as v2.0.0, omitted here; see `references/luca_toolchain.md` and `references/8-stage-pipeline.md` when needed.)
