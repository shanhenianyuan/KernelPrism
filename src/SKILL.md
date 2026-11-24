---
name: kernel-prism
description: "Use for GPU kernel dev across vendors: 8-stage pipeline (模型理解→算子分析→Kernel生成→编译→精度验证→性能分析→自动优化→算子入库) as an anvil app; vendor adapters incl. ascend-cannbot (CANNBot) and lingspeed (lscc -x luca)."
version: 2.1.0
author: anvil
license: proprietary
metadata:
  hermes:
    tags: [KernelPrism, 8-stage-pipeline, vendor-spi, cannbot, ascend, lingspeed, lscc, luca, kernel, gqa, gpu-trace]
    related_skills: [k8s-gpu-npu-inventory, serving-llms-vllm, anvil-app-integration]
---

# KernelPrism — 全 GPU 厂商智能算子开发平台（anvil 的 app）

KernelPrism 是依托 anvil 智能底座的算子开发能力模块：只做能力、是 anvil 的 app（底座=hermes 计算内核 + opencode 内核组件 + python 运行时），把各厂商 GPU 算子开发能力以适配器（vendor adapter / SPI）松耦合接入，统一编排到 **8 段流水线**：

```
模型理解 → 算子分析 → Kernel 生成 → 编译 → 精度验证 → 性能分析 → 自动优化 → 算子入库
```

架构理念、设计文档与快速开始: 仓库根 `README.md`（8 段流水线 / 平台参数化 / paper-live 双模式 / 集群真机回落 / vendorkb）。

## 实现（Phase 1 骨架，2026-11-21）

代码落点：`apps/kernel-prism/`（anvil app 形态，参照 argus）

```
apps/kernel-prism/
├── manifest/install.json        # anvil app 注册清单
├── adapters/ascend/             # 昇腾适配器最小集（04 SPI §2 契约）
│   ├── manifest.json            #   适配器声明（vendor_id/版本/设备/DSL/许可）
│   ├── vendorkb/asc910.json     #   硬件知识库（A12：微架构/ISA/对齐约束）
│   └── hooks/probe.py|status.py #   设备探测 + 能力状态（live/paper 判定）
└── src/
    ├── kp.py                    # CLI：kp run / kp vendor
    ├── artifacts.py             # 工件 schema + 事务性落盘 + BottleneckReport
    ├── vendor.py                # 适配器注册表（manifest 校验/能力矩阵/生命周期）
    └── pipeline.py              # 8 段流水线引擎（门禁/回退/预算/模型级闭环）
```

**用户四点约束（2026-11-21）已落码**：
- ① 生成前瓶颈分析定位 → `build_bottleneck_report`（端到端耗时占比 × Amdahl 上界排序，禁「觉得慢就优化」，非瓶颈算子标注「仅正确性基线」不进生成段）
- ② 瓶颈驱动候选池 + 设计思路并行 → `DESIGN_IDEAS_LIB` 按算子类别给 ≥2 种机制级设计思路，**并行 worker 数 = 思路数**，思路须过 vendorkb 硬件事实核查（`_idea_hw_ok`），不足降级标注「思路不足」
- ③ 算子加速传导模型级 → 段6 `model_level`（Amdahl 折算，未传导 → 「收益未传导」回段2）+ 段7 模型级导向（HAP 达标但模型未受益 → 不判成功）
- ④ 结合硬件知识 → `vendorkb/` 硬件知识库（asc910.json），段2 挂入 `hardware_context`、段3 做思路硬件事实核查

## CLI 用法（Phase 1）

```bash
export KERNEL_PRISM_ROOT=$PWD      # 仓库根（此 SKILL.md 所在目录的上一级）
cd $PWD/..                          # 输出遵循 anvil_output/ 约定

# 厂商适配器管理
python3 $KERNEL_PRISM_ROOT/src/kp.py vendor list
python3 $KERNEL_PRISM_ROOT/src/kp.py vendor add ascend

# 跑 8 段流水线（paper=无硬件冒烟；live=真机）
python3 $KERNEL_PRISM_ROOT/src/kp.py run add --vendor ascend --target smoke
python3 $KERNEL_PRISM_ROOT/src/kp.py run add --vendor ascend --model-goal 吞吐
```

输出目录：`anvil_output/kp-runs/<run_id>/`（run_id = `kp-<vendor>-<task>-<date>-<hex>`；审计 audit.jsonl + 工件 artifacts/ + REPORT.md）。

## 厂商适配器导航

| 厂商 | 适配器 | 状态 | 导航 |
|------|--------|------|------|
| 昇腾 | ascend-cannbot | Phase 1 最小集（manifest+vendorkb+probe） | `adapters/ascend/` + `references/ascend-cannbot.md` |
| 灵汐 | lingspeed | 既有多轮上卡实证 | 下方 §A + `references/luca_toolchain.md` |
| 未来 GPU | vendor-N | SPI 契约就绪 | `references/8-stage-pipeline.md` |

（§A 灵汐 LINGSPEED 工作流、8 段门禁纪律、上卡 pod 四件套、红线、交付物惯例——与 v2.0.0 相同，此处略；需要时查看 `references/luca_toolchain.md` 与 `references/8-stage-pipeline.md`。）
