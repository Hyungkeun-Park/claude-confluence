# claude-confluence

Claude Code skill for reading and writing Confluence pages as markdown with lossless macro preservation.

## Features

- Read Confluence pages as clean markdown
- Write markdown back to Confluence with full macro preservation
- 17 macro types supported with idempotent round-trip (read -> write -> read produces identical output)
- Handles nested macros (e.g., expand inside expand, code inside expand)
- Large file support (100KB+) without timeout

## Installation

### Method 1: Git repo + skill path

Add to your project's `.claude/settings.json`:

```json
{
  "skills": [
    {
      "path": "/path/to/claude-confluence/SKILL.md"
    }
  ]
}
```

Or clone and reference:

```bash
git clone https://github.com/Hyungkeun-Park/claude-confluence.git
```

### Method 2: npm package

```bash
npm install -g claude-confluence
```

### Method 3: pip package

```bash
pip install claude-confluence
```

Then use the CLI directly:

```bash
claude-confluence read --page-id 2251030576 -o page.md
claude-confluence write --page-id 2251030576 --file page.md
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CONFLUENCE_EMAIL` | Yes | Atlassian account email |
| `CONFLUENCE_API_TOKEN` | Yes | API token from [Atlassian](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `CONFLUENCE_BASE_URL` | No | Confluence site URL (default: `nota-dev.atlassian.net`) |

## Supported Macros

| Macro | Type | Round-trip |
|-------|------|------------|
| Table of Contents (toc) | Self-closing | Yes |
| Status lozenge | Self-closing | Yes |
| Anchor | Self-closing | Yes |
| Children | Self-closing | Yes |
| Content by Label | Self-closing | Yes |
| Expand | Body | Yes |
| Panel | Body | Yes |
| Info / Note / Warning / Tip | Body | Yes |
| Code | Body | Yes |
| Excerpt | Body | Yes |
| Section + Column | Body | Yes |
| Page Properties (details) | Body | Yes |
| Noformat | Body | Yes |

## License

MIT
