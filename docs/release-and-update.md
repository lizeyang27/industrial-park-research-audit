# 发布、更新与回滚

本文适用于 GitHub 仓库 `industrial-park-research-audit` 中可安装的 `$industry-research-audit` Skill。私有知识库、授权清单、真实输入和运行输出不随 Skill 发布。

## 版本边界

- `VERSION` 是公开 Skill 的产品版本，例如 `1.2.0`。
- Git Tag 使用同一版本并加 `v` 前缀，例如 `v1.2.0`。
- `CHANGELOG.md` 记录用户可见能力、兼容性和边界变化。
- Knowledge Card、任务信封、语料层和脚本方法中的 `schema_version` 或 `method_version` 独立演进。
- 私有语料快照有自己的版本和生命周期，不写入公开仓库，也不因更新 Skill 而被覆盖。
- `ExperienceSupportBundle`、`ReadingFrameworkManifest`、DraftReceipt 和真实评测输出是私有运行产物，不是新的永久语料层，也不随 Skill 升级覆盖。

追加文章时，不改写旧的冻结快照。私有 `corpus-manifest.json` 可同时声明 `snapshot_ids` 与 `current_snapshot_id`；校验器会逐个核验不可变快照，并允许 L0 记录关联其中任一已声明快照。同一逻辑文章的新正文应继续使用原 `article_id`，生成新的 `source_version_id`，并通过 `supersedes_source_version_id` 形成单链；不要覆盖旧版本，也不要把修订稿伪装成新文章。

## 环境与依赖

- 运行环境：Codex，以及 Python 3.10 或更高版本。
- 核心脚本：仅使用 Python 标准库。
- 可选运行依赖：`pypdf`，记录于 `requirements-optional.txt`，只在抽取 PDF 文本时需要。扫描件、图片和复杂版式仍需单独处理或人工复核。
- 开发验证依赖：`PyYAML` 与 `jsonschema`，记录于 `requirements-dev.txt`，分别用于运行 Codex Skill Creator 提供的 `quick_validate.py` 和校验公开 JSON Schema/夹具；普通 Skill 使用不依赖它们。

依赖应安装在隔离的虚拟环境中。未安装 `pypdf` 时，PDF 抽取会明确失败，不应静默跳过文件。

## 首次安装

1. 从可信仓库获取明确的 Release 或 Tag，不直接依赖未固定的分支状态。
2. 查看 `VERSION`、`CHANGELOG.md` 和 Release 校验值。
3. 将整个 `industry-research-audit` 目录复制到个人 Codex skills 目录。
4. 重启或刷新 Codex，使用一个完全合成的小案例确认 Skill 能被发现。

PowerShell 示例：

```powershell
$sourceSkill = Resolve-Path ".\industrial-park-research-audit"
$skillsRoot = Join-Path $env:USERPROFILE ".codex\skills"
$installedSkill = Join-Path $skillsRoot "industry-research-audit"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
if (Test-Path -LiteralPath $installedSkill) {
    throw "目标目录已存在；请使用更新流程，避免覆盖私有配置或未知文件。"
}
Copy-Item -Recurse -LiteralPath $sourceSkill -Destination $installedSkill
```

## 更新

已安装副本不会因为 GitHub 仓库出现新提交而自动变化。每次更新都应从明确 Tag 获取完整新版本，再进行可恢复替换。

1. 阅读从当前版本到目标版本的全部 changelog，确认 Python、schema 和私有接口兼容性。
2. 关闭正在使用该 Skill 的任务；不要在更新时修改私有知识库。
3. 将当前安装目录移动到 skills 目录之外的备份位置，并在备份名中写入旧版本。
4. 将目标版本复制到原安装路径，重新启动 Codex。
5. 用合成案例执行冒烟测试。确认成功后再恢复日常使用；备份至少保留到验证完成。

更新前必须确认三个绝对路径：当前安装目录、目标版本目录和备份目录。不要使用通配符、递归删除或会覆盖已有备份的命令。

## 回滚

出现以下任一情况时回滚：Skill 无法被发现、脚本启动失败、输出契约不兼容、私有知识卡接口拒绝旧数据，或合成冒烟测试出现重要行为退化。

1. 停止使用新版本，不删除失败副本和诊断信息。
2. 将失败副本移动到隔离目录。
3. 把更新前备份复制回标准安装路径。
4. 重启 Codex，重复同一组合成冒烟测试。
5. 在私有维护记录中保存失败版本、环境和现象；不要把真实输入或私有路径写入公开 issue。

回滚 Skill 不等于回滚私有知识库。若某次更新改变了 schema，应提供显式迁移与逆向兼容说明；没有可靠迁移时保持旧私有库只读，并使用兼容的旧 Skill。

## GitHub 发布闸门

正式 Tag 前依次完成：

1. 在隐私隔离的公开 Git 仓库中只保留公开白名单文件；无论该目录过去如何使用，都以当前 tracked tree、提交历史和发布包的实际检查结果为准，不作无法证明的 clean-room 声明。
2. 核对 `VERSION`、Tag 和 changelog 一致。
3. 运行 Skill Creator 快速校验、全部单元测试、公开预检和容量审计。
4. 使用仓库外的真实主体 denylist 再运行一次公开预检；denylist 本身不得提交。
5. 检查 tracked files、staged diff 和生成的 Release 压缩包；不使用全量暂存命令代替检查。
6. 先推送私有远端，由第二人复核后再公开并生成不可变 Tag。
7. 为 Release 归档记录校验值，并保留前一个可用 Tag 作为回滚点。

v1.1 还必须验证：C/D 引用同一 prior hash，D/F 引用同一支持快照，E/F 的框架创建时间早于草稿 receipt，且任何 `origin_trace` 都没有被标为目标文章事实证据。

v1.2 还必须验证：外部安装后的默认提示路由到 `public_full_evidence`；运行回执逐项绑定公开完整工作流、公开问题先验和产业园区 lens 的真实哈希；联网可用时保存实际打开页面的轨迹；完整技术台账没有被主报告的八项展示上限截断；零至两条新观点均引用已准入证据并带适用边界和证伪条件。

结构和脚本通过不代表模型判断已经获得总体准确率。发布页只能陈述实际完成的测试范围；独立盲测完成前，不得声称复刻特定专家、自动判断文章真假或已经生产部署。

## 永不随版本发布的内容

- 真实文章、题纲、Office/PDF/图片、截图、图表或可逆的正文片段。
- 人工批注、修订痕迹、会议记录、内部规则、生产输出和模型对话。
- 私有知识库、授权清单、真实主体 denylist、路径映射和语料运行日志。
- 人名、机构或账号标识、联系方式、本机路径、访问链接及其参数。
- 对真实材料仅做改名、改数字或删标识的伪合成案例。
