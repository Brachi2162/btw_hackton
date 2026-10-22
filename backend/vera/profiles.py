"""Form-type profiles (spec 4, iron rule 5): what differs between one
sender's form and another's lives in config/profiles/*.yaml, not in code.

A page's profile is picked from the decided sender and the page kind. Today
a profile says what the sender's commitment number looks like: a pattern,
and where on the page it is printed. A page with no matching profile is
`_generic`: the general rules only.

Profiles describe the form's structure (formats, places), never values
from the sample forms.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GENERIC = "_generic"


@dataclass
class Profile:
    id: str
    name: str
    senders: list[str]  # sender ids from config/senders.yaml
    page_kinds: list[str] = field(default_factory=lambda: ["form"])
    # The sender's own commitment-number pattern, and the coarse regions
    # (as the vision model names them) where it is printed.
    commitment_regex: str | None = None
    commitment_regions: list[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self):
        if self.commitment_regex:
            re.compile(self.commitment_regex)  # a typo fails at load, not mid-batch

    def matches(self, sender_id: str | None, page_kind: str) -> bool:
        return sender_id in self.senders and page_kind in self.page_kinds


def load_profiles(config_dir: Path) -> list[Profile]:
    out = []
    for path in sorted((Path(config_dir) / "profiles").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.append(Profile(**data))
    ids = [p.id for p in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate profile ids: {sorted(dupes)}")
    return out


def pick(
    profiles: list[Profile], sender_id: str | None, page_kind: str
) -> Profile | None:
    """The first profile for this sender and page kind, or None (generic)."""
    return next((p for p in profiles if p.matches(sender_id, page_kind)), None)
