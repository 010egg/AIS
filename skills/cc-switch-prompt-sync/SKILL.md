---
name: cc-switch-prompt-sync
description: Export prompt presets from CC Switch's local SQLite database into repository Markdown files and sync them into a `prompts/` directory. Use when the user asks to migrate, back up, publish, or update prompts managed in CC Switch, especially for requests mentioning `~/.cc-switch/cc-switch.db`, "Claude 提示词管理", or syncing prompts into git.
---

# CC Switch Prompt Sync

Use this skill to move prompt presets out of CC Switch and into a repository as Markdown files.

Default assumptions:

- Source database: `~/.cc-switch/cc-switch.db`
- Default prompt app: `claude`
- Target repository directory: `prompts/`

## Workflow

1. Confirm the repository has a `prompts/` directory. Create it if missing.
2. Inspect the requested prompt scope.
If the user named specific prompts, export only those.
If the user said "all Claude prompts", export all rows where `app_type = 'claude'`.
3. Run `scripts/export_prompts.py` from this skill.
4. Review the written Markdown files in `prompts/`.
5. If requested, commit and push the repository changes.

## Quick Start

Export all Claude prompts into the current repository:

```bash
python3 skills/cc-switch-prompt-sync/scripts/export_prompts.py \
  --output-dir prompts
```

Export only a named subset:

```bash
python3 skills/cc-switch-prompt-sync/scripts/export_prompts.py \
  --output-dir prompts \
  --name "指标业务定义" \
  --name "AskUserQuest&Todolist" \
  --name "问问题创建代办-简版"
```

Preview writes without changing files:

```bash
python3 skills/cc-switch-prompt-sync/scripts/export_prompts.py \
  --output-dir prompts \
  --dry-run
```

## Naming Rules

The exporter uses stable filenames for the three known Claude prompts:

- `指标业务定义` -> `metrics-business-definition.md`
- `AskUserQuest&Todolist` -> `ask-user-question-and-todolist.md`
- `问问题创建代办-简版` -> `question-to-todo-lite.md`

For unknown prompt names, the script falls back to a simple ASCII slug, then to `prompt-XX.md` if needed.

## Output Format

Each exported file is Markdown:

- First line: `# <prompt name>`
- Blank line
- Raw prompt content from the database

Preserve the prompt content exactly. Do not rewrite or summarize unless the user explicitly asks.

## Validation

After export:

- List `prompts/` and confirm the expected files exist.
- Review `git status`.
- If the user asked for remote sync, commit and push after verifying the diff.

## Resource

### scripts/export_prompts.py

Read the CC Switch SQLite database and write prompt Markdown files into the repository.
