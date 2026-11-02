# PR-025 Stage2B 北京实验首次执行：候选重建阻断

`PR025_STAGE2B_EXECUTION = FAIL`；`STAGE2B_EXPERIMENT_OUTCOME = NOT_EXECUTED`。本次使用已冻结的 0.7.0 实现、身份基线与执行合同；未修改任何算法、阈值、配置或行动目录。PR-025R 实现提交 `68bd37f1bc49e2bc31fa327fe4e66bf43e9398f7`，身份基线提交 `b5a4303bf9c8203632e517438ad1504c6319987c`，执行冻结提交 `099bd05f143674be640b2247c44a86a2d3e06260`。

## 实际执行到的阶段

| Gate | 结果 |
|---|---|
| 冻结输入与当前 125 READY / 10 REJECTED 身份绑定 | PASS |
| S1 新鲜 AC PF | `PF_CONVERGED`；1 个子网，5 次迭代，最终误差 `1.419087716618256e-09` |
| S1 inventory | 1527 Bus / 639 Line / 1344 Transformer / 0 Link；1983 total branches |
| S1 语义摘要前后 | `6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150` / `6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150`，一致 |
| 两次新鲜 S1 PF 数值结果 | 一致；原始结果摘要 `e54df91555b979598f5b0d7d702cde532cb05c94592c0dedfeb4d9eadbf88634` |
| 初始 active set | 125；规范指纹 `64103a85f435ab6bbcd2e163016b93881a8efb6450506270586fe31892340261` |
| build attempt | `b14b82c9c16656e17361d3590099cc740fb0496b98ee2e12ece6e21c352a382b` |
| 候选物化 | FAIL；`materialized_candidate_fingerprint = null`；Bus/branch inventory 未形成 |
| runtime 失败状态 | `STRUCTURAL_INVALID`；实际最终结构 Gate 尚未执行 |
| AC readiness / 候选 PF / fidelity / controller / replay | 均 `NOT_RUN` |

首个失败阶段为 `CANDIDATE_RECONSTRUCTION`，失败码为 `CANDIDATE_RECONSTRUCTION_FAILED`。底层合同拒绝了一个未被 authoritative active action mapping 记录的多对一 `branch_map` 效果。分类为 `UNJOURNALED_MANY_TO_ONE_BRANCH_MAP_EFFECT`。此处只记录实际阻断，不在同次实验中补造行动、改变商图、放宽 Gate 或更改配置。

私有失败证据清单 SHA-256 为 `11a14e961fc7ccc86aa4012491bef470da803263a8d78c2b8b09e5f86b78a471`；其中保留底层异常和来源身份，未纳入本公开文件。10 个历史拒绝行动没有激活；源模型未变；配置和阈值未漂移；Stage2A 证据未覆盖。

由于初始候选未成功物化，未产生 controller terminal reason，也未形成 `adaptive-reduction-evidence/v2` 的完整运行及 fresh replay 证据。**未接受任何北京压缩候选。** Projection fidelity：`NOT VALIDATED`；physical equivalence：`NOT CLAIMED`。后续实现修复须在独立任务中进行，并重新冻结、执行。
