# KernelPrism — 异构 GPU 智能算子开发平台

> 让 Kernel 开发从「手写 + 试错」进化为「瓶颈驱动 + 自动进化」。
> 一套代码, 多厂商 GPU 通用: 昇腾 / 灵汐 / 沐曦, 新平台即插即用。

KernelPrism 是一个面向**异构 GPU Kernel 开发**的智能能力模块:由大模型驱动的
8 段流水线, 把「模型理解 → 算子分析 → Kernel 生成 → 编译 → 精度验证 →
性能分析 → 自动优化 → 算子入库」全链路自动化。它统一了此前散落在各厂商
工具链中的 Kernel 开发能力, 汇聚生态优势、规避各自缺陷, 并沉淀为可复用的
能力层, 供智能体(如 anvil)直接调用。

---

## 先进理念

### 1. 8 段流水线, 段段可执行、段段可审计

```
v0 模型理解 ─► v1 算子分析 ─► v2 Kernel 生成 ─► v3 编译 ─► v4 精度验证
     │              │              │              │            │
     └──────────────┴──► v5 性能分析 ─► v6 自动优化 ─► v7 算子入库
```

每段都有**明确的输入/输出工件契约**(ModelSpec / BottleneckReport / OpSpec /
BuildArtifact / PrecisionReport / PerfReport / OptimizedKernel / RegistryEntry),
全程写入 `audit.jsonl` 审计流水, 机器可读。任何一段的成功/失败/回退都有记录,
没有「黑盒魔法」。

### 2. 瓶颈驱动的优化, 拒绝「觉得慢就优化」

v1 先做**端到端耗时占比 × Amdahl 上界排序**的瓶颈定位(BottleneckReport),
非瓶颈算子标注「仅正确性基线」、不进生成段;候选池 = 真实设计思路
(并行 worker 数 = 思路数, 禁凑数);每个设计思路须通过 `vendorkb` 硬件知识库的
事实核查, 「结合硬件知识」不是口号而是门禁。

### 3. 平台参数化: 一套引擎, N 个厂商

lingspeed(灵汐 LUCA)、metax(沐曦 MXMACA)、ascend(昇腾 AscendC)共用
同一套生成器(`adapters/_common/mxcagen.py`), 平台差异收敛为**配置字段**
(compiler_label: lscc / mxcc; runtime: LUCA / MXMACA / CANN; 设备节点;
对齐约束; perf baseline), 而不是复制粘贴 N 份代码。双卡同源的 lc_* ↔ mc_*、
`-x luca` ↔ `-x maca` 等事实由 v2 段在运行时做前缀映射, 天然兼容。

### 4. paper / live 双执行模式

- **paper**: 无硬件也能全 8 段走通——编译=语法检查、验证=Python golden、
  性能=机内基准、优化=迭代搜索。开发期冒烟回归、CI 都能跑。
- **live**: 真机执行——probe 探测到硬件工具链后自动切换为真机编译 / 验证 / 测速,
  产出真实数据。

### 5. 集群真机回落: 本机无工具链, 自动投递 k8s 真机

本机没有厂商 SDK 时, `cluster_backend.py` 把「源码 → 编译 → 真机运行」封装成
探针 pod 投递到 k8s 集群的 GPU 节点(chroot 宿主工具链), 跑完即删
(占卡即删硬约束), 集群通道参数(kubectl/kubeconfig/node/镜像)全部由
`KP_LIVE_*` 环境变量注入, 仓库不携带任何集群凭据。paper 成功路径逐字节不变,
升级到 live 零漂移。

### 6. vendorkb: 硬件知识作为一等公民

每个适配器自带 `vendorkb/<chip>.json` 硬件知识库, 沉淀真机实证数据:
微架构/ISA/对齐约束/工具链真身/实测性能基线。v2 段用这些**事实**指导生成与
优化, 优化反馈链(feedback_chain)携带真机证据, 而不是模型拍脑袋。

### 7. 最小适配器契约: 纯声明 manifest + 通用兜底 8 段

新平台接入 = 一个 `manifest.json` 纯声明(设备/DSL/许可/入口), 8 段流水线
自动走平台通用兜底; 需要专属逻辑时, 用薄包装 hook(compile/verify/perf/probe)
覆盖。平台零厂商代码, 适配器目录才允许装厂商专属代码 —— **License 隔离**。

### 8. 智能体友好: 机器可读的完成协议

运行结束输出 `@DONE@ run_id=... status=OK` 单行协议, 产物落
`anvil_output/kp-runs/<run_id>/`(工件 + audit.jsonl + REPORT.md),
anvil 等智能体可无歧义地消费结果继续编排。

### 9. stdlib-only + 中文优先

全部代码仅依赖 Python 标准库, 无第三方运行时要求; 注释与文档中文优先,
降低异构 GPU 工程团队的使用门槛。

---

## 真实实证: 灵汐 LINGSPEED 八段 live 真机收口

`add` 算子 3 个 case 全部真机编译、真机验证、真机测速(2026-11-23, 集群探针
占卡即删、零残留):

| 指标 | 结果 |
|------|------|
| 端到端 8 段 status | OK (audit t=262s, exit=live) |
| 精度 (max_abs_err) | 4.768e-07 (真机验证链 PASS) |
| 自动优化进化 | 3 候选 → 真机编译 → 真机验证 → 真机测速, 进化 3 轮 |
| 性能演化 (speedup) | 11.4 → 12.5 → 14.4 |
| 探针纪律 | pod 完成即删, `NO_PROBE_PODS_LEFT` |

真机基准(写入 vendorkb/lsca.json): per_iter_ms=0.024473, rounds=100,
max_abs_err=4.768e-07, bad=0。

---

## 快速开始

```bash
# 1. 查看已注册适配器与能力矩阵
python3 src/kp.py vendor list

# 2. 登记一个适配器 (如 ascend)
python3 src/kp.py vendor add ascend

# 3. 跑 8 段流水线 — paper 模式(无硬件也能全走通)
python3 src/kp.py run add --vendor ascend --exec-mode paper --budget-iters 2

# 4. 跑 8 段流水线 — live 模式(真机; 需厂商工具链或 KP_LIVE_* 集群通道)
python3 src/kp.py run add --vendor lingspeed --exec-mode live

# 5. 模型级目标(吞吐/时延): 自动启用模型级闭环门禁
python3 src/kp.py run gqa --vendor lingspeed --model-goal 吞吐
```

输出遵循 `anvil_output/kp-runs/<run_id>/` 约定: `audit.jsonl`(段级审计) +
`artifacts/`(全工件) + `REPORT.md`(人读报告)。

## live 集群回落(可选)

```bash
export KP_LIVE_CLUSTER=1                 # 显式启用, 防误投
export KP_KUBECTL=/path/to/kubectl       # kubectl 可执行文件
export KP_KUBECONFIG=/path/to/config     # kubeconfig
export KP_LIVE_NODE=gpu-node-2           # 目标 GPU 节点(占卡即删)
export KP_LIVE_IMAGE=repo/img@sha256:…   # 携带宿主工具链的镜像
```

未配置时 live 在本机路径静默降级, 绝不误投。

---

## 目录结构

```
kernel-prism/
├── src/
│   ├── kp.py           # CLI: kp run / kp vendor (paper|live 双模式)
│   ├── pipeline.py     # 8 段流水线引擎(门禁/回退/预算/模型级闭环)
│   ├── vendor.py       # 适配器注册表(manifest 校验/能力矩阵/生命周期)
│   └── artifacts.py    # 工件 schema + 事务性落盘 + BottleneckReport
├── adapters/
│   ├── _common/
│   │   ├── mxcagen.py        # 平台参数化生成器(lingspeed/metax 共用)
│   │   └── cluster_backend.py# 集群真机回落后端(KP_LIVE_* 注入)
│   ├── ascend/   (manifest + vendorkb/asc910.json + hooks)
│   ├── lingspeed/(manifest + vendorkb/lsca.json + hooks)
│   └── metax/    (manifest + vendorkb/c550.json + hooks)
├── manifest/install.json     # anvil app 注册清单
├── verify_phase1.py          # Phase 1 验收(8 段走通 + 工件全校验)
└── src/SKILL.md              # 开发者技能文档
```

## 适配器一览

| 厂商 | 适配器 | 设备 | DSL | 状态 |
|------|--------|------|-----|------|
| 华为昇腾 | ascend | asc910 / asc950 | AscendC / Triton / TileLang / PyTo | Phase 1 最小集 |
| 灵汐领速 | lingspeed | LSC1000 (X710-M) | LUCA (`lscc -x luca`) | live 真机收口 |
| 沐曦 | metax | C550 | MXMACA (`mxcc -x maca`) | 适配器就绪, 待真机 |

新平台接入流程: 复制最小适配器骨架 → 填 `manifest.json` → 按需覆盖薄包装 hook →
`kp vendor add <id>` → 跑 paper 冒烟 → live 真机收口。

---

## License 与合规

- KernelPrism 平台自身: 代码零厂商专有内容, 仅 Python 标准库。
- 各厂商适配器需遵守其 SDK 许可声明(manifest.json `license` 字段):
  LUCA SDK / MXMACA SDK / CANN OSL 均为`设备限用 + 禁止再分发`,
  接入时需显式 consent, 仓库不夹带任何厂商受限源码。

## Roadmap

- [x] Phase 1: 8 段流水线骨架 + paper 冒烟 + ascend 最小适配器
- [x] Phase 2: lingspeed live 真机 8 段收口(3 轮进化 11.4→14.4)
- [ ] Phase 3: metax(c550) 真机实证 (待集群通道恢复)
- [ ] 更多算子库 (matmul/gqa/softmax…)、模型级收益传导
- [ ] 平台参数化配置外部化、探针目标按平台参数化

---

**KernelPrism** — 异构 GPU Kernel 开发的能力层, 为智能体而生。
