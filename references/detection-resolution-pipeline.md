# 风险发现、取证与关闭

本参考用于把审阅发现转成可执行问题。任何正式审阅都要区分“在哪里发现疑点”“用什么证明”“谁可以关闭”。

## 一、五步最短路径

1. **触发**：从正文、文件组、私有知识或人工语境中产生 `TriggerHit`。
2. **原子化**：把一条混合意见拆成能够独立补证、改写或接受的 `AtomicIssue`。
3. **路由**：分别标注文本可发现性 `T`、发现通道 `D`、风险 `R`、验证难度 `V` 和关闭路线 `C`。
4. **验证**：把候选来源经过准入检查后提升为 `EvidenceItem`，再执行明确的 `VerificationAction`。
5. **关闭**：只有结构化关闭条件满足后，交由指定人形成 `ClosureDecision`。

## 二、文本可发现性

| 代码 | 含义 | 允许的结论 |
|---|---|---|
| `T1 direct` | 正文内部即可确认矛盾、重复或不一致 | 可以描述为文本问题；外部事实仍需来源 |
| `T2 trigger_only` | 量词、动机、单因果或激进表述触发疑点 | 只能写待核问题，不得直接判错 |
| `T3 latent` | 需要交易结构、行业规模、政策时点、读者关系或专家经验才出现 | 必须记录外部知识来源与适用范围 |

不要把 `T3` 退化为敏感词列表。一个词是否有风险取决于主张、证据、受众和发表场景。

## 三、发现通道

- `D0 deterministic_text`：规则可确定的正文或格式检查。
- `D1 semantic_text`：模型形成范围、逻辑、语气或隐含前提候选。
- `D2 document_set`：题纲、来源、表格、批注和版本间的比对。
- `D3 private_knowledge`：获授权知识卡给出问题模板、机制线索或反例。
- `D4 context_or_human`：编辑、政策负责人、利益相关方或领域专家提出。

通道说明问题是如何进入队列的，不决定它是否成立。

## 四、关闭路线

| 路线 | 适用问题 | 最低关闭材料 |
|---|---|---|
| `C0 edit_only` | 纯表达、重复、专业语体 | 修改后的新草稿快照与重跑结果；旧快照自报修改不构成关闭 |
| `C1 direct_compare` | 名称、日期、原值、文内引用和版本 | 直接来源及精确 locator |
| `C2 reproduce` | 比例、公式、图表和派生数字 | 输入值、口径、公式和复算结果 |
| `C3 primary_web` | 当前事实、政策、市场记录或缺失原始来源 | 已打开的权威原始页面、日期和范围适配 |
| `C4 kb_then_corroborate` | 历史经验、机制线索、术语和反例 | 知识卡只作 lead，另需准入证据或人工判断 |
| `C5 context_review` | 标题、政府表意、受众和声誉 | 发表场景、利益相关方图和授权负责人决定 |
| `C6 domain_expert` | 交易结构、行业惯例、多因素机制 | 专家角色、理由、适用范围、替代解释和局限 |
| `C7 narrow_delete_hold` | 不能及时可靠验证 | 暂停发布的人工决定，或缩窄/删除后的新草稿快照与重跑结果 |

一项问题可按顺序使用多条路线。联网搜索只适合补一手事实或当前法源，不适合替代编辑判断和行业机制判断。

## 五、线索与证据分离

以下均是 `DiscoveryHit`，默认不是 `EvidenceItem`：

- 搜索结果或搜索摘要；
- 只有 URL、没有打开和定位的页面；
- 导师批注或会议发言；
- 知识卡命中；
- 专家一句没有依据、范围和局限的判断；
- 正文作者对自己的陈述。

每个 `DiscoveryHit` 至少记录唯一 `hit_id`、真实存在的 `issue_id`、发现 `channel`、`candidate_locator`、带时区的 `captured_at`、来源 `role` 和 `promotion_status`；空壳、错误类型或悬空问题引用均失败。候选来源通过以下检查后才可由审阅者晋升：审阅流程记录已查看原始来源、locator 完整、主体/地区/期间/定义/样本/单位匹配、时效合格、权限合格、来源角色已记录，并对关键结论考虑独立性与反证。

晋升后的每项 `EvidenceItem` 必须有非空 `fit_target_refs`，只绑定本台账实际存在的 `claim:<claim_id>` 或 `viewpoint:<viewpoint_id>`。没有明确目标或尚未晋升的候选继续留在 `discovery_hits`，不能先放进 `evidence_records`。公开网页证据 URL 还必须通过 HTTP(S)、主机、端口、转义和凭据的基本语法检查；任意主机上 `/search`、`/web` 或 `/s` 配常见查询参数的页面，以及其他已识别搜索服务结果页，仍只是发现线索。主张或观点引用的每一项证据都须包含自身目标；计算证据及递归外部输入只有绑定同一目标，才能把外部血缘用于该目标。`web_trace`、fit、目标绑定和来源角色是调用方运行记录；确定性校验只检查格式、引用和交叉约束，不读取浏览器历史，也不证明页面真实性、目标绑定合理或语义支持。URL 与搜索页规则是有限的机械下限，不穷尽页面角色。

用户提供的文件只有在 `supplied_inputs` 中以稳定 `input_id` 声明，并在 bundle 校验时通过 `--supplied-input ID=PATH` 读取真实字节重算 SHA-256 后，才能作为 `supplied_source` 候选。ID 必须是 1–128 位 ASCII，首位为字母或数字，后续仅含字母、数字、点、下划线或连字符；正反斜杠、冒号和其他路径语法均拒绝。字节一致只证明本次读取对象与声明哈希一致，不证明材料内容真实、完整或足以支持主张。

`reproducible_calculation` 必须把每个输入证据连接到实际参与公式、且对结果产生可检测影响的具名操作数，使用受限算术表达式，并与 Decimal 安全复算结果一致；嵌套计算还要对齐上游结果。单个审阅包最多 200 项正式证据，递归计算血缘最多 64 层；超过上限或出现环即失败关闭。以计算支撑正文派生主张时，还必须用 `calculation_evidence_ref` 指向主张实际采用的计算，并用 `derived_result` 将草稿锚点和原子主张中逐字出现的数值或百分数绑定到计算结果。校验器用多组确定性扰动识别 `x * 0 + 常数` 等装饰性血缘，但这不是完整符号依赖证明，取整、分段或局部不敏感公式可能需要改写或退出该证据路线。机器复算不证明输入值从来源摘录正确或输入事实真实。

关闭逻辑不得直接读取搜索摘要、URL 或知识卡 ID。

## 六、原子化要求

若一条批注同时涉及术语、事实、因果和语气，应生成多个问题并保留共同的 `parent_comment_id`。每个问题分别记录：

```text
atomic_issue_id
parent_trigger_ids
claim_ids
atomic_question
category
granularity
text_detectability
discovery_channels
risk_level
verification_level
closure_routes
required_evidence
required_reviewer
workflow_state
disposition
publication_effect
closure_condition
reopen_conditions
```

不要拆分不能独立改变证据、结论或处置的语法片段。

## 七、状态与关闭

```text
CANDIDATE → ATOMIZED → TRIAGED → ROUTED → DISCOVERING
→ VERIFYING → DECISION_READY → HUMAN_REVIEW → CLOSED/BLOCKED
```

`revised`、`deleted`、`narrowed` 和 `evidence_added` 是动作，不是流程终点。旧快照上的自报修改、删除或缩窄不能形成 `closed`；先保存修改后的新草稿快照，重算哈希和主张台账，再重新审阅。`partially_supported`、`contradicted`、`conflicting`、`stale`、`missing` 或 `not_verifiable` 描述的是验证结果，不代表当前文字已经安全；若原主张仍在当前审阅快照中，就必须改写、删除、缩窄或暂停。未解决的 `R2` 或 `R3` 不能因 `accepted_risk` 使文章成为 `ready`；未关闭 R2 必须标为发布前修改或阻断，未关闭 R3 必须标为阻断发布并使总体状态为 `hold`。

关闭记录必须能回答四件事：实际做了什么验证、用了哪些已准入证据、哪些关闭条件逐项成立、谁在什么时间作出决定。`VerificationAction` 和 `ClosureDecision` 都用 `issue_refs` 与 `evidence_refs` 连接问题和证据；`performed_at` 与 `reviewed_at` 必须使用带时区的 ISO 8601 时间，且任一所引动作不得晚于关闭决定。若所引证据直接或经计算血缘包含用户材料，动作和决定时间还都不得早于相应 `supplied_inputs.captured_at`。一个问题的关闭证据须由所引动作的证据并集完整覆盖，决定中的证据集合须与之相同。这些时间仍是调用方记录，机检只建立台账时序下限，不认证真实采集或执行过程。

关闭证据还必须逐项覆盖该问题关联的每条主张，而不是只命中其中一条：至少一项关闭证据须含对应 `claim:<id>`；该主张需要外部证据时，还须逐条存在同目标且范围匹配的外部来源或同目标外部血缘计算。问题若以 `derived` 关闭，全部关联主张都必须是 `derived`，且每条主张的 `calculation_evidence_ref` 都在关闭证据集合中。所有关闭谓词都必须为真；需要外部验证的问题不能用正文自证；`R2`/`R3` 必须记录 `human_reviewed: true`。这些时间、fit、目标绑定、谓词和人工复核标志仍是调用方声明，校验器不认证浏览行为、来源语义或真人身份。只有 `verified`、`supported` 或满足上述逐主张计算绑定的 `derived` 能成为关闭状态。

任何要求外部证据的主张，包括 supporting，在没有有效直接或派生路线时都必须记录未决原因和下一步，由至少一个尚未有效关闭的问题引用，并阻止 `ready`。问题风险较低不等于该外部事实已经得到支持。

## 八、发布影响与前台映射

`publication_effect` 决定问题是否进入用户报告：`revise_before_publish` 与 `block_publication` 必须映射，`advisory` 可以映射，`none` 不得映射。每个显示项由后台 `reader_binding.item_index` 指向，并用 `location_excerpt`、`problem_excerpt`、`reason_excerpt`、`action_excerpt` 分别交叉绑定前台“原文位置”、标题、“为什么重要”和“建议处理”。四个前台逐字出现处不得被有限词表中的明显否定前缀直接否定。多个问题映射到同一项时，每个问题都需记录实质性的合并理由。

前台零项时把“本稿没有仍需在发布前处理的问题。”作为独立完整行输出；作为否定、引语或长句子串出现不算。这不要求后台没有已关闭、提示性或无发布影响的问题。逐字摘录、有限否定词面检查、序号和报告哈希只能证明指定文本满足机械映射条件，不证明前后台语义等价。

主张或文件版本改变、证据过期/撤回/替代、权限撤销、出现冲突来源或动态事实越过有效期时，问题转为 `REOPENED → TRIAGED`。

## 九、批注到触发器

导师批注默认只形成私有 case note。晋升为通用触发器前必须写清：

- 抽象问题模板，而不是原句替换；
- 适用条件与排除条件；
- 推荐的 `T/D/R/V/C`；
- 正例、近似反例和盲测；
- 误报处理与退役条件。

一次性措辞偏好不进入永久规则。真实批注、人物、机构和内部事实不进入公开示例。
