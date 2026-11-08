import re
from dataclasses import dataclass, field

from juno.core import prompts


@dataclass(frozen=True)
class Tickets:
    prefixes: tuple[str, ...] = ()
    no_ticket_branches: tuple[str, ...] = ("develop", "master", "main", "HEAD")
    pattern: re.Pattern[str] | None = field(init=False, default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.prefixes:
            alternation = "|".join(re.escape(p) for p in self.prefixes)
            compiled = re.compile(r"\b(?:" + alternation + r")-[0-9]+\b", re.I)
            object.__setattr__(self, "pattern", compiled)

    @property
    def enabled(self) -> bool:
        return self.pattern is not None

    def find(self, text: str | None) -> list[str]:
        if self.pattern is None:
            return []
        seen: list[str] = []
        for match in self.pattern.finditer(text or ""):
            key = match.group(0).upper()
            if key not in seen:
                seen.append(key)
        return seen

    def command_tickets(self, text: str | None) -> list[str]:
        head = (text or "").lstrip().split("\n", 1)[0]
        match = prompts.COMMAND_RE.match(head)
        return self.find(head[match.end() :]) if match else []

    def split_dirname(self, name: str) -> tuple[str, str | None]:
        if self.pattern is None:
            return name, None
        alternation = "|".join(re.escape(p) for p in self.prefixes)
        match = re.match(r"^(.+?)[-_]((?:" + alternation + r")-[0-9]+)$", name, re.I)
        if match:
            return match.group(1), match.group(2).upper()
        return name, None

    def branch_ticket(self, branch: str | None) -> str | None:
        if not branch or branch in self.no_ticket_branches:
            return None
        found = self.find(branch)
        return found[0] if found else None
