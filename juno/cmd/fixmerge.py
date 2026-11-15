import argparse

from juno import config
from juno.adapters import gate
from juno.cmd import fix, forget, gateio, write
from juno.core import consolidate, writegate
from juno.core.errors import Refused


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    """`fix --merge`: several lines of one section become one line, as one write."""
    ref, text_arg = (args.ref, args.text) if args.text is not None else (None, args.ref)
    if ref is not None or text_arg is None:
        raise Refused('--merge takes no <file>:<line> argument: fix --merge F:a,b --was .. "text"')
    rel, numbers = consolidate.split_merge_ref(args.merge)
    if len(args.was) != len(numbers):
        raise Refused(f"give one --was per merged line ({len(numbers)}), in the order of the lines")
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    text = gate.scan_text(cfg, text_arg, "line")
    if "\n" in text or len(text) > fix.NEW_MAX:
        raise Refused(f"the merged line must be one line of at most {fix.NEW_MAX} characters")
    with write.locked(cfg):
        lines: list[str] = []
        for number, was in zip(numbers, args.was, strict=True):
            _, lines, _ = fix.locate(cfg, f"{rel}:{number}", was, forget=True)
        heads = {consolidate.section_of(lines, n - 1) for n in numbers}
        if len(heads) > 1:
            raise Refused("the lines are not in the same section of the file")
        first = min(numbers) - 1
        gone = [lines[n - 1] for n in sorted(numbers)]
        bullet = lines[first].startswith("- ") and not text.startswith("- ")
        lines[first] = ("- " if bullet else "") + text
        for n in sorted(numbers, reverse=True):
            if n - 1 != first:
                del lines[n - 1]
        res = gate.commit(cfg, seg, "merge", rel, writegate.join(lines), evidence)
        day = write.today()
        archive = forget.ARCHIVE.format(month=day[:7])
        kept = [f"{old} · forgotten {day} · from {rel} · {res.id}" for old in gone]
        old_archive = gate.read(cfg, archive) or "# Forgotten lines\n\n"
        gate.commit(
            cfg,
            seg,
            "merge-archive",
            archive,
            writegate.join([*old_archive.splitlines(), *kept]),
            evidence,
            extra={"link": res.id},
            run_cap=False,
        )
    return gateio.report(res, f"{len(numbers)} lines merged into one in")
