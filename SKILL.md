---
name: vault-keeper
description: 维护 Obsidian / Markdown 知识库并把对话里的结论沉淀进去：收编现有库、校验 frontmatter 和双链、生成目录、按主题找笔记、新建笔记、git 多机同步、会话启动健康提示。触发词：沉淀 / 记到知识库 / 存到 vault / 写进 Obsidian / 整理笔记 / 整理 vault / 断链 / 同步笔记到另一台电脑 / 之前有没有记过 X / 哪篇笔记讲 X / vault-keeper。Use when the user wants to save a conclusion into their notes, organize, lint, find or sync an Obsidian vault, or when a session-start line from vault-keeper says the vault needs attention.
argument-hint: "[find <主题> | new <目录> <标题> | sync | init | hooks]"
metadata:
  tier: always
  kind: pipeline
---

# vault-keeper

**vault-keeper 是知识库的管家：规则放在 agent 一定会读的地方，格式和同步交给脚本，每次用每次整理。**
它解决四件事：agent 写进 Obsidian 后没人提交、agent 不知道库里已经有什么所以不链接、frontmatter 和双链越写越乱、两台电脑各一份。适用于任何 Obsidian 或纯 Markdown 库，Claude Code、Codex 等所有 agent。不删任何东西，git 永不 force。

**vault-keeper keeps an Obsidian or Markdown knowledge base in order: rules where every agent reads them, format and sync done by scripts, a little tidying every time you use it.**
It handles four things: notes the agent wrote but nobody committed, links the agent never made because it didn't know what existed, frontmatter and wikilinks drifting, and two machines holding two versions. Works with any Obsidian or plain-Markdown vault and any agent. Never deletes anything; git is never forced.

用法 / Usage: 说「把这个沉淀到知识库」或「整理一下 vault」· 找笔记 `/vault-keeper find <主题>` · 新建 `/vault-keeper new <目录> <标题>` · 同步 `/vault-keeper sync` · 文档 [中文](README.md) / [English](README.en.md)

---

## 给 agent 的执行指令 / Instructions for the agent

你是用户知识库的管家。脚本做一切确定性的事（扫描、校验、生成目录、git），你做判断（写什么、放哪、链到哪、断链怎么修）。
所有要用户决定的事**一条消息问完**再执行。Respond in the user's language; the CLI's own messages are Chinese for now.

脚本在本 SKILL.md 旁边：`scripts/vk.py`。装在库里时就是
`python3 ~/.agents/skills/vault-keeper/scripts/vk.py <子命令>`（下文简写为 `vk`）。
库的位置：在库目录里运行会自动找到；否则用 `--vault <目录>`；装过 `init` 的库会被记住。
会改东西的子命令默认 dry-run，`--apply` 才执行。

## 模式路由

| 用户说的话 | 做法 |
|---|---|
| 沉淀 / 记下来 / 存到 vault / 写进知识库 / 这个结论存起来 | **沉淀流程**（下文） |
| 无参数 / 整理 / 太乱 / 健康提示 / 断链 | **例行整理** |
| 之前有没有记过… / 哪篇笔记讲… / find | `vk find "<原话>"`，把前几条和一句话摘要给用户 |
| 同步 / 推送 / 另一台电脑看不到 | `vk sync`，看 dry-run 再 `--apply`；CONFLICT 原样转告 |
| 新建一篇… | `vk new <目录> "<标题>"`，然后按沉淀流程写正文 |
| 一键 / 全部装好 / setup | `vk setup`：自动找库、收编、git、钩子、定时；库找不到就问路径 |
| 第一次 / 初始化 / 收编这个库 | `vk init --vault <目录>` 看提案，用户同意后 `--apply` |
| 卸载 / 不要自动提交了 | `vk uninstall`（只撤钩子和定时，库不动） |
| 自动 / 钩子 / 每次都提交 | `vk hooks`（Claude Code）；Codex 用 `vk schedule` |
| 解释代码 L1 | `vk explain L1` |

## 沉淀流程（把对话里的东西写进库）

1. **读规则**：库根目录的 `CLAUDE.md`（或 `AGENTS.md`）。它写明目录角色、必填字段、不做的事。没有就先走 `init`。
2. **先找再写**：`vk find "<主题关键词>"`。有同主题笔记 → 优先更新它（追加小节、改 status），不新建。
3. **选目录**：原文、截图、数据、对话片段 → raw 层（只追加）；结论、分析、方法 → insights 一类的 wiki 目录；拍板的事 → decisions；跨项目反复验证过的 → domains。拿不准就问，和其他问题一起问。
4. **新建**：`vk new <目录> "<标题>"`。它套用 templates/ 里的模板、按目录习惯起文件名。
5. **写正文**：按模板的小节写；frontmatter 必填字段填全（见 CLAUDE.md）；**至少链接 2 篇相关笔记**，用 `[[路径|标题]]`，路径从 `CATALOG.md` 或 `vk find` 的输出里拿；库的 schema 要求置信度、来源标注的就标。
6. **推翻旧结论时**：旧笔记 `status: superseded`，新笔记 frontmatter 加 `supersedes: <旧文件>`，正文写「为什么变了」。
7. **收尾**：`vk lint`（只看自己改的文件的问题）→ `vk index` → 没装钩子就 `vk sync --apply`。
8. **回复**：一行路径、链接了哪几篇、一句话结论。不要把整篇笔记贴回对话。

不写进库的：密钥和任何疑似凭证；用户没明说要记的私人内容；excluded 目录里的任何东西。

## 例行整理（每次都是这套，不是一次性的）

### 1. 检查
```
vk lint            # 或 vk lint --full 看全部
vk stats
```
同时读 `~/.agents/.vault-keeper/<库>/last-lint.json`（结构化）。

### 2. 一条消息问完所有问题（编号，用户可以用「1 全部 2 b 3 跳过」回答）
按顺序列出，没有的跳过，每项一行：

1. **自动项**：重建目录、`sync --apply`（列出将提交多少文件、推送多少 commit）。问「执行？」
2. **F1/F2 缺 frontmatter / 缺字段**：你读每篇笔记开头，给出**补好的 frontmatter**，用户只需说同意哪些。≤10 篇一次，多了分批。
3. **L1 断链**：每条给结论：改成候选 X / 删掉链接留纯文本 / 这篇笔记本该存在，建议新建。
4. **L2 同名歧义**：给出改成带路径链接的清单。
5. **S1 / R1**：S1 给出要改 status 的旧笔记；R1 只报告，解释 raw 不该改，问是否把改动内容搬到 wiki 层。
6. **X1 密钥目录**：报告，问是放行（`secret_dirs_ok`）还是移出库。你**不读**那个目录的内容。
7. **钩子**：`~/.claude/settings.json` 里没有 `vk.py health` 就问是否安装（Claude Code 用户）；Codex 用户问是否装 `vk schedule`。

L3 孤儿和 P1 游离笔记默认不问，只在收尾里报数字；用户要处理再展开。

### 3. 执行（只做用户同意的，按这个顺序）
```
# ① 你用 Edit 改 frontmatter / 链接 / status（脚本不改正文，判断是你的事）
vk index                       # ② 重建目录
vk lint                        # ③ 确认问题数下降
vk sync --apply                # ④ 提交 + pull --rebase + push
vk hooks --install             # 仅用户同意
```
- 脚本输出有 `CONFLICT` 或 `ERROR` 的行原样转告，不要自己绕；冲突时脚本已回退，本地内容没丢，给用户三条手工命令。
- 任何删除都不做：要「删」的笔记移到 archive/。

### 4. 收尾：5 行以内
- 库多少篇、几个目录、CATALOG 多少条
- 这次改了什么（数字）、提交推送了没有
- 还剩什么没决定（L3 孤儿 N、P1 N）
- 另一台电脑：`git pull` 或等钩子
- 下次什么时候再跑（装了钩子就不用记）

## 在 Codex 里运行
同一套命令照跑。差别：没有钩子，`vk schedule --install` 装一个每 10 分钟的 launchd 任务做 `sync --auto`；或者每次沉淀完手动 `vk sync --apply`。规则文件 AGENTS.md 是 CLAUDE.md 的软链接，Codex 进库目录会自动读。

## 不做的事
- 不 `rm`、不 `git push --force`、不 `git reset`、不 `git checkout -- <文件>`
- 不读 excluded / 密钥目录的内容，不读 `.env`，不把密钥写进任何笔记
- 不替用户决定正本、目录和结论；只提案
- 不手改 `CATALOG.md`，不改 raw 层的内容
- 不把 private 目录的内容带进回复，除非用户明确问
