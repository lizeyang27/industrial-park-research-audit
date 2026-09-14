# 读稿前阅读框架

`PriorQuestionManifest` 是一组事前固定的原子检查问题；`ReadingFrameworkManifest` 则把同一批问题组织成研究问题和类型化的定义边界、变量、参与方决策、机制链、替代解释、预期证据与证伪节点。v1.1 用后者检验“知识是否先改变了阅读结构”，而不是宣称模型权重被训练或永久改变。当前 schema 保存的是节点集合及其先验/支持引用，不包含父子边、机制边或证据矩阵单元格。

## 普通单轮审阅的框架优先路径

公开 Skill 的日常使用不必伪装成实验。若 topic brief 和 draft 尚未暴露，先读取公开问题先验并冻结框架，再打开草稿；此时可记录 `runtime.precommit_possible: true` 以及 `framework_timing.status: strict_precommit`，但这仍不代表使用了私有 E/F 条件。若用户第一条消息已经贴出草稿，模型不可能把它“忘掉”。仍应先暂停逐句评价，用公开先验形成一个不带 verdict 的框架，再进行系统复核，并记录：

- `runtime.precommit_possible: false`；
- `framework_timing.status: retrospective_framework_first_pass`；
- `framework_timing.draft_seen_before_framework: true`；
- `framework_timing.causal_claim_allowed: false`；
- 框架用于提高覆盖与组织一致性，不用于证明严格的读稿前因果效果；
- 对框架之外的有效发现照常保留，对框架诱发的误报照常记录。

普通框架至少覆盖：谁在做什么决策、成本与收益由谁承担、项目处于哪个阶段、关键定义与口径、机制成立所需条件、至少一个替代解释、最可能改变结论的证据、反例或证伪条件。公开问题先验只能生成问题和查证方向，不能预填现实答案或替代目标事实的证据。

公开完整模式的框架完成后直接进入本地发现与普通网页核验；只有付费、登录、私有资料或真人专家资源需要另行授权。严格 E/F 评测仍遵循下方的输入隔离、冻结与哈希要求。

## 允许输入

生成 E 组框架时只能读取：

- 不含草稿内容的 `TaskEnvelope`；
- 读稿前存在的中性 topic brief；
- 与 C/D 共用且已经冻结的 `PriorQuestionManifest`；
- 本参考文件和公开输出契约。

F 在以上基础上可读取与 D 相同的 `ExperienceSupportBundle`。不得读取草稿、草稿来源包、修订稿、批注、标准答案或由测试稿派生的知识。

## 语义计划

模型先根据允许输入形成一个私有 framework plan，至少包含：

- 中性研究问题和决策对象；
- 基准假设、尚未确认的差异化命题与定义边界；
- 变量节点及各变量应观察的指标、单位、期间和比较基准；
- 参与方、可控制资源、决策权和约束；
- 机制链、替代解释、反例与失效条件；
- 预期证据类型、来源优先级与关闭条件；
- 必查问题、已知盲区和框架适用范围。

框架内容必须是问题、假设或证据要求，不能预写对草稿的 verdict。私有经验和支持摘要仍标记为 `eligible_as_evidence: false`。

## 冻结与时间链

```text
TaskEnvelope + topic brief
  -> PriorQuestionManifest
  -> optional ExperienceSupportBundle
  -> semantic framework plan
  -> frozen ReadingFrameworkManifest
  -> DraftReceipt
  -> claim / issue mapping
```

确定性脚本负责验证输入哈希、授权、字符预算和时间，计算 framework hash，并在框架冻结后才绑定草稿 SHA-256。框架必须满足：

- `draft_seen: false`；
- E 使用 `routing_source: task_metadata_topic_and_prior_only`，F 使用 `routing_source: task_metadata_topic_prior_and_support`；
- 创建时间不早于所有输入，也严格早于 DraftReceipt；
- E 的支持 sidecar 为空，F 的 sidecar hash 与 D 完全一致；
- 已冻结对象不能原地覆盖；草稿或输入变化时创建新版本。

若框架由草稿字段派生、创建时间倒置、任一输入 hash 不匹配、出现不允许的输入角色或预算超限，应 fail closed。不能把事后总结改写成事前框架。

## 审阅阶段

读入草稿后，将每个重要主张映射为 `covered`、`missing`、`conflicting` 或 `out_of_scope`，并记录对应 framework node 与发现来源。框架不是发现上限；审阅者仍须记录框架之外的有效问题，以便识别知识盲区和框架诱发误报。

E/F 的主要增量不应只用“多发现几条”衡量，还应比较变量覆盖、替代解释、证伪条件、命题保真、框架外发现、框架诱发误报、token 与人工复核成本。
