# AI-Skills

Team skills for AI coding agents. Each skill is one folder under `skills/` holding a
`SKILL.md`: YAML frontmatter (`name`, `description`) followed by Markdown instructions.

| Skill | What it covers |
| --- | --- |
| [`foycom`](skills/foycom/SKILL.md) | Foycom: developing in Odoo, the Odoo-to-Foycom conversion, version mapping (Foycom = Odoo − 10), and the server deployment flow (pull in `/opt/odoo` → convert → upgrade/restart) |

The repo is also a **Claude Code plugin marketplace**, through `.claude-plugin/`.

---

## Claude Code (CLI, VS Code / JetBrains extensions, desktop)

**Option A — as a plugin (recommended, updates with `git pull` of the marketplace):**

```text
/plugin marketplace add amitkansagara/AI-Skills
/plugin install ai-skills@ai-skills
```

This is a private repo, so your git or SSH access to GitHub must work. To update
later, run `/plugin marketplace update ai-skills`.

**Option B — as a plain skill:**

```bash
git clone git@github.com:amitkansagara/AI-Skills.git ~/AI-Skills
# for you, in every project:
ln -s ~/AI-Skills/skills/foycom ~/.claude/skills/foycom
# or for one project only (commit it to share with the team):
mkdir -p .claude/skills && cp -r ~/AI-Skills/skills/foycom .claude/skills/
```

Run `/skills` (or ask "what skills do you have?") to check that `foycom` is listed.
Claude loads it on its own when a request matches its description, for example
"deploy this on the server".

## Claude.ai and Claude Desktop app

Zip the skill folder and upload it under **Settings → Capabilities → Skills**. Code
execution must be enabled.

```bash
cd skills && zip -r foycom.zip foycom
```

## Cursor

Cursor uses **rules**. Add the skill as an agent-requested rule, so Cursor pulls it in
when the description matches:

```bash
mkdir -p .cursor/rules
{ printf -- '---\ndescription: Foycom development, Odoo-to-Foycom conversion and server deployment (pull in /opt/odoo, convert, restart foycom)\nalwaysApply: false\n---\n'; \
  sed '1,/^---$/{/^---$/!d};1,/^---$/d' ~/AI-Skills/skills/foycom/SKILL.md; } > .cursor/rules/foycom.mdc
```

The `sed` strips the skill's own frontmatter. Alternatively, open
**Cursor Settings → Rules**, create a rule named `foycom` and paste the body of
`SKILL.md`. If your Cursor version supports Agent Skills folders, copying
`skills/foycom/` into the folder it documents works too.

## GitHub Copilot (VS Code / JetBrains)

```bash
mkdir -p .github/instructions
cp ~/AI-Skills/skills/foycom/SKILL.md .github/instructions/foycom.instructions.md
```

Copilot reads `.github/instructions/*.instructions.md`. Alternatively, append the body
to `.github/copilot-instructions.md`.

## OpenAI Codex CLI

Reference the skill from `AGENTS.md` at the repo root (or `~/.codex/AGENTS.md` for all
projects):

```markdown
For Foycom work or any server deployment, read and follow ~/AI-Skills/skills/foycom/SKILL.md.
```

If your Codex version supports skills, copy the folder to `~/.codex/skills/foycom/`
instead.

## Gemini CLI

Import the skill from `GEMINI.md` at the project root (or `~/.gemini/GEMINI.md`):

```markdown
@~/AI-Skills/skills/foycom/SKILL.md
```

## Windsurf

```bash
mkdir -p .windsurf/rules && cp ~/AI-Skills/skills/foycom/SKILL.md .windsurf/rules/foycom.md
```

Then set the rule's activation to *Model Decision* in the Windsurf rules panel.

## Grok, ChatGPT and any other assistant

These assistants have no local skill folder. Paste the body of `SKILL.md` (everything
after the second `---`) into the assistant's **custom instructions**, or into a
**project / workspace knowledge file** (Grok Projects, ChatGPT Projects or Custom GPTs,
Gemini Gems). For a one-off chat, attach `SKILL.md` and say "follow this".

---

## Adding a new skill

1. Create `skills/<skill-name>/SKILL.md` with frontmatter:
   ```markdown
   ---
   name: <skill-name>
   description: What it does and exactly when to use it (this decides when agents load it).
   ---
   ```
2. Keep secrets, passwords, keys and host-specific credentials out of the repo.
3. Bump `version` in `.claude-plugin/plugin.json` so plugin users get the update.
