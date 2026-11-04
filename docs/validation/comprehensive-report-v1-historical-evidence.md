# Historical Comprehensive Report Evidence

Historical artifacts are read-only inputs. Their experiment identity, software version, model digest, and evaluation convention remain separate. PR-022 values are never copied into Stage2C evidence.

Runtime values from different package or software versions are diagnostic only; no cross-version speedup is computed.

| Experiment | Case | Scenario | Package | Model digest | Source artifact SHA-256 | Evaluation convention |
| --- | --- | --- | --- | --- | --- | --- |
| PR020_REAL_CASE_STRUCTURE | BEIJING | beijing-base | 0.3.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda | Structural final Gate; physical fidelity not evaluated. |
| PR020_REAL_CASE_STRUCTURE | BEIJING | beijing-low_115 | 0.3.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda | Structural final Gate; physical fidelity not evaluated. |
| PR020_REAL_CASE_STRUCTURE | BEIJING | beijing-lt110 | 0.3.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda | Structural final Gate; physical fidelity not evaluated. |
| PR020_REAL_CASE_STRUCTURE | JILIN | jilin-base | 0.3.0 | 2ceb9219686671e406c4d9186358245a58b927f06fb115b0fa7ec52ad66cb128 | fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda | Structural final Gate; physical fidelity not evaluated. |
| PR020_REAL_CASE_STRUCTURE | JILIN | jilin-lt110 | 0.3.0 | 2ceb9219686671e406c4d9186358245a58b927f06fb115b0fa7ec52ad66cb128 | fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda | Structural final Gate; physical fidelity not evaluated. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | base | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_35 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_37 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_110 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_115 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_230 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR021_BEIJING_VOLTAGE_POLICY_MATRIX | BEIJING | cut_330 | 0.4.0 | 09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e | 99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450 | Voltage cutoff structural matrix; exact source layers; no PF fidelity claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | base | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_330 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_230 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_115 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_110 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_37 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR022_BEIJING_STATIC_SNAPSHOT_AC_PF | BEIJING | cut_35 | 0.5.0 | NOT_RECORDED | c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436 | Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim. |
| PR025_STAGE2C_EXECUTION | BEIJING | initial-candidate | 0.8.0 | 6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150 | cecd31a1f4f793dd5eb40430da3dfebcf7a6b9ebee7522f424121c0bd760e5f8 | Adaptive Stage2C initial candidate; candidate metric values absent in source; not cross-filled. |

## Recorded observations

### PR020_REAL_CASE_STRUCTURE / BEIJING / beijing-base

Declared source schema: `NOT_RECORDED`; package: `0.3.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| s1_bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| s1_line_count | 639 | NOT_RECORDED | AVAILABLE |
| accepted_bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| candidate_bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | 0.08840864440078586 | NOT_RECORDED | AVAILABLE |
| candidate_compression_fraction | 0.08840864440078586 | NOT_RECORDED | AVAILABLE |
| accepted_line_count | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l0_source_line_count | 639 | NOT_RECORDED | AVAILABLE |
| l1_bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| l1_line_count | 504 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_preserved_line_count | 504 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_absorbed_internal_count | 135 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_aggregated_count | 0 | NOT_RECORDED | AVAILABLE |
| l1_derived_line_count | 0 | NOT_RECORDED | AVAILABLE |
| protected_l0_line_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_preserved_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_lost_count | 0 | NOT_RECORDED | AVAILABLE |
| l_plus_association_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_base_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_shunt_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_terminal_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| final_structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |

### PR020_REAL_CASE_STRUCTURE / BEIJING / beijing-low_115

Declared source schema: `NOT_RECORDED`; package: `0.3.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| s1_bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| s1_line_count | 639 | NOT_RECORDED | AVAILABLE |
| accepted_bus_count | 1395 | NOT_RECORDED | AVAILABLE |
| candidate_bus_count | 1395 | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | 0.08644400785854617 | NOT_RECORDED | AVAILABLE |
| candidate_compression_fraction | 0.08644400785854617 | NOT_RECORDED | AVAILABLE |
| accepted_line_count | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l0_source_line_count | 639 | NOT_RECORDED | AVAILABLE |
| l1_bus_count | 1396 | NOT_RECORDED | AVAILABLE |
| l1_line_count | 508 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_preserved_line_count | 508 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_absorbed_internal_count | 131 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_aggregated_count | 0 | NOT_RECORDED | AVAILABLE |
| l1_derived_line_count | 0 | NOT_RECORDED | AVAILABLE |
| protected_l0_line_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_preserved_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_lost_count | 0 | NOT_RECORDED | AVAILABLE |
| l_plus_association_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_base_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_shunt_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_terminal_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| final_structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |

### PR020_REAL_CASE_STRUCTURE / BEIJING / beijing-lt110

Declared source schema: `NOT_RECORDED`; package: `0.3.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| s1_bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| s1_line_count | 639 | NOT_RECORDED | AVAILABLE |
| accepted_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| candidate_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| candidate_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| accepted_line_count | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l0_source_line_count | 639 | NOT_RECORDED | AVAILABLE |
| l1_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| l1_line_count | 637 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_preserved_line_count | 637 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_absorbed_internal_count | 2 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_aggregated_count | 0 | NOT_RECORDED | AVAILABLE |
| l1_derived_line_count | 0 | NOT_RECORDED | AVAILABLE |
| protected_l0_line_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_preserved_count | 189 | NOT_RECORDED | AVAILABLE |
| protected_l1_lost_count | 0 | NOT_RECORDED | AVAILABLE |
| l_plus_association_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_base_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_shunt_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| l_plus_terminal_preserved_count | 18 | NOT_RECORDED | AVAILABLE |
| final_structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |

### PR020_REAL_CASE_STRUCTURE / JILIN / jilin-base

Declared source schema: `NOT_RECORDED`; package: `0.3.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| s1_bus_count | 1803 | NOT_RECORDED | AVAILABLE |
| s1_line_count | 847 | NOT_RECORDED | AVAILABLE |
| accepted_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| candidate_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | 0.01552967276760954 | NOT_RECORDED | AVAILABLE |
| candidate_compression_fraction | 0.01552967276760954 | NOT_RECORDED | AVAILABLE |
| accepted_line_count | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l0_source_line_count | 847 | NOT_RECORDED | AVAILABLE |
| l1_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| l1_line_count | 819 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_preserved_line_count | 819 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_absorbed_internal_count | 28 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_aggregated_count | 0 | NOT_RECORDED | AVAILABLE |
| l1_derived_line_count | 0 | NOT_RECORDED | AVAILABLE |
| protected_l0_line_count | 119 | NOT_RECORDED | AVAILABLE |
| protected_l1_preserved_count | 119 | NOT_RECORDED | AVAILABLE |
| protected_l1_lost_count | 0 | NOT_RECORDED | AVAILABLE |
| l_plus_association_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_base_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_shunt_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_terminal_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| final_structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |

### PR020_REAL_CASE_STRUCTURE / JILIN / jilin-lt110

Declared source schema: `NOT_RECORDED`; package: `0.3.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| s1_bus_count | 1803 | NOT_RECORDED | AVAILABLE |
| s1_line_count | 847 | NOT_RECORDED | AVAILABLE |
| accepted_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| candidate_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | 0.01552967276760954 | NOT_RECORDED | AVAILABLE |
| candidate_compression_fraction | 0.01552967276760954 | NOT_RECORDED | AVAILABLE |
| accepted_line_count | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l0_source_line_count | 847 | NOT_RECORDED | AVAILABLE |
| l1_bus_count | 1775 | NOT_RECORDED | AVAILABLE |
| l1_line_count | 819 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_preserved_line_count | 819 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_absorbed_internal_count | 28 | NOT_RECORDED | AVAILABLE |
| l0_to_l1_aggregated_count | 0 | NOT_RECORDED | AVAILABLE |
| l1_derived_line_count | 0 | NOT_RECORDED | AVAILABLE |
| protected_l0_line_count | 119 | NOT_RECORDED | AVAILABLE |
| protected_l1_preserved_count | 119 | NOT_RECORDED | AVAILABLE |
| protected_l1_lost_count | 0 | NOT_RECORDED | AVAILABLE |
| l_plus_association_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_base_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_shunt_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| l_plus_terminal_preserved_count | 10 | NOT_RECORDED | AVAILABLE |
| final_structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / base

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | BASE | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| retained_voltage_layers_kv | [] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 0cf409cf27f343aea661c37ff48118a62315fe7812a792439e2a592c68d8abf5 | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.08840864440078586,"line":0.3755868544600939} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | f71e32ec0d7f60ec1840cf1a61b94fe4d8fc80a4e3ac2555f6947f6147da7883 | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 399 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 399 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 504 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_35

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_35 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 35.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 0b9f6b686aa8655d8df0028323394ccf6ff819252b18d1be3127c50d7b2e0de9 | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.0013097576948264572,"line":0.003129890453834116} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | bbcae61dbc11feed1346ff192f4d96b5b7e625f903cbe8b62390eed947410fc3 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | a3f74ac02ebe76425548bd021e3802755f54d8ff7657a835ce9d255293e6addf | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | CUT_37 | NOT_RECORDED | AVAILABLE |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 637 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 637 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 637 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_37

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_37 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 37.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 0b9f6b686aa8655d8df0028323394ccf6ff819252b18d1be3127c50d7b2e0de9 | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.0013097576948264572,"line":0.003129890453834116} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | bbcae61dbc11feed1346ff192f4d96b5b7e625f903cbe8b62390eed947410fc3 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | a3f74ac02ebe76425548bd021e3802755f54d8ff7657a835ce9d255293e6addf | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | CUT_35 | NOT_RECORDED | AVAILABLE |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 637 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 637 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 637 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_110

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_110 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 110.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 620dc3dba2b46e65ea7dd6cccb5b64d9b70897c9f284fa2527977829e77ea0fd | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.0013097576948264572,"line":0.003129890453834116} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | 1a4ee856ffc250cf19e3c758520c9a1eb230d54cf0f03068a81c273bc4bbe783 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | a3f74ac02ebe76425548bd021e3802755f54d8ff7657a835ce9d255293e6addf | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | CUT_115 | NOT_RECORDED | AVAILABLE |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 637 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 637 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 637 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_115

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_115 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 115.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 620dc3dba2b46e65ea7dd6cccb5b64d9b70897c9f284fa2527977829e77ea0fd | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.0013097576948264572,"line":0.003129890453834116} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | 1a4ee856ffc250cf19e3c758520c9a1eb230d54cf0f03068a81c273bc4bbe783 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | a3f74ac02ebe76425548bd021e3802755f54d8ff7657a835ce9d255293e6addf | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | CUT_110 | NOT_RECORDED | AVAILABLE |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 637 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 637 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 637 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_230

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_230 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 230.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [230.0,525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | d1e41e3787653b83050758f352f86d6847514b207818ad84a393c3886127840d | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.08644400785854617,"line":0.20657276995305165} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | 7ccd24d1a334df1d64fb81a0be124056fa69dd631da9ad424b40fa5076e88482 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | aceb280a8e1eb251cfd9f4157a07c13553f173b2ffdc2338987c8c4d603a62f1 | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1395 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 507 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1395 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 507 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1396 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 508 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR021_BEIJING_VOLTAGE_POLICY_MATRIX / BEIJING / cut_330

Declared source schema: `pr021-beijing-voltage-policy-summary/v1`; package: `0.4.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_330 | NOT_RECORDED | AVAILABLE |
| requested_cutoff_kv | 330.0 | NOT_RECORDED | AVAILABLE |
| retained_voltage_layers_kv | [525.0] | NOT_RECORDED | AVAILABLE |
| resolved_policy_fingerprint | 9bc79f4ffc72574e402701b965009b4b53ef2868f3ffcfe3d48dce0d457ee11a | NOT_RECORDED | AVAILABLE |
| final_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| accepted_compression_fraction | {"bus":0.08840864440078586,"line":0.3755868544600939} | NOT_RECORDED | AVAILABLE |
| retained_identity_set_digest | a119e67b1dad940386e8c6d59e9148ff2292f967da55c26029833d31811028a4 | NOT_RECORDED | AVAILABLE |
| mapping_semantic_sha256 | 60866e72d9f7df0cc076598c1fc71dcc4d9f0c7d6c0ab0fef0e17d0fb82f92e6 | NOT_RECORDED | AVAILABLE |
| equivalent_policy_ids | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.projected_transformer_rows | 1344 | NOT_RECORDED | AVAILABLE |
| source.generator | 56 | NOT_RECORDED | AVAILABLE |
| source.load | 219 | NOT_RECORDED | AVAILABLE |
| source.shunt_impedance | 135 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| source.storage_unit | 0 | NOT_RECORDED | AVAILABLE |
| source.store | 0 | NOT_RECORDED | AVAILABLE |
| actual_source_voltage_layers_kv | [0.32,0.6,0.69,0.8,1.0,1.14,10.5,13.8,15.8,16.0,18.0,20.0,27.5,37.0,66.0,115.0,230.0,525.0] | NOT_RECORDED | AVAILABLE |
| source_absent_voltage_layers_kv | [330,110,35] | NOT_RECORDED | AVAILABLE |
| candidate.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| candidate.line_count | 399 | NOT_RECORDED | AVAILABLE |
| candidate.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| accepted.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| accepted.line_count | 399 | NOT_RECORDED | AVAILABLE |
| accepted.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| l1.bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| l1.line_count | 504 | NOT_RECORDED | AVAILABLE |
| l1.compression_fraction | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / base

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | BASE | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 399 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.08840864440078586 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.3755868544600939 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 5.378045940839738e-05 | pu | AVAILABLE |
| vm_max_pu | 0.00019638027093504817 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051743 | MW | AVAILABLE |
| cutset_q_max_mvar | 1.1963330127292693 | Mvar | AVAILABLE |
| loss_relative_error | 0.00011097440734940648 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.36325083393603563 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_330

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_330 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1392 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 399 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.08840864440078586 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.3755868544600939 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 5.3780459403704873e-05 | pu | AVAILABLE |
| vm_max_pu | 0.0001963802709357143 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051743 | MW | AVAILABLE |
| cutset_q_max_mvar | 1.1963330126860683 | Mvar | AVAILABLE |
| loss_relative_error | 0.00011097440752881107 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.3478596662171185 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_230

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_230 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1395 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 507 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.08644400785854617 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.20657276995305163 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 5.581910875684765e-05 | pu | AVAILABLE |
| vm_max_pu | 0.0002270926943408469 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051686 | MW | AVAILABLE |
| cutset_q_max_mvar | 1.1963212442419433 | Mvar | AVAILABLE |
| loss_relative_error | 0.00011103421360790007 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.35545283276587725 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_115

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_115 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 637 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.003129890453834116 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 4.887905955009205e-06 | pu | AVAILABLE |
| vm_max_pu | 3.453532406427762e-05 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051686 | MW | AVAILABLE |
| cutset_q_max_mvar | 0.8391246221334967 | Mvar | AVAILABLE |
| loss_relative_error | 8.10517697481874e-05 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.4019273752346635 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_110

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_110 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 637 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.003129890453834116 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 4.887905955009205e-06 | pu | AVAILABLE |
| vm_max_pu | 3.453532406427762e-05 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051686 | MW | AVAILABLE |
| cutset_q_max_mvar | 0.8391246221334967 | Mvar | AVAILABLE |
| loss_relative_error | 8.10517697481874e-05 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.39898416586220264 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_37

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_37 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 637 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.003129890453834116 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 4.887905955009205e-06 | pu | AVAILABLE |
| vm_max_pu | 3.453532406427762e-05 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051686 | MW | AVAILABLE |
| cutset_q_max_mvar | 0.8391246221334967 | Mvar | AVAILABLE |
| loss_relative_error | 8.10517697481874e-05 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.38721399987116456 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR022_BEIJING_STATIC_SNAPSHOT_AC_PF / BEIJING / cut_35

Declared source schema: `pr022-schema-1`; package: `0.5.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| policy | CUT_35 | NOT_RECORDED | AVAILABLE |
| s4_bus_count | 1525 | NOT_RECORDED | AVAILABLE |
| s4_line_count | 637 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.0013097576948264572 | NOT_RECORDED | AVAILABLE |
| line_compression_fraction | 0.003129890453834116 | NOT_RECORDED | AVAILABLE |
| s4_pf_converged | True | NOT_RECORDED | AVAILABLE |
| vm_rmse_pu | 4.887905955009205e-06 | pu | AVAILABLE |
| vm_max_pu | 3.453532406427762e-05 | pu | AVAILABLE |
| cutset_p_max_mw | 7.347785465051686 | MW | AVAILABLE |
| cutset_q_max_mvar | 0.8391246221334967 | Mvar | AVAILABLE |
| loss_relative_error | 8.10517697481874e-05 | relative | AVAILABLE |
| post_fix_s4_pf_runtime_seconds_diagnostic_only | 0.38446758408099413 | s (diagnostic only) | AVAILABLE |
| structural_status | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID | NOT_RECORDED | AVAILABLE |
| ac_readiness_status | AC_MODEL_READY | NOT_RECORDED | AVAILABLE |
| pf_fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| source.bus_count | 1527 | NOT_RECORDED | AVAILABLE |
| source.line_count | 639 | NOT_RECORDED | AVAILABLE |

### PR025_STAGE2C_EXECUTION / BEIJING / initial-candidate

Declared source schema: `stage2c-execution/v1`; package: `0.8.0`.

| Observation | Value | Unit | Status |
| --- | --- | --- | --- |
| active_action_count | 125 | NOT_RECORDED | AVAILABLE |
| bus_compression_fraction | 0.08185985592665357 | NOT_RECORDED | AVAILABLE |
| branch_compression_fraction | 0.11598587997982854 | NOT_RECORDED | AVAILABLE |
| candidate_fingerprint | ad38f087c21873e784a38560f5f2cd58cf38239b05a9b6a478982425d49d9705 | NOT_RECORDED | AVAILABLE |
| fidelity_status | PF_FIDELITY_WITHIN_TOLERANCE | NOT_RECORDED | AVAILABLE |
| fidelity_metric_values | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| metrics_digest | a4bc6a9a084226bc1ea706386f0248575bee9969139a7a650f52396b9f780eaa | NOT_RECORDED | AVAILABLE |
| pf_status | PF_CONVERGED | NOT_RECORDED | AVAILABLE |
| pf_iterations | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| pf_final_mismatch | NOT_RECORDED | NOT_RECORDED | NOT_RECORDED |
| overall_admission | PASS | NOT_RECORDED | AVAILABLE |
| actual_derived_closure_digest | a3792678b1d7127cc03734a643c19029e91fd8ac05b7d9c86114365fa8c88066 | NOT_RECORDED | AVAILABLE |
| expected_derived_closure_digest | a3792678b1d7127cc03734a643c19029e91fd8ac05b7d9c86114365fa8c88066 | NOT_RECORDED | AVAILABLE |
| source.bus | 1527 | NOT_RECORDED | AVAILABLE |
| source.line | 639 | NOT_RECORDED | AVAILABLE |
| source.transformer | 1344 | NOT_RECORDED | AVAILABLE |
| source.link | 0 | NOT_RECORDED | AVAILABLE |
| projection_fidelity | NOT VALIDATED | NOT_RECORDED | AVAILABLE |
| physical_equivalence | NOT CLAIMED | NOT_RECORDED | AVAILABLE |
| experiment_outcome | ACCEPTED | NOT_RECORDED | AVAILABLE |
| terminal_reason | FIDELITY_SATISFIED | NOT_RECORDED | AVAILABLE |
