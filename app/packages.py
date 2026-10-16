"""Validate static local dependencies without fetching remote resources."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from metadata import read_text


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.base = ''
        self.styles = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'style': self.in_style = True
        if attrs.get('style'): self.styles.append(attrs['style'])
        if tag == 'base':
            self.base = attrs.get('href', '')
        if tag in {'img', 'script', 'iframe', 'audio', 'video', 'source', 'track', 'embed', 'input'}:
            self.links.append(attrs.get('src', ''))
        if tag == 'video':
            self.links.append(attrs.get('poster', ''))
        if tag == 'object':
            self.links.append(attrs.get('data', ''))
        if tag == 'link' and set(attrs.get('rel', '').lower().split()) & {'stylesheet', 'icon', 'preload', 'modulepreload'}:
            self.links.append(attrs.get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'style': self.in_style = False

    def handle_data(self, data):
        if self.in_style: self.styles.append(data)


def validate_package(root, entry):
    root = Path(root).resolve()
    source = root / entry
    if not source.is_file():
        raise FileNotFoundError('The report entry file is unavailable. Restore it before sharing.')
    if not read_text(source).strip():
        raise ValueError('The report HTML is empty. Add content before importing.')
    missing = set()
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in {'.html', '.htm', '.xhtml', '.css'}:
            continue
        text = read_text(path)
        base = ''
        links = []
        if path.suffix.lower() != '.css':
            parser = References()
            parser.feed(text)
            base = parser.base
            links.extend(parser.links)
            text = '\n'.join(parser.styles)
        # CSS URLs in stylesheets and inline styles. Skip data and remote URLs.
        links.extend(m.group(2) for m in re.finditer(r'url\(\s*([\'"]?)(.*?)\1\s*\)', text, re.I))
        if path.suffix.lower() == '.css':
            links.extend(re.findall(r'@import\s+[\'"]([^\'"]+)', text, re.I))
        for link in links:
            link = link.strip()
            if not link or link.startswith('#'):
                continue
            url = urlsplit(urljoin(base, link))
            if url.scheme or url.netloc:
                continue
            relative = unquote(url.path)
            if not relative:
                continue
            target = (path.parent / relative).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                missing.add(link)
    if missing:
        names = ', '.join(sorted(missing)[:8])
        raise ValueError('Report has missing local assets: ' + names + '. Include these files in the ZIP using relative paths.')
