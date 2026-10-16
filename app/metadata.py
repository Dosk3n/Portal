"""Extract searchable content from HTML, including nested srcdoc and Pi payloads.

Adapted from the supplied HTML report portal.
"""
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re

def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("windows-1252", errors="replace")


class MetadataParser(HTMLParser):
    """Read ordinary HTML, Pi's JSON payloads, and escaped iframe reports."""

    def __init__(self, depth: int = 0):
        super().__init__(convert_charrefs=True)
        self.depth = depth
        self.title_parts: list[str] = []
        self.fragment_title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.body_parts: list[str] = []
        self.in_title = False
        self.in_head = False
        self.svg_depth = 0
        self.active_title_parts: list[str] | None = None
        self.in_heading = False
        self.hidden: list[str] = []
        self.script_id = ""
        self.script_parts: list[str] = []
        self.json_values: dict[str, str] = {}
        self.dates: list[str] = []
        self.description = ""
        self.inner: list[MetadataParser] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "head":
            self.in_head = True
        elif tag == "svg":
            self.svg_depth += 1
        if tag == "title":
            self.in_title = True
            self.active_title_parts = None if self.svg_depth else (self.title_parts if self.in_head else self.fragment_title_parts)
        elif tag == "h1":
            self.in_heading = True
        elif tag in {"script", "style", "noscript"}:
            self.hidden.append(tag)
            if tag == "script":
                self.script_id = values.get("id", "")
                self.script_parts = []
        elif tag == "time" and values.get("datetime"):
            self.dates.append(values["datetime"])
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "")
            if name in {"date", "dcterms.created", "article:published_time", "datepublished"}:
                self.dates.append(content)
            elif name == "description":
                self.description = content
        elif tag == "iframe" and values.get("srcdoc") and self.depth < 5:
            child = MetadataParser(self.depth + 1)
            child.feed(values["srcdoc"])
            child.close()
            self.inner.append(child)

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False
        elif tag == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)
        if tag == "title":
            self.in_title = False
            self.active_title_parts = None
        elif tag == "h1":
            self.in_heading = False
        elif tag in {"script", "style", "noscript"}:
            if tag == "script" and self.script_id in {"markdown-data", "prompt-data", "summary-data"}:
                try:
                    value = json.loads("".join(self.script_parts))
                    if isinstance(value, str):
                        self.json_values[self.script_id] = value
                except (ValueError, TypeError):
                    pass
            if tag in self.hidden:
                self.hidden.remove(tag)
            if tag == "script":
                self.script_id = ""
                self.script_parts = []

    def handle_data(self, data):
        if self.hidden:
            if self.script_id:
                self.script_parts.append(data)
            return
        if self.in_title:
            if self.active_title_parts is not None:
                self.active_title_parts.append(data)
            return
        if self.in_heading:
            self.heading_parts.append(data)
        if data.strip():
            self.body_parts.append(data)

    def result(self):
        children = [child.result() for child in self.inner]
        title = compact(" ".join(self.title_parts or self.fragment_title_parts))
        heading = compact(" ".join(self.heading_parts))
        prompt = compact(self.json_values.get("prompt-data", ""))
        if not title or title.lower().startswith("pi response report"):
            title = prompt[:160] or heading or title
        if children:
            title = next((child["title"] for child in children if child["title"]), title)
        body = compact(" ".join(self.body_parts + [child["body"] for child in children]))
        # Older Pi reports render the response entirely with JavaScript.
        markdown = self.json_values.get("markdown-data", "")
        if markdown:
            body = compact(body + " " + markdown)
        if prompt:
            body = compact(body + " " + prompt)
        description = self.json_values.get("summary-data") or self.description
        return {
            "title": title or heading,
            "body": body,
            "excerpt": compact(description or body)[:320],
            "dates": self.dates + [date for child in children for date in child["dates"]],
        }


