# vault-keeper

[中文](README.md) | English

> A steward for your knowledge base: rules where every agent reads them, format and sync done by scripts, a little tidying every time you use it. For Obsidian and any Markdown vault; for Claude Code, Codex and every other agent.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)"
```
One command: install the skill → find every vault in Obsidian's registry → write rules and a catalog into each → `git init` and snapshot the ones without git → commit and push → install the Claude Code hooks and a 10-minute background sync → health check. Then tell your agent **"save this to my vault"** or **"tidy up my vault"**.

## What it solves

| You have | It does |
|---|---|
| The agent wrote notes, nobody committed, the other machine never sees them | `vk sync`: commit, pull --rebase, push; a Claude Code Stop hook does it automatically; conflicts are reported, never overwritten |
| The agent doesn't know what's already there, so it duplicates and never links | Generates `CATALOG.md` (one line per note); `vk find` searches by topic; the rules require at least two links per new note |
| Your rules live in a file the agent never opens | `vk init` writes `CLAUDE.md` + `AGENTS.md` at the vault root: folder roles, required fields, forbidden actions. Claude and Codex load them the moment they enter the folder |
| Missing frontmatter, broken wikilinks, unfilled template placeholders | `vk lint`: 16 checks, broken links come with candidates, the agent proposes and you confirm |
| Raw sources got edited, old conclusions never marked superseded | R1 checks git history; S1 checks `supersedes` chains |
| Secret files sitting inside a synced vault | X1 flags secret-looking folders; the script itself never reads them |
| You tidied once and it's a mess again | One health line at every session start; every run is incremental |

Nothing is deleted. Git is never forced. Every change is in history.

## Install

```bash
# One click (recommended): install + adopt + hooks + timer; idempotent, safe to re-run
bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)"

# Same, but name the vault and create a private GitHub repo for sync (needs gh auth login first)
bash -c "$(curl -fsSL https://raw.githubusercontent.com/scotti1i/vault-keeper/main/install.sh)" -- --vault ~/Notes --create-remote

# Skill only, touch no vault (run vk setup or vk init yourself later)
npx skills add scotti1i/vault-keeper
```
Requirements: Python 3.9+, git. No third-party packages. Obsidian itself is optional; without it, point at a folder with `--vault`.

### What the one-click install does

| Step | What happens | Undo |
|---|---|---|
| Find vaults | Reads Obsidian's `obsidian.json` for every vault you have opened | override with `--vault` |
| Adopt | Per vault: infer folder roles and required fields → `.vault-keeper.json`; write the `CLAUDE.md` + `AGENTS.md` rules block; generate `CATALOG.md`; default templates for empty vaults | delete those files |
| Git | `git init` + first snapshot where there is none; commit pending changes where there is; push if a remote exists | local git, delete `.git` any time |
| Remote | Not created by default; `--create-remote` or an interactive yes creates a private repo with `gh` and pushes | delete the repo on GitHub |
| Hooks | Claude Code: one startup line + auto-commit on Stop (settings.json is backed up) | `vk uninstall` |
| Timer | macOS launchd runs `sync --auto --all` every 10 minutes, covering Codex and edits made directly in Obsidian | `vk uninstall` |
| Health | One line per vault: how many notes, what needs attention | — |

From then on you just say "save this" or "tidy up" in conversation; format and sync are no longer your problem.

`vk` = `python3 ~/.agents/skills/vault-keeper/scripts/vk.py`. The step-by-step path still works: `vk init --vault ~/Vault` (proposal) → `--apply` → `vk lint` → `vk hooks --install`.

On an existing vault, `init` builds no skeleton and moves no files. It scans top-level folders and infers roles from names and contents (raw / wiki / private / templates / archive / excluded), infers required fields as those present in at least 90% of a folder's notes, and references an existing SCHEMA.md if it finds one. The result lives in `.vault-keeper.json` inside the vault, so both machines agree, and you can edit it.

An empty vault gets a default set of templates (insight / decision / raw / project).

## Commands

```
vk setup [--vault D…] [-y] [--create-remote]   one click: find vaults → init → git → sync → hooks → timer → health
vk uninstall                             remove hooks and timer; vault files stay
vk init [--vault D] [--apply] [--git]    adopt a vault: infer roles, write the rules block, build the catalog
vk lint [--full] [--json]                checks (read-only)
vk index                                 generate CATALOG.md
vk find <topic>                          find notes
vk new <dir> "<title>" [--template T]    create from template, name it the way the folder does
vk sync [--apply] [--no-push]            git commit / pull / push (dry run by default)
vk health                                one-line status (SessionStart)
vk hooks [--install]                     Claude Code hooks
vk schedule [--install] [--every 10]     launchd / cron timer (for Codex and other hook-less agents)
vk stats / explain <code>
```

## How it thinks

- **Rules belong on the agent's path.** A SCHEMA.md is read only when someone says so; `CLAUDE.md` and `AGENTS.md` at the root are the native entry points for Claude Code and Codex. `init` maintains one marked block inside them and leaves your own text alone.
- **The catalog is generated, never handwritten.** A handwritten index always goes stale. `CATALOG.md` is built from frontmatter and the agent reads it before writing. That is the precondition for notes actually getting linked.
- **Scripts own format, people own content.** Lint can check that a confidence tag exists; it cannot check that it's right. The tool does not pretend otherwise.
- **Sync should be automatic, conflicts should be loud.** Commit often locally, push in batches; on conflict roll back, report, never overwrite.
- **Three layers, no new structure.** Immutable raw sources, an agent-written wiki, a human-written schema: Karpathy's LLM wiki pattern. vault-keeper only installs it onto the vault you already have.

Details: [references/conventions.md](references/conventions.md) (Chinese for now; the agent reads it fine).

## Two machines

Run `init --apply` and `hooks --install` on both; `.vault-keeper.json` travels with the vault. Phones stay read-only via the GitHub app or Obsidian Sync (which coexists with git; just don't run iCloud and Sync on the same vault).

## Known limits

- No good story for agents writing from a phone.
- On Windows the `AGENTS.md` symlink may fail; it falls back to a copy.
- `find` is character-bigram matching, not embeddings. Fine up to a couple of thousand notes.
- Without git you get lint / index / new, but no sync or history.
- CLI output is Chinese for now; the agent translates. An English mode is planned.

## License
MIT
