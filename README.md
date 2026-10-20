# vault-keeper

中文 | [English](README.en.md)

> 知识库的管家：规则放在 agent 一定会读的地方，格式和同步交给脚本，每次用每次整理。给 Obsidian 和任何 Markdown 库，Claude Code、Codex 等所有 agent。

```bash
npx skills add scotti1i/vault-keeper
```
然后对 agent 说一句「**把这个沉淀到知识库**」或「**整理一下 vault**」。

## 它解决什么

| 你遇到的 | 它做的 |
|---|---|
| agent 写了笔记，没人提交，另一台电脑看不到 | `vk sync`：提交、pull --rebase、推送；Claude Code Stop 钩子自动做；冲突不覆盖、原样报出 |
| agent 不知道库里已经有什么，重复写、不链接 | 生成 `CATALOG.md`（每篇一行）；`vk find` 按主题找；规则要求新笔记至少链 2 篇 |
| 规则写在某个文件里，agent 进来根本不读 | `vk init` 在库根目录生成 `CLAUDE.md` + `AGENTS.md`：目录角色、必填字段、不做的事。Claude 和 Codex 一进目录就加载 |
| frontmatter 缺字段、双链断了、模板占位符没填 | `vk lint`：16 种检查，断链给候选，agent 提案你确认 |
| 原始素材被改、旧结论没标 superseded | R1 查 git 历史；S1 查 supersedes 链 |
| 密钥文件混在被同步的库里 | X1 识别疑似密钥目录，脚本本身绝不读它们 |
| 整理过一次又乱了 | 每次会话启动一行健康提示；每次运行都是增量 |

不删任何东西，git 永不 force。改动都在 git 历史里。

## 安装

```bash
# 方式一：Vercel 的 skills CLI（推荐）
npx skills add scotti1i/vault-keeper

# 方式二：手动
git clone https://github.com/scotti1i/vault-keeper ~/.agents/skills/vault-keeper
```
依赖：Python 3.9+、git。无第三方包。Obsidian 本身不是必需的。

## 第一次运行会发生什么

```bash
vk init --vault ~/Vault            # 看提案：目录角色、必填字段、将写入什么
vk init --vault ~/Vault --apply    # 写 .vault-keeper.json、CLAUDE.md、AGENTS.md、CATALOG.md
vk lint                            # 看问题
vk hooks --install                 # Claude Code：启动提示 + 自动提交
```
`vk` = `python3 ~/.agents/skills/vault-keeper/scripts/vk.py`。

对现有库，`init` 不建骨架、不搬文件：扫顶层目录按名字和内容推断角色（raw / wiki / private / templates / archive / excluded），按该目录 ≥90% 笔记都有的字段推断必填项，找到已有的 SCHEMA.md 就引用它。推断结果写在库里的 `.vault-keeper.json`，可以改。

空库会放一套默认模板（insight / decision / raw / project）。

## 命令

```
vk init [--vault D] [--apply] [--git]    收编：推断角色、写规则块、生成目录
vk lint [--full] [--json]                校验（只读）
vk index                                 生成 CATALOG.md
vk find <主题>                           找笔记
vk new <目录> "<标题>" [--template T]    按模板新建，自动起名
vk sync [--apply] [--no-push]            git 提交 / 拉取 / 推送（默认 dry-run）
vk health                                一行健康提示（SessionStart）
vk hooks [--install]                     Claude Code 钩子
vk schedule [--install] [--every 10]     launchd / cron 定时同步（给 Codex 兜底）
vk stats / explain <代码>
```

## 它是怎么想的

- **规则要在 agent 必经之路上。** 写在 SCHEMA.md 里的规则只有被告知时才会读；根目录的 CLAUDE.md / AGENTS.md 是 Claude Code 和 Codex 的原生入口。`init` 只在文件里维护一个标记块，你自己写的部分不动。
- **目录是生成的，不是手写的。** 手写的 INDEX 必然过期。CATALOG.md 从 frontmatter 生成，agent 写前先读，这是「把知识 link 起来」的前提。
- **脚本管格式，人管内容。** lint 查得了「有没有标置信度」，查不了「标得对不对」。工具不假装能替你判断。
- **同步要自动，冲突要显眼。** 本地提交勤，推送批量；冲突时回退、报告、不覆盖。
- **三层模型，不发明新结构。** raw 不可变、wiki 由 agent 写、schema 由人定，这是 Karpathy 的 LLM wiki 模式；vault-keeper 只是把它装到你已有的库上。

详细约定见 [references/conventions.md](references/conventions.md)。

## 两台电脑

见 conventions.md「两台电脑的最短路径」。一句话：两边都 `init --apply` + `hooks --install`，`.vault-keeper.json` 随库同步。手机只读用 GitHub app 或 Obsidian Sync。

## 已知边界

- 手机上 agent 写入没有好方案。
- Windows 建 AGENTS.md 软链接可能失败，会退化成复制一份。
- `find` 是字符二元组匹配，不是向量检索；几百到一两千篇够用。
- 没有 git 的库只有 lint / index / new 可用，没有同步和历史。
- CLI 输出目前是中文，agent 会翻译。

## 许可
MIT
