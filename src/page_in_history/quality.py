import json

from page_in_history.collect import DataPaths, load_documents


def coverage(label: str, count: int, total: int) -> str:
    share = f"{100 * count / total:.0f}%" if total else "-"
    return f"  {label:<34} {count:>4} / {total:<4} {share:>5}"


def report(paths: DataPaths) -> str:
    civilizations = load_documents(paths.civilizations)
    figures = load_documents(paths.figures)
    summary = json.loads(paths.report.read_text())
    scope = summary["slice"]
    lines = [f"Slice: {scope['region'] or 'world'}, {scope['start']} to {scope['end']}", "",
             f"Civilizations ({len(civilizations)})"]
    total = len(civilizations)
    lines += [
        coverage("Map card", sum(bool(c["map"]) for c in civilizations), total),
        coverage("Ruled before card", sum(bool(c["before"]) for c in civilizations), total),
        coverage("Ruled after card", sum(bool(c["after"]) for c in civilizations), total),
        coverage("Capital (context)", sum(bool(c["capitals"]) for c in civilizations), total),
        coverage("Period card (2 of 3 sources agree)", sum(c["period"]["confirmed"] for c in civilizations), total),
        coverage("Notable rulers", sum(bool(c["rulers"]) for c in civilizations), total),
    ]
    for civilization in civilizations:
        missing = [name for name, present in (
            ("period", civilization["period"]["confirmed"]), ("capital", civilization["capitals"]),
            ("before", civilization["before"]), ("after", civilization["after"]),
            ("rulers", civilization["rulers"]),
        ) if not present]
        if missing:
            lines.append(f"    {civilization['name']}: no confirmed {', '.join(missing)}")
    total = len(figures)
    lines += ["", f"Figures ({total})",
              coverage("Who card", sum(bool(f["role"]) for f in figures), total),
              coverage("Image", sum(bool(f["image"]) for f in figures), total),
              coverage("Photo card (photograph, died 1860 or later)", sum(f["photo"] for f in figures), total)]
    lines += ["", f"Failures: {len(summary['failures'])}"] + [f"    {failure}" for failure in summary["failures"]]
    unresolved = summary["unresolved_polities"]
    lines += ["", f"Cliopatria polities around the slice left unresolved: {len(unresolved)}"]
    lines += [f"    {name}: {reason}" for name, reason in unresolved.items()]
    lines += ["", f"Cliopatria time steps ignored as mislabelled spikes: {len(summary['ignored_cliopatria_steps'])}"]
    return "\n".join(lines)
