"""HTML sanitising for anything that came from an email.

Mail bodies are hostile input. They reach the GUI only after:

  1. nh3 (Ammonia) strips scripts, styles, event handlers, forms and frames;
  2. every ``cid:`` image is rewritten to a same-origin attachment URL;
  3. every remaining remote image/media reference is removed, which kills
     tracking pixels and stops the reader's IP leaking to the sender.

The frontend renders the result inside a strict CSP that forbids inline
script anyway, so this is defence in depth rather than the only line.
"""

from __future__ import annotations

import re

import nh3

ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "dd", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "li", "ol", "p", "pre", "q", "s", "small", "span", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}

# "rel" is absent on purpose: nh3 sets it itself via link_rel, and declaring
# both makes it panic. "class" is absent so mail CSS classes cannot collide
# with the app's own styles.
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "table": {"border", "cellpadding", "cellspacing"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "cid"}

_CID_SRC_RE = re.compile(r'src\s*=\s*["\']cid:([^"\']+)["\']', re.IGNORECASE)
_REMOTE_IMG_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*[\"']https?://[^\"']*[\"'][^>]*>", re.IGNORECASE
)


def clean_html(html: str) -> str:
    """Run the sanitiser. Never skip this on mail-sourced HTML."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


def rewrite_inline_images(html: str, cid_to_url: dict[str, str]) -> str:
    """Point ``cid:`` references at our own attachment endpoint.

    A cid we have no attachment for is dropped rather than left dangling.
    """

    def _replace(match: re.Match[str]) -> str:
        cid = match.group(1).strip().strip("<>")
        url = cid_to_url.get(cid) or cid_to_url.get(f"<{cid}>")
        if not url:
            return 'src="" data-cid-missing="1"'
        return f'src="{url}"'

    return _CID_SRC_RE.sub(_replace, html or "")


def strip_remote_media(html: str) -> tuple[str, bool]:
    """Remove externally hosted images. Returns (html, something_was_removed)."""
    if not html:
        return "", False
    cleaned, count = _REMOTE_IMG_RE.subn("", html)
    return cleaned, count > 0


def prepare_email_html(html: str, cid_to_url: dict[str, str]) -> tuple[str, bool]:
    """Full pipeline for an inbound HTML body.

    Order matters: sanitise first so the regex passes only ever see markup nh3
    has already normalised.
    """
    safe = clean_html(html)
    safe = rewrite_inline_images(safe, cid_to_url)
    safe, blocked = strip_remote_media(safe)
    return safe, blocked


def text_to_html(text: str) -> str:
    """Render a plain-text body as safe HTML, preserving line breaks."""
    escaped = nh3.clean(text or "", tags=set(), attributes={})
    return "<p>" + escaped.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
