#!/usr/bin/env python3
"""Thin wrapper for git-repo-based skill usage.

When this plugin is installed as a git repo + skill path, the SKILL.md
references this script. It delegates to the package under src/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_confluence.confluence_page import main

main()
