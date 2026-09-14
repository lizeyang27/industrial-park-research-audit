# 输出契约

正式交付分成两层：默认先给普通读者一份可直接采取行动的中文报告，同时单独保存完整技术记录。面向用户的主报告必须遵循 [用户可读报告](user-facing-report.md)，不得被下列字段台账替代。

在 v1.2 默认的 [公开全证据审阅](public-full-evidence-workflow.md)中，完整技术台账是必需交付物，不因主报告篇幅有限而省略。主报告“发布前需要处理”只显示由 `publication_effect` 选出的零至八个事项：`revise_before_publish` 与 `block_publication` 必须映射到前台，`advisory` 可以映射，`none` 不得映射。零项时必须把固定句“本稿没有仍需在发布前处理的问题。”作为独立完整行输出；作为否定、引语或长句子串出现不算。这只是前台展示规则，不是发现上限；全部有效主张、问题、证据状态、验证动作和关闭条件仍须进入技术记录。其他显式选择的轻量模式可以缩减技术字段，但必须披露缩减范围，且不得冒充 `public_full_evidence`。

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
  "supplied_inputs": [
    {
      "input_id": "SUP-001",
      "sha256": "<exact supplied source bytes sha256>",
      "media_type": "application/pdf",
      "captured_at": "2026-09-14T08:30:00+08:00"
    }
  ],
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

命令行校验器使用拒绝重复对象键的 JSON 解析；同一对象出现两个同名键时返回 `LEDGER_DUPLICATE_KEY`，而不是接受 JSON 解析器常见的“后值覆盖前值”。非法 JSON、错误的对象/数组/字符串/布尔/整数类型和必填字段缺失均失败关闭，不能通过字符串化或默认空值被解释成合法记录。该保证适用于校验器读取的原始 JSON；若调用方先把数据解析成普通字典再直接调用 Python 函数，重复键信息已经丢失，不能宣称完成了原始 JSON 重复键检查。

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
- `search_count`：非负整数，由调用方记录本次检索次数；
- `opened_pages`：调用方声明为本次已打开的、去重后的 HTTP(S) 页面 URL；用于关闭问题或支撑新观点的网页必须在此出现。URL 必须通过基本语法检查，包括协议、主机、端口、百分号转义、控制/空白字符和内嵌凭据边界；
- `stop_reason`：非空文字，说明为什么停止本轮公开检索；
- `skip_reason`：仅离线时必填，说明是用户要求离线、网络不可用或其他明确原因。

每个已关闭问题仍通过 `evidence_records` 和 `closure_evidence_refs` 回查证据。`web_trace` 记录过程，不替代证据准入。bundle 校验器会检查网络状态、次数、页面 URL 和停止原因，强制要求用于关闭问题或支撑新观点的网页同时出现在 `opened_pages`，并具有 `acquisition: opened_page`、有效 URL、精确 locator 及主体、时间、单位、范围四项 fit。这里的 `opened_pages` 和四项 fit 都是调用方写入的运行记录；当前校验器不读取浏览器历史或签名回执，也不判断网页语义是否真的支持主张。正文内部证据、用户提供的材料和可复算结果可以按下文的本地证据类型关闭相应问题，无须伪造网页 URL。离线时 `opened_pages` 必须为空，网页证据不得被记录为本次离线运行取得。若宿主还能提供逐次查询和页面事件，可另加 `search_events` 与 `opened_page_events`，但最小校验器不证明完整事件时序。

校验器把任意主机上路径为 `/search`、`/web` 或 `/s` 且带 `q`、`query`、`keyword(s)`、`wd`、`text` 或 `p` 等常见查询参数的 URL 视为搜索结果页，也识别若干常见搜索服务及跳转页形态；命中规则的入口只能作为 `DiscoveryHit`。这是保守的机械下限，不是网页角色的完整分类器；URL 通过基本语法检查、或未命中搜索页规则，都不能证明它是真实原始来源，更不能证明页面内容支持目标主张。

### 用户提供材料回执

`supplied_inputs` 在完整模式中必须显式存在；没有用户提供的来源材料时使用空列表。每项包含唯一 `input_id`、真实文件字节的 SHA-256、非空 `media_type` 和带时区的 ISO 8601 `captured_at`。`input_id` 必须是 1–128 位 ASCII 标识，首位只能是字母或数字，后续只能是字母、数字、点、下划线或连字符；因此正反斜杠、冒号、盘符、URI 和其他路径语法均会被拒绝。技术台账只保存该 ID 与哈希，不保存本机路径。

仅在 JSON 中自报哈希不构成字节核验。每个已声明输入都必须在运行校验器时通过可重复参数绑定实际文件：

```powershell
python scripts\validate_review_bundle.py draft.md technical-ledger.json user-report.md `
  --supplied-input "SUP-001=C:\private\source.pdf" --pretty
```

校验器会重新读取参数右侧的真实字节并重算 SHA-256；缺少任一声明输入、哈希不符、重复 ID、读文件失败或传入未声明 ID 均失败。`evidence_records` 中的 `supplied_source.input_refs` 只能引用这些已完成字节核验的 `input_id`。任何 `VerificationAction` 或 `ClosureDecision` 直接引用该证据、或引用递归计算血缘中含该证据的结果时，其带时区时间不得早于对应 `captured_at`；动作仍不得晚于关闭决定。这项检查证明“本次校验读取的字节与声明哈希一致”并建立台账中的最低时间顺序，不证明文件内容真实、完整、合法取得、真实采集过程，或其中某段语义支持目标主张；这些仍由证据准入和人工复核处理。

`supplied_inputs`、`claim_register`、`discovery_hits`、`verification_actions`、`closure_decisions`、`evidence_records`、`issues` 与 `new_viewpoints` 在完整模式中必须显式存在，即使某类对象本次为空也使用空列表；缺少字段表示相应技术层被跳过，校验失败。它们的详细对象字段继续遵循下方台账契约。

### 公开 F-style 与正式 E/F

`public_full_evidence` 是公开可分发的 F-style 生产模式：它使用公开问题先验、框架先行、默认联网和两层输出，但不是 `IRA-ABCDEF-V1.1` 的正式 F 组。正式 E/F 必须另外满足冻结 `PriorQuestionManifest`、E/F 对应的 `ReadingFrameworkManifest`、F 与 D 一致的 `ExperienceSupportBundle`、草稿隔离、时间链和固定实验条件。缺少这些条件时：

- `runtime.mode` 仍写 `public_full_evidence`；
- 不把运行标成正式 E 或 F；
- 不声称私有知识已在读稿前产生因果增量；
- 可以描述为“公开 F-style 审阅”，但不能描述为“复刻导师”或“模型已学习私有知识”。

## 技术记录：总体结论

只使用以下三种状态：

- `ready`：没有未关闭的 `R3` 或 `R2`，所有声明需要外部证据的主张（包括 supporting）都有有效证据路线，也没有 `revise_before_publish` 或 `block_publication`；并不表示文章绝对正确。
- `revise`：存在可通过补证、缩窄范围或调整推理解决的重要问题，但没有任何 `block_publication`。
- `hold`：核心事实、关键因果、政策适用或高风险指控尚无足够依据；只要存在 `block_publication` 就必须使用此状态。

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
| `importance` | core、important 或 supporting |
| `requires_external_evidence` | 布尔值；事实、计算、预测，以及核心/重要推断必须为 true |
| `evidence_status` | supported、partially_supported、derived、contradicted、conflicting、stale、missing、not_verifiable |
| `evidence_refs` | 已准入证据 ID；有确定的 `evidence_status` 时必须填写 |
| `calculation_evidence_ref` | 仅 `derived` 必填；必须属于 `evidence_refs`，并指向一项通过校验的 `reproducible_calculation` |
| `derived_result` | 仅 `derived` 必填；必须是正文锚点和原子主张中逐字出现的数值或百分数字符串，并与所指计算结果的十进制数值及百分号标记一致 |
| `unresolved_reason` | 未取得有效证据时为什么仍未决；任何需要外部证据的主张无有效路线时必填，包括 supporting |
| `next_action` | 关闭上述未决主张的具体下一步；与 `unresolved_reason` 成对出现 |
| `hidden_assumptions` | 结论成立所依赖但原文未充分说明的条件 |
| `counterevidence` | 已知反例、替代解释或需要主动寻找的反证 |

不要为了显示分析深度而拆分没有独立意义的语法片段。数字、比较、因果、预测、动机判断和行动建议通常需要单独成项。

用于 v1.2 bundle 校验的每项主张至少包含 `claim_id`、草稿中的逐字 `source_anchor`、`source_locator`、可读的 `claim`、合法 `claim_type`、`scope`、`importance` 以及布尔型 `requires_external_evidence`。事实、计算、预测，以及核心/重要推断不得自行标成不需要外部证据。非空草稿不得提交空的 `claim_register`，且至少要有一个核心主张。每项被主张引用的证据都必须在 `fit_target_refs` 中显式包含 `claim:<claim_id>`。需要外部证据的主张不分重要性，必须二选一：以 `supported` 引用针对该主张且范围匹配的直接外部证据，或以 `derived` 引用针对该主张、带同一目标外部输入血缘且已通过安全复算的计算证据；否则同时记录非空的 `unresolved_reason` 和 `next_action`，关联至少一个尚未有效关闭的问题，并阻止 `ready`。`derived` 还必须用 `calculation_evidence_ref` 唯一指出实际采用的计算证据，并用 `derived_result` 把计算结果绑定到正文锚点和原子主张；非 `derived` 主张不得携带这两个字段。`partially_supported`、`contradicted`、`conflicting`、`stale`、`missing` 与 `not_verifiable` 对实质主张均是未完成状态，即使被错误标为“不需外部证据”也不能支持 `ready`。这保证“完整模式”不能用空台账、正文自证、未定向证据或占位对象静默退回基础审阅，但不要求为凑数量制造主张或搜索。

## 问题台账

| 字段 | 要求 |
|---|---|
| `issue_id` | 稳定且不重复，例如 `ISS-001` |
| `claim_refs` | 一个或多个实际存在的主张 ID；跨主张问题列出全部相关主张 |
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
| `requires_external_evidence` | 布尔值；说明该问题能否仅凭正文或本地复算关闭 |
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
| `reader_binding` | 可选前台映射；含 `item_index`、`location_excerpt`、`problem_excerpt`、`reason_excerpt`、`action_excerpt`。见下方“前后台绑定” |
| `reader_grouping_reason` | 多个后台问题映射到同一前台项时必填，解释为何可合并 |
| `closure_note` | 由人工填写的处理依据 |
| `reopen_conditions` | 主张变化、证据过期/撤回/替代、权限撤销、冲突来源或动态事实更新等重开条件 |

先列 `R3`，再列 `R2`，最后列 `R1`。不要让表达建议淹没事实和逻辑问题。同一段同时涉及政策适用与行业机制时拆成两个问题，分别设置验证等级。

`revised`、`deleted` 与 `narrowed` 只是旧快照上的自报编辑动作，不得直接视为 closed；`evidence_added` 也只有在证据、动作和关闭决定全链路通过时才可形成关闭。搜索结果、URL、批注、会议说法或知识卡命中不得直接填入可准入证据；详见 [风险发现、取证与关闭](detection-resolution-pipeline.md)。未解决的 `R2` 或 `R3` 即使记录 `accepted_risk`，总体状态也不能是 `ready`。

`prior_question_refs`、`support_item_refs` 和 `framework_node_refs` 是发现血缘，不是正确性证明。若一次发现来自正文而非事前输入，应如实保留为空；不能为了提高“框架利用率”而事后强行映射。

## 发现与证据台账

对 `V2`、`V3` 或需要外部材料的 `V1`，另行记录：

| 对象 | 最小字段 |
|---|---|
| `DiscoveryPlan` | issue_id、channel、purpose、authorization_ref、expected_source_type、stop_condition、cost_band、status |
| `DiscoveryHit` | hit_id、issue_id、channel、candidate_locator、captured_at、role、promotion_status |
| `EvidenceItem` | evidence_id、issuer、source_type、locator、captured_at、primary_or_secondary、directness、independence、freshness、scope_fit、rights_status、admissibility、limitations |
| `VerificationAction` | action_id、issue_refs、evidence_refs、action_type、result、performed_by、performed_at |
| `ClosureDecision` | closure_id、issue_refs、evidence_refs、predicate_results、disposition、resolution_action、reviewer_role、reviewed_at、residual_limitations、reopen_conditions、human_reviewed |

`DiscoveryHit` 与 `EvidenceItem` 必须是不同对象。只有打开原始来源、记录精确定位并完成范围、时效、权限和独立性检查后，候选材料才可晋升为证据。

用于 bundle 校验的每个 `discovery_hits` 对象必须包含唯一、非空的 `hit_id`，指向真实存在问题的 `issue_id`，以及非空 `channel`、`candidate_locator`、带时区 ISO 8601 `captured_at`、`role` 和 `promotion_status`。这些字段只记录候选如何被发现及是否晋升；它们不是证据，不能关闭问题。对象类型、字段类型或引用不匹配即失败，空壳对象不能通过。

用于 v1.2 bundle 校验的 `evidence_records` 是上述 `EvidenceItem` 的最小可机检投影。单个审阅包最多 200 项；超过上限整体失败，不能把被截断的尾部描述为已校验：

| 字段 | 要求 |
|---|---|
| `evidence_id` | 稳定且唯一 |
| `acquisition` | 只允许 `opened_page`、`supplied_source`、`reproducible_calculation` 或 `draft_internal`；search snippet、summary 或 result 只能作为发现线索 |
| `url` | 仅 `opened_page` 必填，且必须是本次 `web_trace.opened_pages` 中的有效 HTTP(S) 原始页面地址 |
| `input_refs` | 三类本地证据必填，用稳定引用指向用户提供材料、计算输入或草稿位置；不得用本机绝对路径或任何大小写形式的 `file:` URI 充当公开证据 |
| `input_evidence_refs` | `reproducible_calculation` 必填；回指本台账中真实存在的输入证据，每项都必须被至少一个实际参与公式的 `calculation_operands.evidence_ref` 覆盖，且至少有一项具备外部来源血缘 |
| `calculation_operands` | `reproducible_calculation` 必填；非空对象数组，每项含 `name`、不带千分位逗号的有限十进制数或百分数字符串 `value`、以及属于 `input_evidence_refs` 的 `evidence_ref`；每项都必须实际参与公式并对结果产生可检测影响 |
| `calculation_expression` | `reproducible_calculation` 必填；仅允许数值、括号、已声明操作数名、`+ - * / %`、受限整数幂、`abs` 和 `round`，不接受任意代码 |
| `calculation_result` | `reproducible_calculation` 必填；可解析的有限数值或百分数字符串，必须与 Decimal 安全复算结果一致 |
| `locator` | 能回到页面章节、附件页码、表格/单元格、公式输入或草稿段落的精确位置 |
| `fit_target_refs` | 必填且非空，显式声明本证据的四项 `fit` 适用于哪些对象；只允许 `claim:<claim_id>` 或 `viewpoint:<viewpoint_id>`。尚未晋升或没有明确目标的候选只保留在 `discovery_hits`，不得进入 `evidence_records` |
| `fit.entity` | 来源主体与主张主体是否匹配 |
| `fit.time` | 来源时点、有效期或统计期是否匹配 |
| `fit.unit` | 指标单位、分母和计算口径是否匹配 |
| `fit.scope` | 地域、样本、项目分期、对象和结论范围是否匹配 |

四项 `fit` 通常应为 `true`。某一维度对该证据确实不适用时，可以写 `not_applicable`，但必须在 `fit_explanation` 中逐维解释；不能用“不适用”绕过实质不匹配。四类 acquisition 都必须有 locator、完整 fit 和非空 `fit_target_refs`。每项主张引用的全部证据都必须包含对应 `claim:<id>`；每条新观点引用的全部证据都必须包含对应 `viewpoint:<id>`。可复算结果要针对某个目标继承外部血缘时，计算证据和递归链上的外部输入都必须绑定同一目标。目标引用必须指向本台账中实际存在的主张或观点，不能用一组泛化 fit 支持任意对象。尚无目标的候选留在发现台账，不能用“先存进证据池以后再说”的方式进入证据台账。主体、时间和范围必须由调用方明确记录为匹配，单位只能记录为匹配或确实不适用；四项全部写“不适用”不能支持发布。`opened_page.url` 必须通过基本 HTTP(S)、主机、端口、转义和凭据语法检查，并排除 `/search|/web|/s` 配常见查询参数及其他已识别搜索服务结果页；该规则既不穷尽搜索服务，也不证明通过者是真实原始来源。本地证据只证明其相应的内部一致性、用户所供材料字节与声明哈希一致或算术结果可由所填操作数复算，不能借此关闭仍需外部事实验证的部分，也不能证明 fit 声明、目标绑定的语义、操作数或来源内容本身真实。

安全复算还要求：每个操作数名是字母开头、最长 64 字符的 ASCII 标识符；所有声明操作数都实际参与公式，每个 `input_evidence_ref` 至少被一个实际参与公式的操作数覆盖，公式常量只能是数值字面量。递归计算血缘的机械深度上限为 64；超过上限、出现环或无法完成验证即失败关闭。为防止用 `x * 0 + 常数`、`x - x + 常数` 或 `x ** 0` 伪造外部血缘，校验器还会对每个操作数做多组确定性扰动，并要求结果出现可检测变化。操作数指向上游 `reproducible_calculation` 时，其数值和百分号语义必须与上游 `calculation_result` 一致。校验器解析受限 AST 并以 Decimal 复算，不使用 `eval`；它证明的是“声明公式、声明操作数与声明结果在允许的算术范围内一致，且这些操作数不是已识别的无影响装饰”。扰动检查不是完整的符号依赖证明；遇到取整、分段或在当前数值附近不敏感的合法公式时，可能需要改写计算表达或退出可复算证据路线。上述检查均不证明输入证据中的原值被正确摘录或这些原值真实。

用于 bundle 校验的每个 `issues` 对象至少包含 `issue_id`、一个或多个有效 `claim_refs`、`risk_level`、布尔型 `requires_external_evidence`、逐字 `source_anchor`、非空 `source_locator`、`problem`、`reason`、`suggested_action`、`workflow_state`、`verification_status`、`publication_effect` 和 `closure_evidence_refs`。问题不得把所连主张已经要求的外部证据降格为“不需要外部证据”。被标为已关闭或已有确定验证结论的问题必须引用存在且满足上述准入条件的证据。只有 `verified`、`supported` 或 `derived` 可以进入 `closed`；派生关闭还必须引用通过安全复算的计算证据。`partially_supported`、`contradicted`、`conflicting`、`stale`、`missing` 和 `not_verifiable` 表示当前快照仍需改写、删除、缩窄或暂停，不能直接关闭。

`workflow_state: closed` 还必须通过 `verification_action_refs` 和 `closure_decision_ref` 回指同一问题与同一组关闭证据。每个动作和关闭决定都使用 `issue_refs`、`evidence_refs`；动作的 `performed_at`、决定的 `reviewed_at` 必须是带时区的 ISO 8601 时间，且任一所引动作不得晚于关闭决定。若所引证据直接或经递归计算血缘使用 `supplied_input`，两类时间还都不得早于对应材料的 `captured_at`。一个问题列出的关闭证据必须由所引用动作的证据并集完整覆盖，关闭决定的证据集合必须与之完全相同。对问题连接的每一项主张，`closure_evidence_refs` 中都至少有一项证据用相应 `claim:<id>` 明确绑定；该主张要求外部证据时，还必须逐项存在针对同一主张且范围匹配的外部来源或带同一目标外部血缘的有效计算。若问题的 `verification_status` 是 `derived`，全部关联主张都必须同样是 `derived`，并且每项主张的 `calculation_evidence_ref` 都在关闭证据中。关闭决定的所有谓词都必须为 true，并至少包含 `evidence_admissible` 与 `claim_resolved`；外部问题再要求 `scope_fit_confirmed`，`R2`/`R3` 再要求 `human_review_completed` 且 `human_reviewed: true`。这些时间、谓词和人工复核标志是调用方声明，当前校验器不认证采集/执行过程、执行者或真人身份。

旧快照上的 `revised`、`deleted` 或 `narrowed` 只是调用方自报的编辑动作，不能作为 `closed` 决定；此类关闭会失败。应先保存修改后的新草稿快照，重算 `draft_sha256`、重新拆解主张并再次运行审阅，让已经删除或缩窄的文字不再出现在新快照中。当前关闭决定实际只接受以证据解决问题的 `evidence_added`，或经证据和适用谓词确认的 `false_positive`。仅写“已修改”“已删除”或“已找到来源”都不能关闭问题。

任何要求外部证据的主张，无论是 core、important 还是 supporting，都只有针对该主张的 `supported` 直接外部证据，或具备同一目标可复算外部输入血缘的 `derived`，才能支持 `ready`。没有有效路线时必须同时给出 `unresolved_reason` 与 `next_action`，并由至少一个未有效关闭的问题引用该主张；否则既报告台账错误，也阻止当前快照成为 `ready`。每个未有效关闭的 `R3` 问题必须使用 `block_publication`，并使总体状态为 `hold`；未有效关闭的 `R2` 只能使用 `revise_before_publish` 或 `block_publication`。`partially_supported`、`contradicted`、`conflicting`、`stale`、`missing` 和 `not_verifiable` 仍是未完成状态。非空草稿至少需要一个核心主张，且每项主张必须填写可读主张文本、原文定位、类型和范围，不能用空台账或占位对象冒充完整审阅。

### 前后台绑定

技术状态必须与用户报告严格对应：`ready` 对应的完整首句是“可以发布。”，`revise` 对应“修改后发布。”，`hold` 对应“暂缓发布。”；也接受等价的中文/英文感叹号或英文句点作为句末符号。规范短语前不得有否定、引号或条件前缀。校验器还会在“可以发布”后的正文中寻找一组常见的“不得发布、暂缓、撤回”等冲突短语；这是防止明显自相矛盾的机械边界，不是完整语义一致性证明。

主报告“发布前需要处理”中的每个三级标题块按出现顺序从 1 编号。后台问题通过 `reader_binding.item_index` 指向其中一块，并用四段逐字交叉锚点约束对应关系：`location_excerpt` 必须同时出现在该块的“原文位置”行和后台 `source_anchor` 或 `source_locator`，`problem_excerpt` 必须同时出现在该块标题和后台 `problem`，`reason_excerpt` 必须同时出现在该块的“为什么重要”行和后台 `reason`，`action_excerpt` 必须同时出现在该块的“建议处理”行和后台 `suggested_action`。四个摘录都必须非空且至少含四个非空白字符；前台出现处不得被校验器有限词表中的明显否定前缀直接否定。`revise_before_publish` 与 `block_publication` 必须映射，`advisory` 可选，`none` 不得映射；每个前台块至少有一个后台问题，多个问题合并到同一块时每项都要填写实质性的 `reader_grouping_reason`。这些逐字锚点、有限否定检查和整份 `report_sha256` 只能证明指定字节满足若干映射下限，不能证明前台自然语言与后台判断语义等价。

前台没有需发布前处理的事项时，不写三级标题，把“本稿没有仍需在发布前处理的问题。”作为独立完整行输出。这不要求后台 `issues` 必须为空；已关闭或仅 `none`/未映射 `advisory` 的技术记录仍可保留。前台新观点数量必须与 `new_viewpoints` 一致，每条以规范化后的完整三级标题块 SHA-256 写入 `reader_item_sha256` 做一一绑定。完整生产台账仍须保留本文件“问题台账”列出的其余字段，不能把最小可机检投影当作全部审阅记录。

## 新观点

`new_viewpoints` 必须是数组，允许零至两项。证据不足时保留空数组，并在用户报告中把“本稿暂不足以形成可靠的新观点。”作为独立完整行输出；不得为了显示专业度强行凑数。固定句作为否定、引语或长句子串出现不算。

每项至少包含：

| 字段 | 要求 |
|---|---|
| `viewpoint_id` | 稳定且唯一，例如 `VP-001`；供证据目标绑定，不进入普通读者报告 |
| `actor` | 将受到影响或作出选择的明确参与方 |
| `decision` | 原稿之外、可能被改变的具体决策 |
| `mechanism` | 新增变量、传导链、替代解释或情景分叉，不得只是原文换写 |
| `evidence_refs` | 至少一个本次 `evidence_records` 中存在且已准入的证据 ID；每项都须以 `viewpoint:<viewpoint_id>` 绑定本观点，其中至少一项是范围匹配的 `opened_page`/`supplied_source`，或带同一目标范围匹配外部血缘且已安全复算的 `reproducible_calculation` |
| `boundary` | 适用的地区、园区类型、产业、项目阶段、时间或其他范围 |
| `falsifier` | 能推翻或显著削弱该观点的可观察反例、阈值或未来数据 |
| `reader_item_sha256` | 对应前台完整三级标题块经去除首尾空白并统一 LF 后的 SHA-256；必须与前台一一对应 |

前台每条观点必须固定使用“适用对象”“决策问题”“可能机制”“适用边界”“反证条件”五个标签，每个标签值都要有实质内容。后台 `actor`、`decision`、`mechanism`、`boundary`、`falsifier` 的完整字段值必须分别逐字出现在对应标签值中，且该出现处不得被有限词表中的明显否定前缀直接否定；`reader_item_sha256` 继续绑定整块文字。后台完整 `mechanism` 字符串若逐字出现在草稿中，不能作为新观点通过。字段逐字出现、有限否定/复制检查和整块哈希只是保守的机械下限，不证明两层语义等价、观点得到语义支持或观点真正新颖。

`draft_internal`、公开/私有问题先验和知识卡不能单独支撑新观点。满足目标绑定和外部血缘只是最低结构要求；审阅者仍须检查该证据是否真正支持新增机制，并披露剩余不确定性。

上述命令行校验器从原始 JSON 读取时能够检查 JSON 可解析且无重复对象键，并继续检查字段类型与结构、字节哈希、指定逐字锚点、目标引用关系、允许的算术复算和一部分证据纪律；若调用方先解析成普通字典再直接调用 Python 函数，重复键已经无法恢复。它不能自动判断网页内容是否真实、用户材料内容是否真实、计算输入本身是否真实、`fit_target_refs` 是否在语义上合理、主张是否拆全、locator 是否语义准确、前后台文本是否语义等价，或模型是否遗漏了行业问题；这些仍须由审阅者和盲测评价。

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
