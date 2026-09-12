# 输出契约

本参考规定正式审校结果的最小字段。普通问答可以适当简化，但不得省略会改变结论的来源、范围、不确定性或待核事项。

## 总体结论

只使用以下三种状态：

- `ready`：未发现未关闭的 `R3` 或 `R2`；并不表示文章绝对正确。
- `revise`：存在可通过补证、缩窄范围或调整推理解决的重要问题。
- `hold`：核心事实、关键因果、政策适用或高风险指控尚无足够依据。

结论后说明审查范围，例如是否拿到原始来源、是否完成动态信息核验、是否只审查正文。

## 主张台账

每个可独立验证的主张使用一行：

| 字段 | 要求 |
|---|---|
| `claim_id` | 稳定且不重复，例如 `CLM-001` |
| `source_anchor` | 原稿段落、页码、表格、单元格或其他可回查位置 |
| `claim` | 不改变原意的最小主张 |
| `claim_type` | fact、calculation、inference、forecast、judgment、recommendation |
| `scope` | 主体、地区、期间、样本、口径及单位 |
| `evidence_status` | supported、partially_supported、derived、conflicting、stale、missing、not_verifiable |
| `source_locator` | 来源文件和页码、章节、表格或URL；没有则明确为空 |
| `hidden_assumptions` | 结论成立所依赖但原文未充分说明的条件 |
| `counterevidence` | 已知反例、替代解释或需要主动寻找的反证 |
| `issue_id` | 若无问题可为空 |

不要为了显示分析深度而拆分没有独立意义的语法片段。数字、比较、因果、预测、动机判断和行动建议通常需要单独成项。

## 问题台账

| 字段 | 要求 |
|---|---|
| `issue_id` | 稳定且不重复，例如 `ISS-001` |
| `claim_id` | 对应主张；跨主张问题可以列多个 |
| `category` | 术语、证据、范围、口径、比较、因果、政策、声誉、结构或表达 |
| `granularity` | `G0` 表面、`G1` 主张、`G2` 关系或 `G3` 篇章命题 |
| `text_detectability` | `T1 direct`、`T2 trigger_only` 或 `T3 latent`；说明只看正文能够知道到哪一步 |
| `discovery_channels` | `D0` 确定性正文、`D1` 语义正文、`D2` 文件组、`D3` 私有知识或 `D4` 语境/人工，可多选 |
| `discovery_origin` | `prior_knowledge`、`reading_framework`、`draft_text`、`document_set`、`web`、`expert` 或 `human_review`；必须与首次发现时间一致 |
| `prior_question_refs` | C/D/E/F 中实际参与该发现的 `prior_question_id`；非先验发现为空，不得事后借用 |
| `support_item_refs` | D/F 中实际用于校准来源角色、边界或核验路径的 `support_item_id`；它不是 `EvidenceItem`，不得填入证据字段 |
| `framework_node_refs` | E/F 中实际参与该发现的 framework node；框架外发现为空并标记 `framework_mapping_status: outside_framework` |
| `framework_mapping_status` | `covered`、`missing`、`conflicting`、`out_of_scope`、`outside_framework` 或 `not_applicable` |
| `risk_level` | `R1` 提示、`R2` 重要或 `R3` 阻断；表示潜在后果，不表示验证难度 |
| `verification_level` | `V1` 显性核验、`V2` 语境与政策核验或 `V3` 行业专家核验 |
| `level_basis` | 为什么分别落入该风险级与验证级；不得只写标签 |
| `problem` | 具体指出哪里不成立或尚未证明 |
| `reason` | 解释判断依据，不只复述原文 |
| `required_evidence` | 关闭问题需要的材料、计算或领域判断 |
| `required_reviewer` | 规则/数据复核、编辑/政策负责人或行业专家 |
| `can_auto_close` | 默认 false；仅确定性复算或完全匹配的直接来源可候选 true，最终仍保留人工状态 |
| `closure_routes` | `C0` 至 `C7` 的一个或多个关闭路线；不得把发现通道当关闭路线 |
| `suggested_action` | 补证、复算、缩窄、改为假设、删除、重构或交人工判断 |
| `closure_condition` | 什么结果足以关闭问题 |
| `workflow_state` | candidate、atomized、triaged、routed、discovering、verifying、decision_ready、human_review、closed、blocked 或 reopened |
| `disposition` | pending、revised、evidence_added、accepted_risk、false_positive、deleted、narrowed 或 unverifiable；这是处置，不等于关闭 |
| `publication_effect` | none、advisory、revise_before_publish 或 block_publication |
| `closure_note` | 由人工填写的处理依据 |
| `reopen_conditions` | 主张变化、证据过期/撤回/替代、权限撤销、冲突来源或动态事实更新等重开条件 |

先列 `R3`，再列 `R2`，最后列 `R1`。不要让表达建议淹没事实和逻辑问题。同一段同时涉及政策适用与行业机制时拆成两个问题，分别设置验证等级。

`revised` 和 `evidence_added` 只是处置动作，不得直接视为 closed。搜索结果、URL、批注、会议说法或知识卡命中不得直接填入可准入证据；详见 [风险发现、取证与关闭](detection-resolution-pipeline.md)。未解决的 `R3` 即使记录 `accepted_risk`，总体状态也不能是 `ready`。

`prior_question_refs`、`support_item_refs` 和 `framework_node_refs` 是发现血缘，不是正确性证明。若一次发现来自正文而非事前输入，应如实保留为空；不能为了提高“框架利用率”而事后强行映射。

## 发现与证据台账

对 `V2`、`V3` 或需要外部材料的 `V1`，另行记录：

| 对象 | 最小字段 |
|---|---|
| `DiscoveryPlan` | issue_id、channel、purpose、authorization_ref、expected_source_type、stop_condition、cost_band、status |
| `DiscoveryHit` | hit_id、issue_id、channel、candidate_locator、captured_at、role、promotion_status |
| `EvidenceItem` | evidence_id、issuer、source_type、locator、captured_at、primary_or_secondary、directness、independence、freshness、scope_fit、rights_status、admissibility、limitations |
| `VerificationAction` | action_id、action_type、method_version、input_evidence_ids、result、performed_by、performed_at、output_hash |
| `ClosureDecision` | closure_id、predicate_results、disposition、resolution_action、evidence_ids、reviewer_role、reviewed_at、residual_limitations、valid_until、reopen_conditions |

`DiscoveryHit` 与 `EvidenceItem` 必须是不同对象。只有打开原始来源、记录精确定位并完成范围、时效、权限和独立性检查后，候选材料才可晋升为证据。

## 分阶段验证决策

完成基础审阅后，必须给用户一个可停止的决策点：

| 字段 | 要求 |
|---|---|
| `completed_scope` | 已完成的本地审阅与 `V1` 核验范围 |
| `remaining_v2` | 尚需语境、利益相关方或当前政策核验的问题数与优先项 |
| `remaining_v3` | 尚需行业机制、知识库或专家判断的问题数与优先项 |
| `estimated_tokens` | 分别给出 `V1` 已完成、`V2` 增量、`V3` 增量和全量区间 |
| `estimate_assumptions` | 文档字符数、主张数、来源数、知识卡数量、是否现场搜索及其他假设 |
| `external_calls` | 预计检索或数据调用次数区间；没有则为 0 |
| `human_dependency` | 哪些问题即使继续消耗 token 也仍需编辑、政策或行业专家关闭 |
| `choices` | 停止并接受限制、只验证选定 `V2`、或升级选定 `V3` |

token 数只能作为规划区间，不得伪装成实际计量、金额报价或特定模型承诺。若宿主提供真实 usage，应将实际值与预测值分栏记录。

## 买方研究命题卡

正式重构至少包括：

1. 中性研究问题。
2. 一句话核心命题及适用范围。
3. 市场共识或当前基准；没有证据时写“尚未建立”。
4. 作者的不同判断及其与共识的差异。
5. 驱动变量、代理指标、方向、时滞和证据状态。
6. 对销量、价格、成本、利润、现金流、资本开支、资产价值或融资条件的传导链。
7. 可观察催化剂及时间窗口。
8. 基准、上行和下行情景；缺少数据时只给方向，不造数。
9. 可证伪条件。
10. 待取得数据和当前无法回答的问题。

## 选题与成稿一致性

若同时提供选题与成稿，增加以下字段：

| 字段 | 含义 |
|---|---|
| `original_question` | 选题真正要回答的问题 |
| `article_thesis` | 成稿实际提出的核心判断 |
| `preserved` | 被保留且有证据支持的内容 |
| `evidence_driven_changes` | 因核验或新资料而合理改变的内容 |
| `publication_driven_changes` | 因标题、篇幅或叙事而改变的内容 |
| `lost_uncertainty` | 从假设写成确定结论的部分 |
| `missing_variables` | 买方研究需要但成稿没有分析的变量 |
| `drift_assessment` | aligned、partially_drifted、materially_drifted |

## 语言要求

- 区分“原文称”“来源显示”“据此推断”和“尚待验证”。
- 给出最小可执行修改，不使用“加强逻辑”“补充数据”等空泛意见。
- 置信度使用低、中、高并说明原因；没有校准依据时不输出伪精确百分比。
- 引用应能让复核者回到原始位置，不能只写机构名称。
- 未经用户要求，不输出整篇重写稿。
