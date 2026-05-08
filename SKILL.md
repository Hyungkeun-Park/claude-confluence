---
name: confluence
description: Read, edit, and update Confluence pages as markdown with lossless macro preservation (TOC, panels, code blocks, tables). Use whenever the user mentions Confluence pages, wants to upload/download/edit wiki content, asks about "컨플루언스", references a Confluence URL or page ID, or needs to push a large markdown file to a wiki page. Also triggers for round-trip workflows like "read this page, change X, and update it back." Handles files of any size (100KB+) without timeout.
argument-hint: [read|write|update] <page-id-or-url> [file-path]
---
# Confluence Page Manager

Read and write Confluence pages as markdown files. Macros (TOC, panels, status lozenges) survive the round-trip through HTML comment placeholders — they are preserved on read and restored on write.

## Prerequisites

Three environment variables are needed:

| Variable | Required | Description |
|----------|----------|-------------|
| `CONFLUENCE_EMAIL` | Yes | Atlassian account email |
| `CONFLUENCE_API_TOKEN` | Yes | API token from https://id.atlassian.com/manage-profile/security/api-tokens |
| `CONFLUENCE_BASE_URL` | No | Confluence site URL (default: `nota-dev.atlassian.net`) |

Check with: `echo $CONFLUENCE_EMAIL`
If not set, try `source ~/.bashrc`. If still empty, ask the user.

## Tool

The tool script location depends on how this skill was installed:

| Method | Script path |
|--------|-------------|
| Git repo | `tools/confluence_page.py` (relative to skill root) |
| npm | `confluence-page` (global binary) |
| pip | `claude-confluence` or `python -m claude_confluence` |

Detect which is available:
```bash
# Check pip install first (most reliable)
command -v claude-confluence 2>/dev/null && CONFLUENCE_CMD="claude-confluence"
# Then npm
command -v confluence-page 2>/dev/null && CONFLUENCE_CMD="confluence-page"
# Fallback to git repo script
[ -z "$CONFLUENCE_CMD" ] && CONFLUENCE_CMD="python $SKILL_DIR/tools/confluence_page.py"
```

In all examples below, replace `$CONFLUENCE_CMD` with the detected command.

## Usage

### Read a page

```bash
$CONFLUENCE_CMD read --page-id <PAGE_ID> -o <output.md>
```

- Downloads the page in storage format, converts to markdown
- Macros become `<!-- confluence:xxx -->` placeholders
- Code blocks become fenced code blocks with language hints
- Tables become markdown tables

### Write (update) a page

```bash
$CONFLUENCE_CMD write --page-id <PAGE_ID> --file <input.md> [--title "New Title"] [--message "Version note"]
```

- Converts markdown to Confluence storage format
- Restores `<!-- confluence:xxx -->` placeholders to actual macros
- Converts fenced code blocks to Confluence code macros with CDATA
- Auto-increments the version number

## Arguments

The `$ARGUMENTS` variable contains text after the command name.

Examples:
- `/confluence` -> interactive mode
- `/confluence read 2251030576` -> read page
- `/confluence write 2251030576 docs/my-page.md` -> upload file
- `/confluence update https://nota-dev.atlassian.net/wiki/spaces/NPP02/pages/2251030576/...` -> round-trip

## Execution Steps

### 1. Parse Arguments

Extract the operation and page ID from `$ARGUMENTS`:
- If a full Confluence URL is given, extract the page ID (the numeric segment after `/pages/`)
- If only a page ID is given with no operation keyword, infer from context (file path present -> write, otherwise -> read)
- If no arguments, ask the user what they want to do

### 2. Ensure Environment

```bash
source ~/.bashrc 2>/dev/null; echo "EMAIL=${CONFLUENCE_EMAIL:-unset} TOKEN=${CONFLUENCE_API_TOKEN:+set}"
```

If either variable is `unset`, run the setup flow below. If both are set, skip to step 3.

#### Setup Flow (first-time only)

1. Ask the user for the missing values using `AskUserQuestion`:
   - `CONFLUENCE_EMAIL`: "What is your Atlassian account email?"
   - `CONFLUENCE_API_TOKEN`: "Enter your Confluence API token (generate at https://id.atlassian.com/manage-profile/security/api-tokens)"

2. Persist to `~/.bashrc` so future sessions pick them up automatically:
   ```bash
   # Remove any existing confluence env lines to avoid duplicates
   sed -i '/^export CONFLUENCE_EMAIL=/d' ~/.bashrc
   sed -i '/^export CONFLUENCE_API_TOKEN=/d' ~/.bashrc
   # Append new values
   echo 'export CONFLUENCE_EMAIL="<user-provided-email>"' >> ~/.bashrc
   echo 'export CONFLUENCE_API_TOKEN="<user-provided-token>"' >> ~/.bashrc
   ```

3. Source to activate in the current session:
   ```bash
   source ~/.bashrc
   ```

4. Confirm registration succeeded:
   ```
   "Confluence credentials saved to ~/.bashrc. They will be available in all future sessions."
   ```

#### Using credentials

Always export env vars in the same command chain as the script — shell state does not persist between Bash calls:
```bash
source ~/.bashrc && $CONFLUENCE_CMD ...
```

### 3. Execute Operation

#### Read ("read")

1. Determine output path:
   - If user specified a file path, use it
   - Otherwise default to `/tmp/confluence_<PAGE_ID>.md`

2. Run:
   ```bash
   export CONFLUENCE_EMAIL="..." CONFLUENCE_API_TOKEN="..." && \
   $CONFLUENCE_CMD read --page-id <PAGE_ID> -o <output_path>
   ```

3. Report the saved file path and byte size.

4. If the user wants to edit, read the file and apply changes with Edit tool.

#### Write ("write")

Use when the user has a ready markdown file to push.

1. Determine the markdown file to upload:
   - If user specified a file path, use it
   - If a file was previously read in this session, offer to use that
   - Otherwise ask the user

2. Run:
   ```bash
   export CONFLUENCE_EMAIL="..." CONFLUENCE_API_TOKEN="..." && \
   $CONFLUENCE_CMD write --page-id <PAGE_ID> --file <file_path>
   ```

3. Report the new version number and URL.

#### Round-trip ("update")

Use when the user wants to modify an existing page — combines read, edit, and write in one flow.

1. **Read**: Download the page as markdown
   ```bash
   $CONFLUENCE_CMD read --page-id <PAGE_ID> -o /tmp/confluence_<PAGE_ID>.md
   ```

2. **Edit**: Ask the user what changes they want. Apply edits to the downloaded markdown file using Read/Edit tools.

3. **Write**: Upload the modified file back
   ```bash
   $CONFLUENCE_CMD write --page-id <PAGE_ID> --file /tmp/confluence_<PAGE_ID>.md
   ```

4. Report completion with version number and URL.

## Macro Placeholder Format

Macros are preserved as HTML comments during read and restored during write.

### Self-closing macros (no body)

| Macro | Placeholder |
|-------|-------------|
| Table of Contents | `<!-- confluence:toc maxLevel="3" -->` |
| Status lozenge | `<!-- confluence:status title="Done" colour="Green" -->` |
| Anchor | `<!-- confluence:anchor -->` |
| Children | `<!-- confluence:children sort="creation" -->` |
| Content by Label | `<!-- confluence:contentbylabel labels="x" spaces="Y" -->` |

### Body macros (with content)

| Macro | Placeholder |
|-------|-------------|
| Expand | `<!-- confluence:expand title="Title" -->content<!-- /confluence:expand -->` |
| Panel | `<!-- confluence:panel title="Title" -->content<!-- /confluence:panel -->` |
| Info / Note / Warning / Tip | `<!-- confluence:info title="Title" -->content<!-- /confluence:info -->` |
| Excerpt | `<!-- confluence:excerpt -->content<!-- /confluence:excerpt -->` |
| Section + Column | `<!-- confluence:section --><!-- confluence:column width="50%" -->content<!-- /confluence:column --><!-- /confluence:section -->` |
| Page Properties | `<!-- confluence:details -->\|Key\|Value\|...<!-- /confluence:details -->` |
| Noformat | `<!-- confluence:noformat -->preformatted text<!-- /confluence:noformat -->` |

### Round-trip tested macros

All macros below have been verified for idempotent read->write->read round-trip:

toc, expand (including nested), panel, info, note, warning, tip, code, status, children, excerpt, anchor, section/column, contentbylabel, details, noformat

### Known limitations

| Limitation | Cause |
|------------|-------|
| `noformat` leading whitespace stripped | MarkdownIt normalizes indentation |
| `contentbylabel` parameter order may change | Confluence API returns parameters in different order |
| `recently-updated` macro causes 500 error | Confluence API does not accept this macro via REST write |
| `detailssummary` (Page Properties Report) crashes renderer | Confluence cannot render this macro when inserted via API |
| Trailing blank lines around placeholders may be removed | Whitespace normalization on read |

## Editing Patterns

When a user asks to add or modify Confluence macros in a markdown file, use the placeholder syntax directly. The placeholders are converted to real Confluence macros on write.

### Adding a Table of Contents

User: "Add a TOC at the top of the page"

Insert at the desired location:
```markdown
<!-- confluence:toc maxLevel="3" -->
```

### Wrapping content in an Expand (collapsible section)

User: "Wrap the details section in a collapsible block"

Before:
```markdown
## Details
Long content here...
```

After:
```markdown
<!-- confluence:expand title="Details" -->
## Details
Long content here...
<!-- /confluence:expand -->
```

### Adding an info/warning/note/tip panel

User: "Add a warning box about the deadline"

```markdown
<!-- confluence:warning title="Deadline" -->
This must be completed by Friday.
<!-- /confluence:warning -->
```

### Adding a status lozenge

User: "Mark this as done"

```markdown
<!-- confluence:status title="DONE" colour="Green" -->
```

Available colours: `Green`, `Yellow`, `Red`, `Blue`, `Grey`.

### Inserting a two-column layout

User: "Split this into two columns"

```markdown
<!-- confluence:section -->
<!-- confluence:column width="50%" -->
Left column content.
<!-- /confluence:column -->
<!-- confluence:column width="50%" -->
Right column content.
<!-- /confluence:column -->
<!-- /confluence:section -->
```

### Adding a task list (checkboxes)

User: "Add a checklist for the review steps"

```markdown
- [ ] Code review completed
- [ ] Tests passing
- [x] Documentation updated
```

### Adding colored text

User: "Make this text red"

```markdown
<!-- confluence:color style="color: rgb(255,0,0)" -->Important text<!-- /confluence:color -->
```

### Inserting a mention

User: "Mention the assignee"

```markdown
<!-- confluence:mention account-id="123456:abcdef" -->
```

### Nesting macros

Placeholders can be nested. For example, an expand inside another expand:

```markdown
<!-- confluence:expand title="Overview" -->
Summary content.
<!-- confluence:expand title="Technical Details" -->
Detailed technical content.
<!-- /confluence:expand -->
<!-- /confluence:expand -->
```

## Error Handling

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set" | Env vars not exported | `source ~/.bashrc` or ask user for credentials |
| HTTP 401 | Token expired or wrong email | Ask user to regenerate token at Atlassian profile |
| HTTP 404 | Wrong page ID | Verify page ID from the Confluence URL |
| HTTP 409 | Version conflict (concurrent edit) | Re-read the page to get latest version, then retry write |
