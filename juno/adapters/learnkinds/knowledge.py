import os
from typing import Any

from juno.adapters import cardwrite, repos, store, vaultview
from juno.adapters.learnkinds.base import Ctx, list_md, status_of, today, vault_text
from juno.core import extract, extract_place, extract_prompt, vaultfmt
from juno.core.digest import clip
from juno.core.textchecks import as_list

KEY = "knowledge"
SHAPE = list
LOCKED = True
STATE_RANK = 1


def creatable(ctx: Ctx) -> set[str]:
    return {r for r in ctx.seg.repos if repos.repo_dir(ctx.cfg, r)}


def has_profile(ctx: Ctx, repo: str) -> bool:
    return os.path.isfile(os.path.join(ctx.cfg.vault, "projects", repo + ".md"))


def instructions(ctx: Ctx) -> list[str]:
    exists = {r for r in ctx.seg.repos if has_profile(ctx, r)}
    profiles = extract_prompt.profile_status(list(ctx.seg.repos), exists, creatable(ctx))
    return [
        '- "knowledge": at most 3 {"file", "text", "evidence"}: durable facts verified in this '
        'segment that stay true next month (not task status). "file": "projects/<repo>.md" for '
        f'a gotcha of one repo (profiles: {profiles}) or "knowledge/<slug>.md" for cross-repo '
        'or topic knowledge (a new file also needs "title" and may have "triggers" and '
        '"repos"). "text": one neutral sentence. "evidence": the command result, file:line or '
        "commit in the digest that shows it. Never secrets, customer data, partner or end-user "
        "IDs, emails or phone numbers. Skip what the vault state already says."
    ]


def state(ctx: Ctx) -> str:
    seg = ctx.seg
    can = creatable(ctx)
    fresh = [r for r in seg.repos if r in can and not has_profile(ctx, r)]
    gotchas = {
        r: vaultfmt.section(vault_text(ctx.cfg, f"projects/{r}.md"), "Gotchas") for r in seg.repos
    }
    known = [
        f"knowledge/{n}"
        for n in list_md(ctx.cfg, "knowledge")
        if extract.KNOWLEDGE_RE.match("knowledge/" + n)
    ]
    parts: list[str] = []
    if fresh:
        parts.append(
            "Segment repos without a profile (projects/<repo>.md may be targeted; it is "
            "created): " + ", ".join(fresh)
        )
    for repo, section in gotchas.items():
        if section:
            parts.append(f"projects/{repo}.md Gotchas:\n" + clip(section, 1500))
    parts.append("Knowledge files: " + (", ".join(known) or "-"))
    return "\n\n".join(parts)


def fact_allowed(ctx: Ctx, retired: set[str] | None, rel: str) -> bool:
    profile = extract.PROFILE_RE.match(rel)
    sub, name = ("projects", profile.group(1)) if profile else ("knowledge", rel[10:-3])
    path = cardwrite.safe_path(ctx.cfg, sub, name + ".md")
    if path is None:
        return False
    if os.path.isfile(path):
        return vaultfmt.hidden_reason(rel, store.read_text(path), retired) is None
    if sub == "projects":
        return name in creatable(ctx)
    return not (retired and rel in retired)


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))
    if not items:
        return
    cfg, seg = ctx.cfg, ctx.seg
    retired = vaultview.read_retired(cfg.state)

    def profile_path(name: str) -> str:
        found = repos.repo_dir(
            cfg, next((r for r in seg.repos if r.casefold() == name.casefold()), name)
        )
        home = os.path.expanduser("~")
        return found.replace(home, "~", 1) if found else ""

    texts, written, dropped = extract_place.apply_facts(
        items,
        ctx.digest or "",
        list(seg.repos),
        lambda rel: vault_text(cfg, rel),
        sid8=seg.sid[:8],
        card_files=ctx.card_files,
        today=today(),
        profile_path=profile_path,
        allowed=lambda rel: fact_allowed(ctx, retired, rel),
    )
    created = 0
    for rel, text in texts.items():
        path = os.path.join(cfg.vault, *rel.split("/"))
        created += rel.startswith("projects/") and not os.path.isfile(path)
        store.backup(cfg, path)
        store.write_text(path, text)
    ctx.result.update(knowledge=written, knowledge_dropped=dropped, profiles_created=created)
    sections[KEY] = status_of(len(items), len(written), dropped)
