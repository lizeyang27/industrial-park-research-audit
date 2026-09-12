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
| `C0 edit_only` | 纯表达、重复、专业语体 | 修订锚点与编辑确认 |
| `C1 direct_compare` | 名称、日期、原值、文内引用和版本 | 直接来源及精确 locator |
| `C2 reproduce` | 比例、公式、图表和派生数字 | 输入值、口径、公式和复算结果 |
| `C3 primary_web` | 当前事实、政策、市场记录或缺失原始来源 | 已打开的权威原始页面、日期和范围适配 |
| `C4 kb_then_corroborate` | 历史经验、机制线索、术语和反例 | 知识卡只作 lead，另需准入证据或人工判断 |
| `C5 context_review` | 标题、政府表意、受众和声誉 | 发表场景、利益相关方图和授权负责人决定 |
| `C6 domain_expert` | 交易结构、行业惯例、多因素机制 | 专家角色、理由、适用范围、替代解释和局限 |
| `C7 narrow_delete_hold` | 不能及时可靠验证 | 缩窄、删除或暂停的人工决定 |

一项问题可按顺序使用多条路线。联网搜索只适合补一手事实或当前法源，不适合替代编辑判断和行业机制判断。

## 五、线索与证据分离

以下均是 `DiscoveryHit`，默认不是 `EvidenceItem`：

- 搜索结果或搜索摘要；
- 只有 URL、没有打开和定位的页面；
- 导师批注或会议发言；
- 知识卡命中；
- 专家一句没有依据、范围和局限的判断；
- 正文作者对自己的陈述。

候选来源通过以下检查后才可晋升：原始来源已打开、locator 完整、主体/地区/期间/定义/样本/单位匹配、时效合格、权限合格、来源角色已记录，并对关键结论考虑独立性与反证。

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

`revised` 和 `evidence_added` 是动作，不是流程终点。`unverifiable` 若仍保留主张，也不是关闭。未解决的 `R3` 不能因 `accepted_risk` 使文章成为 `ready`。

主张或文件版本改变、证据过期/撤回/替代、权限撤销、出现冲突来源或动态事实越过有效期时，问题转为 `REOPENED → TRIAGED`。

## 八、批注到触发器

导师批注默认只形成私有 case note。晋升为通用触发器前必须写清：

- 抽象问题模板，而不是原句替换；
- 适用条件与排除条件；
- 推荐的 `T/D/R/V/C`；
- 正例、近似反例和盲测；
- 误报处理与退役条件。

一次性措辞偏好不进入永久规则。真实批注、人物、机构和内部事实不进入公开示例。
