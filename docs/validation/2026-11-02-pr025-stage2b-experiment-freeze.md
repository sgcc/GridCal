# PR-025 Stage2B 北京自适应实验预执行冻结

状态：`PRE_EXECUTION`。本文件与 `pr025-stage2b-experiment-freeze.json` 冻结同一合同；尚未重建候选网、运行北京 PF、评价保真度或执行自适应控制器。

## 提交与历史边界

| 对象 | 固定值 |
|---|---|
| PR-025R 实现提交 / tree | `68bd37f1bc49e2bc31fa327fe4e66bf43e9398f7` / `13a5530f7df5a8663f27cf19dedfc14fa3a602e1` |
| Stage2B 身份基线提交 / tree | `b5a4303bf9c8203632e517438ad1504c6319987c` / `acf2516105ef6e56c768673450e72063187a96f3` |
| 身份基线 | `STAGE2B_IDENTITY_BASELINE_V1`，`ReductionAction.fingerprint` |
| 身份基线 artifact SHA-256 | `d37e7ffc6030fc9598126ee4a120955e0a01c1a8035e9097bcb903746dfcd37a` |
| 包 / PyPSA | `0.7.0` / `1.2.4` |
| Stage2A | `FAIL / STRUCTURAL_INVALID / NOT_ACCEPTED`；证据摘要 `13a39a0468e19f80218dbbbfb48ccb531bded3c1cb2ac508d50412275446fab2`，保持不变 |

PR-025R 合并版本确立了 135 条历史行动中 125 READY、10 REJECTED，拒绝类别均为 `STRICT_SINGLETON_CONFLICT`。PR-025R 公开审计未持久化逐行动身份集合，故 `PR025R_IDENTITY_CONTINUITY = NOT_INDEPENDENTLY_VERIFIABLE`；不得声称 Stage2B 身份集合与 PR-025R 逐身份相同。本次 Stage2B 身份基线是在候选重建前首次预注册的身份层证据。

## 来源与行动合同

| 绑定 | SHA-256 / 数量 |
|---|---|
| S1 语义模型 | `6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150` |
| 来源 sidecar | `7fc151484448d06fe430b43a8618503a0caed123b2c9530d8b9de19afb3c6165` |
| 显式真实来源账本 | `5293db89fee4cc9e773880afe8b5a17d00b75b78aa8cb5f15697d92170976bd7`；639 条 Line、18 条 L+ 关联 |
| 已解析保护集合 | `45a7be7cfa3070507c8c8615a4676a6c7ed4b0b04ee1c571f27fcb4d14186f38` |
| 历史行动目录 | 135；`70f5932d4d27b572425cadfb63cc79fe505f844ec9e49e81b5bbc08cab6fb99e` |
| 当前 readiness | 125 READY、10 REJECTED；`STRICT_SINGLETON_CONFLICT` |
| 规范 READY 集合 | `64103a85f435ab6bbcd2e163016b93881a8efb6450506270586fe31892340261` |
| 规范 REJECTED 集合 | `234c9e432982d92d151a59d289def2ce1172fea66529fc8e59b5538214614d3f` |
| 规范 readiness 报告 | `dc1326859c3b69a0a9ce83b2d97700d708a65501163c85bf1add0383b4df967b` |

全部 135 个 `ReductionAction.fingerprint` 均有效且唯一。READY 与 REJECTED 不相交，并集恰为历史目录。初始 active set **恰为 125 个 READY 行动**，其集合指纹按相同 READY 角色定义，等于上述规范 READY 指纹；10 个拒绝行动留在历史证据中，不进入 active set。旧的 action-ID 集合指纹只是兼容性观察值，不是 Stage2B 身份基线。

## 起始策略和自适应规则

起始意图为 `PR022_BASE_L1_RADIAL_ACTIONS`；电压 cutoff 禁用；并行走廊使用 `preserve_protected_members`；seed 为 17；aggregation backend 为 `pypsa`，one-port 配置为 `Load`，line-length factor 为 1.0；分区 planner 为 `pypsa_modularity`，connectivity policy 为 `reject`；task profile 为 `planning_aggregation`，配置保护规则数为 0。使用显式真实来源账本。配置分区目标 1392 是策略输入，**不是** Stage2B 实际候选 Bus 数或接受标准。不会为 10 个拒绝行动补造替代行动、重跑 planner 或改动阈值。

完整配置摘要：`bdd0c6f33c27c458d06e1a64f1a000673e53a938ca89eab4bcf3f591513f3bf6`。排除 `input.path` 与 `run.output_dir` 的语义配置摘要：`f261fb7f5d4dd5d417617afd642193cfd22895f309beb9c14cb5157330e9197e`。前者保留来源冻结绑定，后者用于路径无关的 build-attempt 身份。

自适应策略为 `HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION`，最多 20 次迭代；新增 Bus 与 branch 成本权重均为 1.0；改进容差 `1e-12`；Bus 与 branch 的最低压缩比例均为 5%。正成本按“归一化 HARD 违例改进 / 扩展成本”排序。零成本且有正改进的候选优先于正成本候选；存在 HARD 违例时，零改进候选不可选。依赖采用 dependent closure；并列时依次按新增 Bus、branch 数、region fingerprint 和排序后的行动 StableId 决定。自适应配置摘要：`64c42fedf3ee497e63a3a50437137619a1291de57d4352def5ba3e1a3769d23f`。

## PF 与证据边界

PF 合同固定为单一 `now` snapshot、`PypsaAcPowerFlowExecutor`、公开 `Network.pf`、`pypsa.newton_raphson`、容差 `1e-8`；保留来源控制及 tap/移相日程。不授权重调控制、重调度或恢复操作。

| 合同 | SHA-256 |
|---|---|
| 运行点 | `334a7275a2e837722016db2b732d23e2c30ce392e3b1f592ade5d5f995706c1e` |
| 指标策略 | `745cdb4b5cdb6d8547e1ac9139be988b6cd393b9e1f7838bfb245aa32f6db5ad` |
| adaptive runtime PF executor | `221369358669457b9ebe013fb6443fdff008585b93d718f302ad814df3e29ac6` |
| 来源 topology / device schedule | `706719ce503a4e5744b571ece1ff72776e778578c7fdf6e665a8315ed04b8b67` / `f01eaab395ab2e692d86ec290bf6159df3fb4e7ca1311b52487849f4308754ca` |
| 阈值集 `PR022_AC_PF_FIDELITY_V1` | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |

阈值是预注册的项目实验限值，不是 IEEE 或国家标准限值；本次未修改。执行证据 schema 固定为 `adaptive-reduction-evidence/v2`，必须绑定实现、身份基线和最终冻结提交/tree、来源与行动摘要、配置、运行点、指标、阈值及 PF executor 摘要。冻结提交/tree 应由后续执行记录引用实际提交值，本文件不能自引用未来提交。若候选未成功物化，`materialized_candidate_fingerprint = null`。

私有运行命名空间为 `runs/beijing-pr025-stage2b/`；公开验证文件位于 `docs/validation/`。历史 Stage2A 的 1392 Bus / 504 Line 只是旧预期，不作为 Stage2B 接受条件。

**预执行绑定 Gate：PASS。** 候选重建：NO；北京 PF：NO；自适应控制器：NO；projection fidelity：`NOT VALIDATED`；physical equivalence：`NOT CLAIMED`。
