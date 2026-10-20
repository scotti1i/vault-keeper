# vault-keeper 约定

## 模型：三层 + 投影

```
raw（不可变）  →  wiki（agent 写、人审）  ←  schema（人定：CLAUDE.md / SCHEMA.md / templates）
                         ↓
                CATALOG.md（生成，agent 写前先读）
                         ↓
                git（历史 + 多机同步；Obsidian Sync 可并存）
```

这就是 Karpathy 2026-04 的 LLM wiki 模式。vault-keeper 不发明新结构，只把它装到任何现有库上并让它持续运转。

## 目录角色

| 角色 | 含义 | 规则 | 名字推断 |
|---|---|---|---|
| `raw` | 原始素材 | 只追加不编辑（R1 查 git 历史）；进 CATALOG | raw, sources, clippings, inbox, captures, literature, references, 原始, 素材 |
| `wiki` | 沉淀层 | 必须有 frontmatter（F1）；该目录 ≥90% 笔记都有的字段为必填（F2）；进 CATALOG | 其余有 md 的目录 |
| `private` | 私人 | lint 照查，但不进 CATALOG、不进 find、agent 不主动引用 | life, personal, private, journal, diary, daily, 日记 |
| `templates` | 模板 | `vk new` 从这里取；`{{date}}` `{{title}}` `{{time}}` 替换 | templates, template, _templates, 模板 |
| `archive` | 归档 | 不进 CATALOG；「删除」= 移到这里 | archive, archives, archived, 归档 |
| `attachments` | 附件 | 只用于链接解析 | md 占比 < 30% 的目录，或 attachments / assets / images / media |
| `excluded` | 不碰 | 不读、不扫、不在输出里提内容 | `.obsidian` `.git` `.trash` 以及名字像 secret / credential / key / .env 的 |

角色写在库里的 `.vault-keeper.json`，随库同步，所以两台机器判断一致。

## 配置 `.vault-keeper.json`

```json
{
  "version": 1,
  "roles": {"raw": ["raw", "inbox"], "wiki": ["insights", "decisions"], "private": ["life"],
            "templates": ["templates"], "archive": ["archive"], "excluded": ["secrets"]},
  "required": {"insights": ["date", "project", "status", "tags", "type"]},
  "rules_file": "SCHEMA.md",
  "catalog": "CATALOG.md",
  "root_allow": ["harness-session"],
  "secret_dirs_ok": [],
  "sync": {"push_every_min": 30, "auto_pull": true}
}
```

全局 `~/.agents/vault-keeper.json` 只记 `{"vaults": ["~/Vault"]}`，供 `health` 和 `sync --all` 用。
状态在 `~/.agents/.vault-keeper/<库名>-<hash>/`：`last-lint.json`、`last-sync.json`、`journal.jsonl`、`sync.lock`。

## lint 代码

| 代码 | 级别 | 含义 | 谁修 |
|---|---|---|---|
| F1 | warn | wiki 笔记无 frontmatter | agent 提案，用户同意 |
| F2 | warn | 缺该目录的必填字段 | 同上 |
| F3 | warn | 正文残留 `{{date}}` 等占位符 | agent |
| L1 | warn | 断链（含 `[text](x.md)`）。附候选 | agent 判断：改链 / 去链 / 补笔记 |
| L2 | info | `[[同名]]` 多个候选，Obsidian 静默选一个 | 改带路径链接 |
| L3 | info | 孤儿（无入链） | 可选 |
| R1 | warn | raw 文件在 git 历史里被修改 | 报告；内容搬去 wiki |
| S1 | warn | `supersedes: X` 但 X 的 status 不是 superseded | 改旧笔记 status |
| D1 | info | 不同目录同名文件（README/index 等常见名除外） | 改名或带路径链接 |
| P1 | info | 根目录游离笔记（白名单外） | 移进目录 |
| X1 | warn | 疑似密钥目录被 git 跟踪或未 ignore | 用户决定：放行 / 移出 |
| G1–G4 | warn/info | 未提交 / 未推送 / 无远端 / 远端领先 | `vk sync --apply` |
| C1 | info | CATALOG 缺失或过期 | `vk index` |
| H1 | warn | 根目录无 CLAUDE.md / AGENTS.md | `vk init --apply` |

链接解析按 Obsidian 语义：`[[name]]` 按文件名、大小写不敏感、多候选取最短路径；`[[a/b/name]]` 按库内路径，再按相对当前文件路径；
代码块和行内代码里的 `[[ ]]` 不算链接；`http(s)://` `file://` 不查；库外绝对路径存在就算外部文件。

## 同步语义

- `vk sync`：dry-run 显示将提交 / 推送 / 拉取什么。
- `vk sync --apply`：`git add -A` → commit（信息自动写改动按目录统计）→ `pull --rebase` → push。
- 冲突：`rebase --abort` 回到本地状态，打印 `CONFLICT` 与文件名，退出码 2。本地 commit 还在，内容不丢。
- `--auto`（钩子用）：总是提交；push 只在距上次推送 ≥ `push_every_min` 或积压 ≥10 个 commit 时做；**永远退出 0**，错误写进 `last-sync.json`，下次 `health` 提示。
- 锁：`sync.lock`，5 分钟过期；两个会话同时 Stop 不会打架。
- 永不 `--force`、`reset`、`checkout -- file`。

## `vk setup`：一键的边界

做：找库（Obsidian `obsidian.json`，或 `--vault`）→ `init --apply --git`（新文件、幂等）→ 无 commit 的打初始快照 → 有远端就 `sync --apply` → Claude Code 钩子 → macOS launchd 定时 → 体检一行。
不做：不建远端（除非 `--create-remote` 或交互里说 y，且 `gh` 已登录）；不装 Obsidian；不修任何需要判断的问题（F1/L1 等留给 agent 对话）；不推送没有远端的库。
`vk uninstall` 撤钩子和定时，库里的文件和 git 历史保留。

## 两台电脑的最短路径

```
# 机器 A（已有库）
bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)" -- --create-remote
#   等价于：vk init --apply + 初始快照 + gh repo create vault-<名> --private --push + hooks + schedule

# 机器 B
git clone <远端> ~/Vault
bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)"
#   读到同一份 .vault-keeper.json，只补 CLAUDE.md、注册、钩子
```
手机 / 平板只读：GitHub app 或 Obsidian Sync（和 git 并存没问题，但不要 iCloud + Sync 双开）。

## 和 skill-keeper 的关系
同一套哲学：脚本做确定性的事、agent 做判断、一条消息问完、钩子让它持续、永不删除。
skill-keeper 管「agent 会做什么」，vault-keeper 管「agent 知道什么」。两个 skill 的 `health` 都挂在 SessionStart，各一行。

## Obsidian 官方 CLI
Obsidian 1.12.4（2026-02）起有 `obsidian` CLI（search / backlinks / property）。它需要桌面 app 在跑，所以 vault-keeper 全部基于纯文件，不依赖它；需要 Obsidian 内部索引（如 Bases 查询）时 agent 可以另外调用。
