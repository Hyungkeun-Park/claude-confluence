#!/usr/bin/env python3
"""Confluence page updater with macro preservation and large content support.

Bypasses MCP limitations (timeout on large payloads, macro loss on markdown
round-trip) by calling the Confluence REST API directly.

Usage:
    # Upload markdown file to a Confluence page
    claude-confluence write --page-id 2251030576 --file document.md

    # Read a Confluence page as markdown (with macro placeholders preserved)
    claude-confluence read --page-id 2251030576 -o page.md

Environment variables:
    CONFLUENCE_BASE_URL  : Confluence site URL (default: nota-dev.atlassian.net)
    CONFLUENCE_EMAIL     : User email for API authentication
    CONFLUENCE_API_TOKEN : API token (generate at https://id.atlassian.com/manage-profile/security/api-tokens)
"""

from __future__ import annotations

import argparse
import html as html_mod
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests
from markdown_it import MarkdownIt

MACRO_PLACEHOLDER_OPEN = re.compile(r"<!--\s*confluence:([\w-]+)((?:\s+[\w-]+=\"[^\"]*\")*)\s*-->")
MACRO_PLACEHOLDER_CLOSE = re.compile(r"<!--\s*/confluence:([\w-]+)\s*-->")
MACRO_PARAM_KV = re.compile(r'([\w-]+)="([^"]*)"')

CODE_BLOCK_WITH_LANG = re.compile(
    r'<pre><code class="language-([\w+-]+)">(.*?)</code></pre>',
    re.DOTALL,
)
CODE_BLOCK_PLAIN = re.compile(
    r"<pre><code>(.*?)</code></pre>",
    re.DOTALL,
)

AC_MACRO_OPEN = re.compile(r"<ac:structured-macro[^>]*ac:name=\"([\w-]+)\"[^>]*?(?:/>|>)")
AC_MACRO_SELF_CLOSE = re.compile(r"<ac:structured-macro[^>]*ac:name=\"([\w-]+)\"[^>]*/>")
AC_MACRO_CLOSE_TAG = "</ac:structured-macro>"
AC_PARAM = re.compile(
    r'<ac:parameter ac:name="([\w-]+)">(.*?)</ac:parameter>',
    re.DOTALL,
)
AC_RICH_BODY = re.compile(
    r"<ac:rich-text-body>(.*?)</ac:rich-text-body>",
    re.DOTALL,
)
AC_PLAIN_BODY = re.compile(
    r"<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>",
    re.DOTALL,
)


def _get_config() -> dict[str, str]:
    base_url = os.environ.get("CONFLUENCE_BASE_URL", "nota-dev.atlassian.net")
    email = os.environ.get("CONFLUENCE_EMAIL", "")
    token = os.environ.get("CONFLUENCE_API_TOKEN", "")
    if not email or not token:
        print(
            "Error: CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set.\n"
            "Generate a token at: https://id.atlassian.com/manage-profile/security/api-tokens",
            file=sys.stderr,
        )
        sys.exit(1)
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    return {"base_url": base_url.rstrip("/"), "email": email, "token": token}


def _api_get(config: dict[str, str], path: str, params: dict[str, str] | None = None) -> Any:
    resp = requests.get(
        f"{config['base_url']}{path}",
        params=params or {},
        auth=(config["email"], config["token"]),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _api_put(config: dict[str, str], path: str, payload: dict[str, Any]) -> Any:
    resp = requests.put(
        f"{config['base_url']}{path}",
        json=payload,
        auth=(config["email"], config["token"]),
        headers={"Content-Type": "application/json"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _get_page(config: dict[str, str], page_id: str) -> dict[str, Any]:
    return _api_get(config, f"/wiki/rest/api/content/{page_id}", {"expand": "body.storage,version"})


def _update_page(
    config: dict[str, str],
    page_id: str,
    title: str,
    storage_body: str,
    version: int,
    message: str = "",
) -> dict[str, Any]:
    return _api_put(
        config,
        f"/wiki/rest/api/content/{page_id}",
        {
            "version": {"number": version, "message": message},
            "title": title,
            "type": "page",
            "body": {"storage": {"value": storage_body, "representation": "storage"}},
        },
    )


# ---------------------------------------------------------------------------
# Markdown -> Confluence storage format
# ---------------------------------------------------------------------------


def _html_escape_cdata(text: str) -> str:
    return text.replace("]]>", "]]]]><![CDATA[>")


def _code_blocks_to_ac_macro(html: str) -> str:
    """Convert <pre><code> blocks to Confluence code macros for syntax highlighting."""

    def _replace_lang(m: re.Match) -> str:
        lang = m.group(1)
        code = m.group(2)
        code = code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
        return (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[{_html_escape_cdata(code)}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )

    def _replace_plain(m: re.Match) -> str:
        code = m.group(1)
        code = code.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
        return (
            f'<ac:structured-macro ac:name="code">'
            f"<ac:plain-text-body><![CDATA[{_html_escape_cdata(code)}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )

    html = CODE_BLOCK_WITH_LANG.sub(_replace_lang, html)
    html = CODE_BLOCK_PLAIN.sub(_replace_plain, html)
    return html


PLAIN_TEXT_BODY_MACROS = {"noformat"}
UNSUPPORTED_MACROS = {"detailssummary", "recently-updated"}


def _build_ac_macro(name: str, params: dict[str, str], body_html: str | None) -> str:
    """Build an ac:structured-macro element from name, params, and optional body."""
    macro_open = f'<ac:structured-macro ac:name="{name}" ac:schema-version="1">'
    params_xml = "".join(f'<ac:parameter ac:name="{k}">{v}</ac:parameter>' for k, v in params.items())
    if body_html is not None:
        if name in PLAIN_TEXT_BODY_MACROS:
            plain = re.sub(r"<[^>]+>", "", body_html)
            plain = html_mod.unescape(plain)
            return (
                f"{macro_open}"
                f"{params_xml}"
                f"<ac:plain-text-body><![CDATA[{_html_escape_cdata(plain)}]]></ac:plain-text-body>"
                f"</ac:structured-macro>"
            )
        return (
            f"{macro_open}"
            f"{params_xml}"
            f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
            f"</ac:structured-macro>"
        )
    return f"{macro_open}{params_xml}</ac:structured-macro>"


def _find_innermost_body_placeholder(html: str) -> tuple[int, int, str, dict[str, str], str] | None:
    """Find the innermost body placeholder (one whose body contains no other body placeholders)."""
    best: tuple[int, int, str, dict[str, str], str] | None = None
    for open_m in MACRO_PLACEHOLDER_OPEN.finditer(html):
        name = open_m.group(1)
        close_pattern = re.compile(r"<!--\s*/confluence:" + re.escape(name) + r"\s*-->")
        depth = 0
        reopen_pattern = re.compile(r"<!--\s*confluence:" + re.escape(name) + r"(?:\s+[\w-]+=\"[^\"]*\")*\s*-->")
        pos = open_m.end()
        found_close = False
        while pos < len(html):
            next_open = reopen_pattern.search(html, pos)
            next_close = close_pattern.search(html, pos)
            if next_close is None:
                break
            if next_open and next_open.start() < next_close.start():
                depth += 1
                pos = next_open.end()
            elif depth > 0:
                depth -= 1
                pos = next_close.end()
            else:
                body = html[open_m.end() : next_close.start()].strip()
                has_nested_body = bool(re.search(
                    r"<!--\s*confluence:[\w-]+(?:\s+[\w-]+=\"[^\"]*\")*\s*-->.*?<!--\s*/confluence:[\w-]+\s*-->",
                    body,
                    re.DOTALL,
                ))
                if not has_nested_body:
                    params = dict(MACRO_PARAM_KV.findall(open_m.group(2)))
                    return (open_m.start(), next_close.end(), name, params, body)
                found_close = True
                break
        if not found_close:
            continue
    return best


def _macro_placeholders_to_ac(html: str) -> str:
    """Convert <!-- confluence:xxx --> placeholders to <ac:structured-macro> elements."""
    while True:
        placeholder = _find_innermost_body_placeholder(html)
        if placeholder is None:
            break
        start, end, name, params, body_html = placeholder
        if name in UNSUPPORTED_MACROS:
            html = html[:start] + html[end:]
            continue
        macro = _build_ac_macro(name, params, body_html)
        html = html[:start] + macro + html[end:]

    def _replace_self(m: re.Match) -> str:
        name = m.group(1)
        if name in UNSUPPORTED_MACROS:
            return ""
        params = dict(MACRO_PARAM_KV.findall(m.group(2)))
        return _build_ac_macro(name, params, None)

    html = MACRO_PLACEHOLDER_OPEN.sub(_replace_self, html)

    html = re.sub(
        r"<p>\s*(<ac:structured-macro[^>]*>.*?</ac:structured-macro>)\s*</p>",
        r"\1",
        html,
        flags=re.DOTALL,
    )
    return html


def _escape_bare_brackets(html: str) -> str:
    """Escape bare [...] that are not markdown links to prevent Confluence auto-linking."""
    parts: list[str] = []
    last_end = 0
    for m in re.finditer(r"<code>(.*?)</code>|<ac:structured-macro.*?</ac:structured-macro>", html, re.DOTALL):
        parts.append(_escape_bare_brackets_segment(html[last_end : m.start()]))
        parts.append(m.group(0))
        last_end = m.end()
    parts.append(_escape_bare_brackets_segment(html[last_end:]))
    return "".join(parts)


def _escape_bare_brackets_segment(segment: str) -> str:
    """Escape [...] not followed by (...) in a non-code segment."""
    return re.sub(
        r"\[([^\]]*)\](?!\()",
        lambda m: f"&#91;{m.group(1)}&#93;",
        segment,
    )


def _color_placeholders_to_ac(html: str) -> str:
    """Convert <!-- confluence:color --> placeholders back to <span style="...">."""

    def _replace(m: re.Match) -> str:
        style = m.group(1)
        content = m.group(2)
        return f'<span style="{style}">{content}</span>'

    return re.sub(
        r'<!--\s*confluence:color\s+style="([^"]*)"\s*-->(.*?)<!--\s*/confluence:color\s*-->',
        _replace,
        html,
        flags=re.DOTALL,
    )


def _image_placeholders_to_ac(html: str) -> str:
    """Convert image placeholders and <img> tags back to ac:image elements."""
    IMAGE_PLACEHOLDER = re.compile(
        r"<!--\s*confluence:image((?:\s+[\w-]+=\"[^\"]*\")*)\s*-->\s*"
        r"(?:<p>\s*)?<img\s+src=\"([^\"]*)\"[^/]*/>\s*(?:</p>\s*)?"
        r"<!--\s*/confluence:image\s*-->",
        re.DOTALL,
    )

    def _replace(m: re.Match) -> str:
        attrs_str = m.group(1)
        filename = m.group(2)
        attrs = dict(MACRO_PARAM_KV.findall(attrs_str))
        attr_xml = "".join(f' ac:{k}="{v}"' for k, v in attrs.items())
        return f'<ac:image{attr_xml}><ri:attachment ri:filename="{filename}" /></ac:image>'

    html = IMAGE_PLACEHOLDER.sub(_replace, html)

    def _replace_external(m: re.Match) -> str:
        src = m.group(1)
        alt = m.group(2) or ""
        if src.startswith("http"):
            return f'<ac:image ac:alt="{alt}"><ri:url ri:value="{src}" /></ac:image>'
        return m.group(0)

    html = re.sub(r'<p>\s*<img\s+src="([^"]*)"(?:\s+alt="([^"]*)")?\s*/>\s*</p>', _replace_external, html)
    return html


def _emoticon_placeholders_to_ac(html: str) -> str:
    """Convert <!-- confluence:emoticon name="smile" --> back to ac:emoticon."""

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        return f'<ac:emoticon ac:name="{name}" />'

    return re.sub(r'<!--\s*confluence:emoticon\s+name="([^"]*)"\s*-->', _replace, html)


def _mention_placeholders_to_ac(html: str) -> str:
    """Convert <!-- confluence:mention ... --> back to ac:link with ri:user."""

    def _replace(m: re.Match) -> str:
        attrs_str = m.group(1)
        attrs = dict(MACRO_PARAM_KV.findall(attrs_str))
        if "account-id" in attrs:
            return f'<ac:link><ri:user ri:account-id="{attrs["account-id"]}" /></ac:link>'
        elif "username" in attrs:
            return f'<ac:link><ri:user ri:username="{attrs["username"]}" /></ac:link>'
        elif "userkey" in attrs:
            return f'<ac:link><ri:user ri:userkey="{attrs["userkey"]}" /></ac:link>'
        return ""

    return re.sub(
        r"<!--\s*confluence:mention((?:\s+[\w-]+=\"[^\"]*\")*)\s*-->",
        _replace,
        html,
    )


def _task_list_placeholders_to_ac(html: str) -> str:
    """Convert markdown checkboxes (rendered as <li>[x] text</li>) back to ac:task-list."""
    CHECKBOX_LIST = re.compile(
        r"<ul>\s*((?:<li>\s*\[[ x]\].*?</li>\s*)+)</ul>",
        re.DOTALL,
    )

    def _replace_list(m: re.Match) -> str:
        items_html = m.group(1)
        tasks: list[str] = []
        for item_m in re.finditer(r"<li>\s*\[( |x)\]\s*(.*?)</li>", items_html, re.DOTALL):
            status = "complete" if item_m.group(1) == "x" else "incomplete"
            body = item_m.group(2).strip()
            tasks.append(
                f"<ac:task><ac:task-status>{status}</ac:task-status>"
                f"<ac:task-body>{body}</ac:task-body></ac:task>"
            )
        if not tasks:
            return m.group(0)
        return "<ac:task-list>" + "".join(tasks) + "</ac:task-list>"

    return CHECKBOX_LIST.sub(_replace_list, html)


def markdown_to_storage(markdown_text: str) -> str:
    """Convert markdown with optional macro placeholders to Confluence storage format."""
    md = MarkdownIt("commonmark", {"html": True})
    md.enable("table")
    html = md.render(markdown_text)
    html = _code_blocks_to_ac_macro(html)
    html = _task_list_placeholders_to_ac(html)
    html = _escape_bare_brackets(html)
    html = _color_placeholders_to_ac(html)
    html = _image_placeholders_to_ac(html)
    html = _emoticon_placeholders_to_ac(html)
    html = _mention_placeholders_to_ac(html)
    html = _macro_placeholders_to_ac(html)
    html = re.sub(r"</?(?:thead|tbody)>\n?", "", html)
    return html


# ---------------------------------------------------------------------------
# Confluence storage format -> Markdown (with macro placeholders)
# ---------------------------------------------------------------------------


def _find_ac_macros(storage_html: str) -> list[tuple[int, int, str, str]]:
    """Find top-level ac:structured-macro elements tracking nesting depth.

    Returns:
        List of (start, end, macro_name, inner_content) tuples.
    """
    results: list[tuple[int, int, str, str]] = []
    search_start = 0
    while True:
        self_close = AC_MACRO_SELF_CLOSE.search(storage_html, search_start)
        open_match = AC_MACRO_OPEN.search(storage_html, search_start)
        if not open_match:
            break
        if self_close and self_close.start() == open_match.start() and self_close.group(0).endswith("/>"):
            results.append((self_close.start(), self_close.end(), self_close.group(1), ""))
            search_start = self_close.end()
            continue
        macro_name = open_match.group(1)
        outer_start = open_match.start()
        inner_start = open_match.end()
        depth = 1
        pos = inner_start
        while depth > 0 and pos < len(storage_html):
            next_self = AC_MACRO_SELF_CLOSE.search(storage_html, pos)
            next_open = AC_MACRO_OPEN.search(storage_html, pos)
            close_idx = storage_html.find(AC_MACRO_CLOSE_TAG, pos)
            if close_idx == -1:
                break
            if next_self and next_self.start() < close_idx and (not next_open or next_self.start() <= next_open.start()):
                pos = next_self.end()
                continue
            if next_open and next_open.start() < close_idx:
                depth += 1
                pos = next_open.end()
            else:
                depth -= 1
                if depth == 0:
                    inner = storage_html[inner_start:close_idx]
                    outer_end = close_idx + len(AC_MACRO_CLOSE_TAG)
                    results.append((outer_start, outer_end, macro_name, inner))
                    search_start = outer_end
                else:
                    pos = close_idx + len(AC_MACRO_CLOSE_TAG)
        if depth > 0:
            search_start = open_match.end()
    return results


AUTO_GENERATED_PARAMS = {"cql"}


def _extract_own_params(inner: str) -> dict[str, str]:
    """Extract only direct-child ac:parameter elements, ignoring nested macro params."""
    rich_start = inner.find("<ac:rich-text-body>")
    plain_start = inner.find("<ac:plain-text-body>")
    param_region = inner
    if rich_start != -1:
        param_region = inner[:rich_start]
    elif plain_start != -1:
        param_region = inner[:plain_start]
    params: dict[str, str] = {}
    for pm in AC_PARAM.finditer(param_region):
        if pm.group(1) not in AUTO_GENERATED_PARAMS:
            params[pm.group(1)] = pm.group(2)
    return params


def _extract_outermost_body(inner: str, open_tag: str, close_tag: str) -> str | None:
    """Extract content of the outermost body element, handling nesting."""
    start = inner.find(open_tag)
    if start == -1:
        return None
    content_start = start + len(open_tag)
    depth = 1
    pos = content_start
    while depth > 0 and pos < len(inner):
        next_open = inner.find(open_tag, pos)
        next_close = inner.find(close_tag, pos)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + len(open_tag)
        else:
            depth -= 1
            if depth == 0:
                return inner[content_start:next_close]
            pos = next_close + len(close_tag)
    return None


def _convert_single_macro(name: str, inner: str) -> str:
    """Convert a single ac:structured-macro's inner content to markdown/placeholder."""
    if name in UNSUPPORTED_MACROS:
        return ""

    params = _extract_own_params(inner)
    param_str = "".join(f' {k}="{v}"' for k, v in params.items())

    plain_body = AC_PLAIN_BODY.search(inner)

    if name == "code":
        lang = params.get("language", "")
        code_content = plain_body.group(1) if plain_body else ""
        code_content = code_content.strip("\n")
        lang_hint = f"```{lang}" if lang else "```"
        return f"\n\n{lang_hint}\n{code_content}\n```\n\n"

    if name in PLAIN_TEXT_BODY_MACROS and plain_body:
        content = plain_body.group(1)
        return f"<!-- confluence:{name}{param_str} -->\n{content}\n<!-- /confluence:{name} -->"

    rich_body = _extract_outermost_body(inner, "<ac:rich-text-body>", "</ac:rich-text-body>")
    if rich_body is not None:
        return f"<!-- confluence:{name}{param_str} -->\n{rich_body.strip()}\n<!-- /confluence:{name} -->"

    return f"<!-- confluence:{name}{param_str} -->"


def _convert_colors(storage_html: str) -> str:
    """Convert <span style="color: ..."> elements to placeholders."""

    def _replace_color(m: re.Match) -> str:
        style = m.group(1)
        content = m.group(2)
        return f'<!-- confluence:color style="{style}" -->{content}<!-- /confluence:color -->'

    return re.sub(
        r'<span\s+style="(color:\s*[^"]*)">(.*?)</span>',
        _replace_color,
        storage_html,
        flags=re.DOTALL,
    )


def _convert_images(storage_html: str) -> str:
    """Convert <ac:image> elements to markdown images or placeholders."""

    def _replace_image(m: re.Match) -> str:
        tag = m.group(0)
        attrs: dict[str, str] = {}
        for attr_m in re.finditer(r'(ac:[\w-]+)="([^"]*)"', tag):
            key = attr_m.group(1).replace("ac:", "")
            attrs[key] = attr_m.group(2)
        attachment = re.search(r'ri:filename="([^"]*)"', tag)
        url = re.search(r'ri:value="([^"]*)"', tag)
        if attachment:
            filename = attachment.group(1)
            alt = attrs.get("alt", filename)
            attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
            return f"<!-- confluence:image{attr_str} -->\n![{alt}]({filename})\n<!-- /confluence:image -->"
        elif url:
            src = url.group(1)
            alt = attrs.get("alt", "")
            return f"![{alt}]({src})"
        return ""

    return re.sub(r"<ac:image[^>]*>.*?</ac:image>", _replace_image, storage_html, flags=re.DOTALL)


def _convert_emoticons(storage_html: str) -> str:
    """Convert <ac:emoticon> elements to placeholders."""

    def _replace_emoticon(m: re.Match) -> str:
        name_m = re.search(r'ac:name="([^"]*)"', m.group(0))
        if name_m:
            return f'<!-- confluence:emoticon name="{name_m.group(1)}" -->'
        return ""

    return re.sub(r"<ac:emoticon[^/]*/\s*>", _replace_emoticon, storage_html)


def _convert_mentions(storage_html: str) -> str:
    """Convert <ac:link><ri:user .../></ac:link> elements to placeholders."""

    def _replace_mention(m: re.Match) -> str:
        tag = m.group(0)
        account_id = re.search(r'ri:account-id="([^"]*)"', tag)
        username = re.search(r'ri:username="([^"]*)"', tag)
        userkey = re.search(r'ri:userkey="([^"]*)"', tag)
        if account_id:
            return f'<!-- confluence:mention account-id="{account_id.group(1)}" -->'
        elif username:
            return f'<!-- confluence:mention username="{username.group(1)}" -->'
        elif userkey:
            return f'<!-- confluence:mention userkey="{userkey.group(1)}" -->'
        return ""

    return re.sub(
        r"<ac:link>\s*<ri:user[^/]*/>\s*(?:<ac:link-body>.*?</ac:link-body>\s*)?</ac:link>",
        _replace_mention,
        storage_html,
        flags=re.DOTALL,
    )


def _convert_task_lists(storage_html: str) -> str:
    """Convert <ac:task-list> elements to markdown checkboxes."""

    def _replace_task_list(m: re.Match) -> str:
        task_list_html = m.group(0)
        tasks = re.findall(r"<ac:task>(.*?)</ac:task>", task_list_html, re.DOTALL)
        lines: list[str] = []
        for task_html in tasks:
            status_m = re.search(r"<ac:task-status>(.*?)</ac:task-status>", task_html)
            body_m = re.search(r"<ac:task-body>(.*?)</ac:task-body>", task_html, re.DOTALL)
            checked = status_m and status_m.group(1).strip() == "complete"
            body = ""
            if body_m:
                body = _inline_markup(body_m.group(1))
                body = re.sub(r"<[^>]+>", "", body).strip()
            checkbox = "[x]" if checked else "[ ]"
            lines.append(f"- {checkbox} {body}")
        return "\n" + "\n".join(lines) + "\n"

    return re.sub(r"<ac:task-list>.*?</ac:task-list>", _replace_task_list, storage_html, flags=re.DOTALL)


def _ac_macro_to_placeholder(storage_html: str) -> str:
    """Convert <ac:structured-macro> elements to <!-- confluence:xxx --> placeholders."""
    while True:
        macros = _find_ac_macros(storage_html)
        if not macros:
            break
        for start, end, name, inner in reversed(macros):
            replacement = _convert_single_macro(name, inner)
            storage_html = storage_html[:start] + replacement + storage_html[end:]
    return storage_html


def _strip_tags(html: str) -> str:
    """Remove all remaining HTML tags, keeping inner text."""
    return re.sub(r"<[^>]+>", "", html)


def _inline_markup(html: str) -> str:
    """Convert inline HTML elements to markdown."""
    text = html
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    text = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)
    text = re.sub(
        r"<ac:link>\s*<ri:page[^/]*/>\s*<ac:link-body>(.*?)</ac:link-body>\s*</ac:link>",
        r"\1",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"<br\s*/?>", "\n", text)
    return text


def _cell_text(cell_html: str) -> str:
    """Extract clean text from a table cell."""
    text = _inline_markup(cell_html)
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\1", text, flags=re.DOTALL)
    text = _strip_tags(text)
    text = text.replace("|", "\\|").strip()
    text = re.sub(r"\s*\n\s*", " ", text)
    return text


def _convert_tables(html: str) -> str:
    """Convert HTML tables to markdown tables."""

    def _table_to_md(m: re.Match) -> str:
        table_html = m.group(0)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
        if not rows:
            return ""

        md_rows: list[list[str]] = []
        for row_html in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL)
            md_rows.append([_cell_text(c) for c in cells])

        if not md_rows:
            return ""

        num_cols = max(len(r) for r in md_rows)
        for r in md_rows:
            while len(r) < num_cols:
                r.append("")

        lines: list[str] = []
        lines.append("| " + " | ".join(md_rows[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in range(num_cols)) + " |")
        for row in md_rows[1:]:
            lines.append("| " + " | ".join(row) + " |")

        return "\n" + "\n".join(lines) + "\n\n"

    return re.sub(r"<table[^>]*>.*?</table>", _table_to_md, html, flags=re.DOTALL)


def _convert_lists(html: str) -> str:
    """Convert HTML lists to markdown lists."""

    def _list_to_md(m: re.Match) -> str:
        list_html = m.group(0)
        is_ordered = m.group(0).startswith("<ol")
        items = re.findall(r"<li[^>]*>(.*?)</li>", list_html, re.DOTALL)
        lines: list[str] = []
        for i, item_html in enumerate(items):
            text = _inline_markup(item_html)
            text = re.sub(r"<p[^>]*>(.*?)</p>", r"\1", text, flags=re.DOTALL)
            text = _strip_tags(text).strip()
            text = re.sub(r"\s*\n\s*", " ", text)
            prefix = f"{i + 1}. " if is_ordered else "* "
            lines.append(f"{prefix}{text}")
        return "\n" + "\n".join(lines) + "\n\n"

    html = re.sub(r"<[ou]l[^>]*>.*?</[ou]l>", _list_to_md, html, flags=re.DOTALL)
    return html


def storage_to_markdown(storage_html: str) -> str:
    """Convert Confluence storage format to markdown with macro placeholders."""
    text = storage_html

    # 1. Images, emoticons, mentions, task lists (before macro processing)
    text = _convert_colors(text)
    text = _convert_images(text)
    text = _convert_emoticons(text)
    text = _convert_mentions(text)
    text = _convert_task_lists(text)

    # 2. Code macros -> fenced blocks
    text = _ac_macro_to_placeholder(text)

    # 3. Tables -> markdown tables
    text = _convert_tables(text)

    # 4. Lists -> markdown lists
    text = _convert_lists(text)

    # 5. Block-level elements
    text = re.sub(
        r"<h([1-6])[^>]*>(.*?)</h\1>",
        lambda m: f"\n{'#' * int(m.group(1))} {_inline_markup(m.group(2))}\n",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>",
        lambda m: "\n"
        + "\n".join("> " + line for line in _strip_tags(_inline_markup(m.group(1))).strip().split("\n"))
        + "\n",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"<hr\s*/?>", "\n---\n", text)

    # 6. Paragraphs
    text = re.sub(r"<p[^>]*>(.*?)</p>", lambda m: _inline_markup(m.group(1)) + "\n\n", text, flags=re.DOTALL)

    # 7. Inline formatting for any remaining content
    text = _inline_markup(text)

    # 8. Protect code blocks and macro placeholders, then strip remaining HTML tags
    protected_blocks: list[str] = []

    def _save_block(m: re.Match) -> str:
        protected_blocks.append(m.group(0))
        return f"__PROTECTED_{len(protected_blocks) - 1}__"

    text = re.sub(r"```[\w]*\n.*?\n```", _save_block, text, flags=re.DOTALL)

    def _protect_placeholders(t: str) -> str:
        """Protect macro placeholders from strip_tags, handling nesting via depth tracking."""
        while True:
            open_m = MACRO_PLACEHOLDER_OPEN.search(t)
            if not open_m:
                break
            name = open_m.group(1)
            close_pat = re.compile(r"<!--\s*/confluence:" + re.escape(name) + r"\s*-->")
            reopen_pat = re.compile(
                r"<!--\s*confluence:" + re.escape(name) + r"(?:\s+[\w-]+=\"[^\"]*\")*\s*-->"
            )
            pos = open_m.end()
            depth = 0
            matched_close = None
            while pos < len(t):
                next_open = reopen_pat.search(t, pos)
                next_close = close_pat.search(t, pos)
                if next_close is None:
                    break
                if next_open and next_open.start() < next_close.start():
                    depth += 1
                    pos = next_open.end()
                elif depth > 0:
                    depth -= 1
                    pos = next_close.end()
                else:
                    matched_close = next_close
                    break
            if matched_close:
                full = t[open_m.start() : matched_close.end()]
                protected_blocks.append(full)
                placeholder = f"__PROTECTED_{len(protected_blocks) - 1}__"
                t = t[: open_m.start()] + placeholder + t[matched_close.end() :]
            else:
                protected_blocks.append(open_m.group(0))
                placeholder = f"__PROTECTED_{len(protected_blocks) - 1}__"
                t = t[: open_m.start()] + placeholder + t[open_m.end() :]
        return t

    text = _protect_placeholders(text)
    text = _strip_tags(text)
    for i in range(len(protected_blocks) - 1, -1, -1):
        text = text.replace(f"__PROTECTED_{i}__", protected_blocks[i])

    # 9. Decode HTML entities
    text = html_mod.unescape(text)

    # 10. Normalize em-dash table separators to standard markdown hyphens
    def _fix_separator_row(m: re.Match) -> str:
        return re.sub(r"—", "---", m.group(0))

    text = re.sub(
        r"^\|[\s—|]+\|$",
        _fix_separator_row,
        text,
        flags=re.MULTILINE,
    )

    # 11. Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip() + "\n"
    return text


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_write(args: argparse.Namespace) -> None:
    config = _get_config()

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"Error: File not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    markdown_text = md_path.read_text(encoding="utf-8")
    print(f"Read {len(markdown_text):,} bytes from {md_path}")

    page = _get_page(config, args.page_id)
    current_version = page["version"]["number"]
    title = args.title or page["title"]

    storage_html = markdown_to_storage(markdown_text)
    print(f"Converted to storage format: {len(storage_html):,} bytes")

    message = args.message or f"Updated from {md_path.name}"
    result = _update_page(config, args.page_id, title, storage_html, current_version + 1, message)

    print(f"Updated: {result['title']} (v{result['version']['number']})")
    print(f"URL: {config['base_url']}/wiki{result['_links']['webui']}")


def cmd_read(args: argparse.Namespace) -> None:
    config = _get_config()

    page = _get_page(config, args.page_id)
    storage_html = page["body"]["storage"]["value"]

    markdown_text = storage_to_markdown(storage_html)

    if args.output:
        Path(args.output).write_text(markdown_text, encoding="utf-8")
        print(f"Written {len(markdown_text):,} bytes to {args.output}")
    else:
        print(markdown_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Confluence page tool with macro preservation")
    sub = parser.add_subparsers(dest="command", required=True)

    wp = sub.add_parser("write", help="Upload markdown to Confluence page")
    wp.add_argument("--page-id", required=True, help="Confluence page ID")
    wp.add_argument("--file", required=True, help="Markdown file path")
    wp.add_argument("--title", help="Page title (default: keep existing)")
    wp.add_argument("--message", help="Version message")
    wp.set_defaults(func=cmd_write)

    rp = sub.add_parser("read", help="Download Confluence page as markdown")
    rp.add_argument("--page-id", required=True, help="Confluence page ID")
    rp.add_argument("-o", "--output", help="Output file path")
    rp.set_defaults(func=cmd_read)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
