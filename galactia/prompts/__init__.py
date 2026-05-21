from __future__ import annotations

from importlib import resources


def load_prompt(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def render_prompt(name: str, **values) -> str:
    rendered = load_prompt(name)
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", "" if value is None else str(value))
    return rendered
