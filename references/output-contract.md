# 输出契约

正式交付分成两层：默认先给普通读者一份可直接采取行动的中文报告，同时单独保存完整技术记录。面向用户的主报告必须遵循 [用户可读报告](user-facing-report.md)，不得被下列字段台账替代。

在 v1.2 默认的 [公开全证据审阅](public-full-evidence-workflow.md)中，完整技术台账是必需交付物，不因主报告篇幅有限而省略。主报告“发布前需要处理”的零至八项只是前台展示上限，不是发现上限；全部有效主张、问题、证据状态、验证动作和关闭条件仍须进入技术记录。其他显式选择的轻量模式可以缩减技术字段，但必须披露缩减范围，且不得冒充 `public_full_evidence`。

本文件以下内容规定的是技术记录的最小字段。`ready`、`revise`、`hold`，以及 `R`/`V`/`T`/`D`/`C`/`G` 编码、对象 ID 和机器字段只能出现在技术记录或用户明确要求的技术附录中。普通用户报告使用“可以发布”“修改后发布”“暂缓发布”和自然语言问题说明，同时仍须保留会改变结论的来源、范围、不确定性与待核事项。

## v1.2 审阅包 envelope

`scripts/validate_review_bundle.py` 接受原始草稿字节、UTF-8 JSON 技术台账和原始用户报告字节。用于该校验器的最小 envelope 为：

```json
{
  "draft_sha256": "<exact draft bytes sha256>",
  "report_sha256": "<exact report bytes sha256>",
  "runtime": {
    "skill_version": "<VERSION contents>",
    "commit": null,
    "source_ref": "v1.2.0 or installation URL/ref",
    "commit_unavailable_reason": "archive install has no reliable git metadata",
    "mode": "public_full_evidence",
    "precommit_possible": false,
    "loaded_resources": [
      {"path": "SKILL.md", "sha256": "<installed bytes sha256>"},
      {"path": "VERSION", "sha256": "<installed bytes sha256>"},
      {"path": "references/public-full-evidence-workflow.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/public-industrial-park-priors.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/industrial-park-lens.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/verification-routing.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/detection-resolution-pipeline.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/output-contract.md", "sha256": "<installed bytes sha256>"},
      {"path": "references/user-facing-report.md", "sha256": "<installed bytes sha256>"}
    ]
  },
  "framework_timing": {
    "status": "retrospective_framework_first_pass",
    "draft_seen_before_framework": true,
    "causal_claim_allowed": false,
    "framework_artifact_ref": null
  },
  "web_trace": {
    "network_available": true,
    "search_count": 0,
    "opened_pages": [],
    "stop_reason": "core claims statused and remaining routes recorded"
  },
  "publication_status": "ready|revise|hold",
  "claim_register": [],
  "discovery_hits": [],
  "verification_actions": [],
  "closure_decisions": [],
  "evidence_records": [],
  "issues": [],
  "new_viewpoints": []
}
```

上例只展示顶层结构；非空草稿不得沿用空的主张台账。聊天粘贴内容先按 UTF-8 无 BOM、LF 换行生成审阅快照，且不改变 Unicode 规范化、标点、空白或正文措辞；Office/PDF 等二进制输入则使用有转换说明的文本快照。所有锚点和 `draft_sha256` 都绑定这份实际审阅快照。

### 运行回执

`runtime` 用于记录并机检本次结果所声明的公开完整模式，降低安装后静默退回基础模式而不披露的风险：

| 字段 | 要求 |
|---|---|
| `skill_version` | 必须与已安装仓库的 `VERSION` 内容完全一致 |
| `commit` | 能取得时记录本次安装来源的 7–64 位十六进制提交标识；不能取得时必须为 `null`，并同时填写 `source_ref` 与 `commit_unavailable_reason`，不得编造哈希 |
| `source_ref` | `commit` 不可得时必填，记录安装所用的 Tag、Release、GitHub URL 或其他可回查来源；有 commit 时可选 |
| `commit_unavailable_reason` | `commit` 不可得时必填，解释复制包、下载归档等为什么没有可靠 Git 元数据；有 commit 时可选 |
| `mode` | 默认公开完整模式固定为 `public_full_evidence` |
| `precommit_possible` | 布尔值；只说明本次是否可能满足严格读稿前条件，不等于已经证明因果效果 |
| `loaded_resources` | 调用方记录为已加入运行上下文的仓库相对路径及其原始字节 SHA-256；不得只声明“已安装” |

`public_full_evidence` 至少加载并逐项校验以下九份资源的本地真实哈希：

- `SKILL.md`；
- `VERSION`；
- `references/public-full-evidence-workflow.md`；
- `references/public-industrial-park-priors.md`；
- `references/industrial-park-lens.md`；
- `references/verification-routing.md`；
- `references/detection-resolution-pipeline.md`；
- `references/output-contract.md`；
- `references/user-facing-report.md`。

路径缺失、哈希不匹配、版本不一致或模式不是 `public_full_evidence` 时，不得把结果描述为 v1.2 默认完整运行。校验器只检查提交标识的格式；取得不到可靠 Git 元数据时，宁可记录来源与原因也不得伪造 commit。资源哈希只能证明声明的对象与当前已安装文件一致，也不能证明模型确实阅读或正确执行了其中判断。需要更强运行证明时，应由宿主记录不可变调用上下文或签名回执。

### 草稿与报告绑定

`draft_sha256` 和 `report_sha256` 均按原始文件字节计算。校验器同时要求问题的 `source_anchor` 是草稿中的逐字子串；概括性意见不得加引号冒充原文。草稿或报告任一字节改变后，都必须重新生成相应哈希与审阅包，不能沿用旧验证结果。

### 框架时序

`framework_timing` 至少记录：

- `status`：`strict_precommit` 或 `retrospective_framework_first_pass`；
- `draft_seen_before_framework`：形成框架前草稿是否已经进入参与上下文；
- `causal_claim_allowed`：能否声称知识或框架在读稿前造成了效果；
- `framework_artifact_ref`：若存在冻结框架，引用其私有审计对象；公开报告不披露本地路径或私有 ID。

当草稿已经出现在当前对话、提示或参与生成框架的上下文中时，必须使用 `retrospective_framework_first_pass`、`draft_seen_before_framework: true`、`causal_claim_allowed: false`，并与 `runtime.precommit_possible: false` 保持一致。`validate_review_bundle.py` 会检查这些字段的类型、允许值和交叉一致性；它仍不能独立证明真实时间链或上下文隔离。需要正式证明时，使用 [读稿前阅读框架](reading-framework-workflow.md)的哈希、时间和隔离校验。

### 联网轨迹

若宿主可联网且用户没有明确要求离线，公开网页核验是默认同轮完成的动作，不在 V1 后再次暂停。`web_trace` 的机检最小字段为：

- `network_available`：布尔值，说明本次执行器是否提供网页访问；
- `search_count`：非负整数，记录实际检索次数；
- `opened_pages`：去重后的 HTTP(S) 页面 URL；用于关闭问题或支撑新观点的网页必须在此出现；
- `stop_reason`：非空文字，说明为什么停止本轮公开检索；
- `skip_reason`：仅离线时必填，说明是用户要求离线、网络不可用或其他明确原因。

每个已关闭问题仍通过 `evidence_records` 和 `closure_evidence_refs` 回查证据。`web_trace` 记录过程，不替代证据准入。bundle 校验器会检查网络状态、次数、页面 URL 和停止原因，强制要求用于关闭问题或支撑新观点的网页同时出现在 `opened_pages`，并具有 `acquisition: opened_page`、有效 URL、精确 locator 及主体、时间、单位、范围四项 fit。正文内部证据、用户提供的材料和可复算结果可以按下文的本地证据类型关闭相应问题，无须伪造网页 URL。离线时 `opened_pages` 必须为空，网页证据不得被记录为本次离线运行取得。若宿主还能提供逐次查询和页面事件，可另加 `search_events` 与 `opened_page_events`，但最小校验器不证明完整事件时序。

`claim_register`、`discovery_hits`、`verification_actions` 与 `closure_decisions` 在完整模式中必须显式存在，即使某类对象本次为空也使用空列表；缺少字段表示相应技术层被跳过，校验失败。它们的详细对象字段继续遵循下方台账契约。

### 公开 F-style 与正式 E/F

`public_full_evidence` 是公开可分发的 F-style 生产模式：它使用公开问题先验、框架先行、默认联网和两层输出，但不是 `IRA-ABCDEF-V1.1` 的正式 F 组。正式 E/F 必须另外满足冻结 `PriorQuestionManifest`、E/F 对应的 `ReadingFrameworkManifest`、F 与 D 一致的 `ExperienceSupportBundle`、草稿隔离、时间链和固定实验条件。缺少这些条件时：

- `runtime.mode` 仍写 `public_full_evidence`；
- 不把运行标成正式 E 或 F；
- 不声称私有知识已在读稿前产生因果增量；
- 可以描述为“公开 F-style 审阅”，但不能描述为“复刻导师”或“模型已学习私有知识”。

## 技术记录：总体结论

只使用以下三种状态：

- `ready`：没有未关闭的 `R3` 或 `R2`，也没有仍缺证据的核心/重要外部主张；并不表示文章绝对正确。
- `revise`：存在可通过补证、缩窄范围或调整推理解决的重要问题。
- `hold`：核心事实、关键因果、政策适用或高风险指控尚无足够依据。

结论后说明审查范围，例如是否拿到原始来源、是否完成动态信息核验、是否只审查正文。

## 主张台账

每个可独立验证的主张使用一行：

| 字段 | 要求 |
|---|---|
| `claim_id` | 稳定且不重复，例如 `CLM-001` |
| `source_anchor` | 从审阅快照中逐字复制的最短充分文本；必须是草稿字符串的真实子串 |
| `source_locator` | 原稿段落、页码、表格、单元格或其他可回查位置；与逐字锚点分开 |
| `claim` | 不改变原意的最小主张 |
| `claim_type` | fact、calculation、inference、forecast、judgment、recommendation |
| `scope` | 主体、地区、期间、样本、口径及单位 |
| `evidence_status` | supported、partially_supported、derived、contradicted、conflicting、stale、missing、not_verifiable |
| `evidence_refs` | 已准入证据 ID；有确定的 `evidence_status` 时必须填写 |
| `unresolved_reason` | 未取得证据时为什么仍未决；核心/重要外部主张无证据时必填 |
| `next_action` | 关闭上述未决主张的具体下一步；与 `unresolved_reason` 成对出现 |
| `hidden_assumptions` | 结论成立所依赖但原文未充分说明的条件 |
| `counterevidence` | 已知反例、替代解释或需要主动寻找的反证 |
| `issue_id` | 若无问题可为空 |

不要为了显示分析深度而拆分没有独立意义的语法片段。数字、比较、因果、预测、动机判断和行动建议通常需要单独成项。

用于 v1.2 bundle 校验的每项主张至少包含 `claim_id`、草稿中的逐字 `source_anchor`、`importance`（`core`、`important` 或 `supporting`）以及 `requires_external_evidence`。非空草稿不得提交空的 `claim_register`。核心或重要外部主张必须二选一：引用已准入证据并记录 `evidence_status`，或同时记录非空的 `unresolved_reason` 和 `next_action`。这保证“完整模式”不能用空台账静默退回基础审阅，但不要求为凑数量制造主张或搜索。

## 问题台账

| 字段 | 要求 |
|---|---|
| `issue_id` | 稳定且不重复，例如 `ISS-001` |
| `claim_id` | 对应主张；跨主张问题可以列多个 |
| `source_anchor` | 从审阅快照中逐字复制的最短充分文本；机器校验据此绑定当前草稿 |
| `source_locator` | 页码、段落、表格、单元格或其他便于人工回查的位置 |
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
| `verification_status` | pending、unverified、verified、supported、partially_supported、derived、contradicted、conflicting、stale、missing 或 not_verifiable |
| `closure_evidence_refs` | 支撑验证结果或关闭决定的已准入证据 ID；未决时可为空 |
| `verification_action_refs` | `closed` 时必填，回指实际执行且引用同一问题/证据的验证动作 |
| `closure_decision_ref` | `closed` 时必填，回指引用同一问题/证据的关闭决定 |
| `disposition` | pending、revised、evidence_added、accepted_risk、false_positive、deleted、narrowed 或 unverifiable；这是处置，不等于关闭 |
| `publication_effect` | none、advisory、revise_before_publish 或 block_publication |
| `closure_note` | 由人工填写的处理依据 |
| `reopen_conditions` | 主张变化、证据过期/撤回/替代、权限撤销、冲突来源或动态事实更新等重开条件 |

先列 `R3`，再列 `R2`，最后列 `R1`。不要让表达建议淹没事实和逻辑问题。同一段同时涉及政策适用与行业机制时拆成两个问题，分别设置验证等级。

`revised` 和 `evidence_added` 只是处置动作，不得直接视为 closed。搜索结果、URL、批注、会议说法或知识卡命中不得直接填入可准入证据；详见 [风险发现、取证与关闭](detection-resolution-pipeline.md)。未解决的 `R2` 或 `R3` 即使记录 `accepted_risk`，总体状态也不能是 `ready`。

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

用于 v1.2 bundle 校验的 `evidence_records` 是上述 `EvidenceItem` 的最小可机检投影：

| 字段 | 要求 |
|---|---|
| `evidence_id` | 稳定且唯一 |
| `acquisition` | 只允许 `opened_page`、`supplied_source`、`reproducible_calculation` 或 `draft_internal`；search snippet、summary 或 result 只能作为发现线索 |
| `url` | 仅 `opened_page` 必填，且必须是本次 `web_trace.opened_pages` 中的有效 HTTP(S) 原始页面地址 |
| `input_refs` | 三类本地证据必填，用稳定引用指向用户提供材料、计算输入或草稿位置；不得用本机绝对路径充当公开证据 |
| `locator` | 能回到页面章节、附件页码、表格/单元格、公式输入或草稿段落的精确位置 |
| `fit.entity` | 来源主体与主张主体是否匹配 |
| `fit.time` | 来源时点、有效期或统计期是否匹配 |
| `fit.unit` | 指标单位、分母和计算口径是否匹配 |
| `fit.scope` | 地域、样本、项目分期、对象和结论范围是否匹配 |

四项 `fit` 通常应为 `true`。某一维度对该证据确实不适用时，可以写 `not_applicable`，但必须在 `fit_explanation` 中逐维解释；不能用“不适用”绕过实质不匹配。四类 acquisition 都必须有 locator 和完整 fit。本地证据只证明其相应的内部一致性、用户所供材料内容或可复算结果，不能借此关闭仍需外部事实验证的部分。

用于 bundle 校验的每个 `issues` 对象至少包含 `issue_id`、`risk_level`、逐字 `source_anchor`、`workflow_state`、`verification_status` 和 `closure_evidence_refs`。被标为已关闭或已有确定验证结论的问题必须引用存在且满足上述准入条件的证据。`workflow_state: closed` 还必须通过 `verification_action_refs` 和 `closure_decision_ref` 回指同一问题、同一组关闭证据；仅写“已修改”或“已找到来源”不能关闭问题。存在未关闭的 `R2` 或 `R3` 时，`publication_status` 不得为 `ready`。技术状态必须与用户报告结论严格对应：`ready` 对应“可以发布”，`revise` 对应“修改后发布”，`hold` 对应“暂缓发布”。完整生产台账仍须保留本文件“问题台账”列出的其余字段，不能把最小可机检投影当作全部审阅记录。

## 新观点

`new_viewpoints` 必须是数组，允许零至两项。证据不足时保留空数组，并在用户报告中说明本次没有形成可靠的新观点；不得为了显示专业度强行凑数。

每项至少包含：

| 字段 | 要求 |
|---|---|
| `actor` | 将受到影响或作出选择的明确参与方 |
| `decision` | 原稿之外、可能被改变的具体决策 |
| `mechanism` | 新增变量、传导链、替代解释或情景分叉，不得只是原文换写 |
| `evidence_refs` | 至少一个本次 `evidence_records` 中存在的证据 ID；公开/私有问题先验不能充当证据 |
| `boundary` | 适用的地区、园区类型、产业、项目阶段、时间或其他范围 |
| `falsifier` | 能推翻或显著削弱该观点的可观察反例、阈值或未来数据 |

新观点引用存在的证据只是最低结构要求；审阅者仍须检查该证据是否真正支持新增机制，并披露剩余不确定性。

## 分阶段验证决策

在默认 `public_full_evidence` 模式中，先完成本地审阅和普通公开网页核验，再给用户一个可停止的决策点。不得把本可由当前公开网页完成的 V2 核验全部推迟成“如需继续”；决策点主要覆盖剩余付费/登录态/私有材料、现场数据和专家判断。只有用户明确选择离线或分阶段交付时，才可在公开核验前停止，并必须披露这一范围限制。

| 字段 | 要求 |
|---|---|
| `completed_scope` | 已完成的本地审阅、普通公开网页核验和 `V1`–`V3` 中实际关闭的范围 |
| `remaining_v2` | 公开检索后仍需额外语境、利益相关方、受限政策来源或人工裁决的问题数与优先项 |
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
