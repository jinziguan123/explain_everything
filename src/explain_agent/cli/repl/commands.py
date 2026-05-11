from dataclasses import dataclass


class SlashCommandError(ValueError):
    pass


@dataclass
class SlashCommand:
    name: str
    arg: str


_ALIASES = {"exit": "quit"}
_KNOWN = {"new", "sessions", "load", "clear", "help", "quit"}


def parse_slash_command(raw: str) -> SlashCommand:
    line = raw.strip()
    if not line.startswith("/"):
        raise SlashCommandError(f"not a slash command: {raw}")
    body = line[1:].strip()
    if not body:
        raise SlashCommandError("empty slash command")
    parts = body.split(maxsplit=1)
    name = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    name = _ALIASES.get(name, name)
    if name not in _KNOWN:
        raise SlashCommandError(f"unknown command: /{name}")
    return SlashCommand(name=name, arg=arg)
