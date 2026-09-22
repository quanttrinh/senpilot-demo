"""Rendering for PEP 750 template strings (t-strings)."""

from string.templatelib import Template


def render(template: Template) -> str:
    """Render a t-string ``Template`` into its plain string form."""
    parts: list[str] = []
    for item in template:
        if isinstance(item, str):
            parts.append(item)
            continue
        value = item.value
        if item.conversion == "r":
            value = repr(value)
        elif item.conversion == "a":
            value = ascii(value)
        elif item.conversion == "s":
            value = str(value)
        parts.append(format(value, item.format_spec))
    return "".join(parts)
