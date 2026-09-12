# 私有经验来源与支持数据

私有经验不是一段可以直接当作事实引用的“答案”。v1.1 将经验的形成来源、复现材料、反证与独立核验分开记录，使审阅者既能使用经验提出问题，也能知道这条经验的证据上限。

## 对象分层

```text
SourceAsset -> SourceSpan -> ExperienceEpisode -> KnowledgeCard
                                             -> SupportAssessment
                                             -> ExperienceSupportBundle
```

- `SourceAsset` 是不可变来源版本，至少有来源类型、SHA-256、权利和生命周期。
- `SourceSpan` 是可回查的位置，如批注范围、段落、表格单元或字符区间。定位尚未人工确认时必须标记 `pending`。
- `ExperienceEpisode` 记录一次“原主张—审改意见—修订结果”或一次会议观察。缺少某一环节时保留空缺，不推断结果。
- `KnowledgeCard` 只保存可复用的检查问题、适用条件、排除条件和证伪方式。
- `SupportAssessment` 逐项记录一张卡的来源、支持、反例、独立性和准入状态。
- `ExperienceSupportBundle` 是某次任务的最小私有 sidecar；它必须引用已经冻结的 `PriorQuestionManifest`，不能改变问题集合或排序。

原始正文、人物、文件名和本地路径只留在授权的私有存储中。公开 Skill 只包含接口、校验器和全合成示例。

## 四种来源角色

| 角色 | 能说明什么 | 默认能否证明目标文章事实 |
|---|---|---|
| `origin_trace` | 经验由哪次批注、会议或复盘形成 | 否 |
| `corroboration` | 另一材料对机制或边界提供支持 | 否；完成证据准入后才可能另建 EvidenceItem |
| `counterevidence` | 存在反例、冲突或更窄的适用范围 | 否，但必须参与校准 |
| `boundary_example` | 展示经验何时适用或不适用 | 否，通常只是案例线索 |

同一文件、同一版本链、同一机构转载、共享底层数据或同一访谈不能重复计作独立支持。独立性不明时用 `unknown`，不要猜测。

## 强制边界

1. 导师批注只证明该审改意见曾出现；会议实录只证明该说法曾被表达。
2. 来源可追溯不等于行业有效性得到验证。分别记录抽象准确度、内部复现、外部有效性和本次触发价值。
3. `origin_trace_only` 不得自动提高 Knowledge Card 的置信度，也不得改写为 `verified` 或 `corroborated`。
4. 支持项只有在原始来源已打开、定位可复现、范围与时点匹配、权利允许且独立性完成评估后，才可作为外部事实的证据候选；仍须进入单独的 EvidenceItem 流程。
5. 反证、冲突、过期、撤权和来源版本改变必须触发降级、重建或人工复核，不能静默丢弃。
6. 标准输出只报告数量、状态和稳定错误码，不回显来源 ID、locator、路径或私有转述。

## D/F 的运行方式

D 与 C 使用字节完全相同的 `PriorQuestionManifest`。D 只多读一个 hash-bound 支持 sidecar，用于理解问题的来历、适用边界和仍缺的验证。F 与 E 同理。

当前 v1.1.0 的 `ExperienceSupportBundle` 是保守的第一步：它包括问题 ID、经验卡 ID、来源 ID/类型/声明哈希、locator、转化链、声明的独立来源线索、授权快照、内容预算和 bundle hash。未提供 `--source-root` 时，来源状态只能是 `manifest_declared_not_verified`；提供后会逐个读取清单中的本地来源并复算 SHA-256，全部通过才是 `byte_hash_verified`。这仍不复现 locator，也不判断来源内容正确。即使存在独立来源 ID，也只标为 `declared_not_verified`，不会成为事实证据。

更完整的 `SourceSpan -> ExperienceEpisode -> SupportAssessment` 台账仍是后续私有层工作：它应增加支持、反证和边界关系、独立性分组、locator 人工复核与证据准入状态。不得把当前 bundle 描述成已经完成这些人工裁决。

若没有独立线索，保留 `origin_trace_only` 和 `none_declared`；有线索但尚未完成独立核验时，使用 `origin_trace_with_declared_independent_leads` 和 `declared_not_verified`。零独立支持是有效结果，不是需要补写的空白。
