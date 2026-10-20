# vault-keeper

[中文](README.md) | English

> A steward for your knowledge base: rules where every agent reads them, format and sync done by scripts, a little tidying every time you use it. For Obsidian and any Markdown vault; for Claude Code, Codex and every other agent.

```bash
npx skills add scotti1i/vault-keeper
```
Then tell your agent **"save this to my vault"** or **"tidy up my vault"**.

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
# Option 1: Vercel's skills CLI (recommended)
npx skills add scotti1i/vault-keeper

# Option 2: manual
git clone https://github.com/scotti1i/vault-keeper ~/.agents/skills/vault-keeper
```
Requirements: Python 3.9+, git. No third-party packages. Obsidian itself is optional.

## First run

```bash
vk init --vault ~/Vault            # proposal: folder roles, required fields, what will be written
vk init --vault ~/Vault --apply    # writes .vault-keeper.json, CLAUDE.md, AGENTS.md, CATALOG.md
vk lint                            # see what's wrong
vk hooks --install                 # Claude Code: startup line + auto-commit on Stop
```
`vk` = `python3 ~/.agents/skills/vault-keeper/scripts/vk.py`.

On an existing vault, `init` builds no skeleton and moves no files. It scans top-level folders and infers roles from names and contents (raw / wiki / private / templates / archive / excluded), infers required fields as those present in at least 90% of a folder's notes, and references an existing SCHEMA.md if it finds one. The result lives in `.vault-keeper.json` inside the vault, so both machines agree, and you can edit it.

An empty vault gets a default set of templates (insight / decision / raw / project).

## Commands

```
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
