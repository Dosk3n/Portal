"""Deployment settings shared by the server and public-link store."""
import os
from urllib.parse import urlsplit


def canonical_origin(value: str) -> str:
    """Require an HTTP(S) origin, without credentials or a URL prefix."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or any(character.isspace() for character in value)
                or '\\' in value):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('PUBLIC_BASE_URL must be an HTTP(S) origin such as https://reports.example.com.') from None
    hostname = parsed.hostname.lower()
    if ':' in hostname:
        hostname = f'[{hostname}]'
    default_port = 80 if parsed.scheme == 'http' else 443
    suffix = f':{port}' if port is not None and port != default_port else ''
    return f'{parsed.scheme}://{hostname}{suffix}'


def public_base_url(value: str | None = None) -> str:
    return canonical_origin(value if value is not None else os.environ.get('PUBLIC_BASE_URL', 'http://localhost:8769'))
