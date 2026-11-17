#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the requirements traceability matrix and verification summary.

Inputs
  docs/requirements.md   requirement table (rows starting with "| REQ-")
  JUnit XML              from `pytest --junitxml=...`; each test carries a "requirements"
                         property (set from @pytest.mark.req in tests/conftest.py) plus any
                         measured values recorded with record_property().

Output (Markdown)
  One row per requirement: linked tests, verdict (PASS / FAIL / NOT VERIFIED), and evidence.
  A requirement passes only if it has at least one test and all its tests pass.

Exit code is 1 if any requirement fails or is not verified, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REQ_ROW = re.compile(r"^\|\s*(REQ-[A-Z]+-\d{3})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


def load_requirements(path: Path) -> dict[str, str]:
    reqs = {}
    for line in path.read_text().splitlines():
        m = REQ_ROW.match(line)
        if m:
            reqs[m.group(1)] = m.group(2)
    return reqs


def load_results(path: Path):
    tests = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "FAIL"
        elif tc.find("skipped") is not None:
            status = "SKIP"
        else:
            status = "PASS"
        module = tc.get("classname", "").split(".")[-1]
        tests.append({
            "id": f"{module}::{tc.get('name')}",
            "status": status,
            "reqs": [r for r in props.pop("requirements", "").split(",") if r],
            "evidence": props,
            "time": float(tc.get("time", 0)),
        })
    return tests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--junit", type=Path, required=True)
    ap.add_argument("--requirements", type=Path, default=Path("docs/requirements.md"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="Verification report")
    args = ap.parse_args()

    reqs = load_requirements(args.requirements)
    tests = load_results(args.junit)
    by_req = defaultdict(list)
    unknown = set()
    for t in tests:
        for r in t["reqs"]:
            if r not in reqs:
                unknown.add(r)
            by_req[r].append(t)

    lines = [f"# {args.title}", ""]
    lines.append(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from `{args.junit.name}`.")
    lines.append("")
    n_pass = n_fail = n_nv = 0
    rows = []
    for rid, text in reqs.items():
        ts = by_req.get(rid, [])
        if not ts or all(t["status"] == "SKIP" for t in ts):
            verdict, n_nv = "NOT VERIFIED", n_nv + 1
        elif any(t["status"] == "FAIL" for t in ts):
            verdict, n_fail = "FAIL", n_fail + 1
        else:
            verdict, n_pass = "PASS", n_pass + 1
        test_ids = "<br>".join(f"`{t['id']}` {t['status']}" for t in ts) or "none"
        evidence = []
        for t in ts:
            for k, v in t["evidence"].items():
                evidence.append(f"{k}={v}")
        ev = "<br>".join(evidence[:8]) + (" …" if len(evidence) > 8 else "")
        rows.append(f"| {rid} | {text} | {test_ids} | **{verdict}** | {ev} |")

    lines.append(f"**{n_pass} of {len(reqs)} requirements verified PASS**, {n_fail} FAIL, {n_nv} NOT VERIFIED. "
                 f"{sum(t['status'] == 'PASS' for t in tests)} of {len(tests)} tests passed.")
    lines.append("")
    lines.append("| Requirement | Summary | Tests | Verdict | Evidence (measured) |")
    lines.append("|---|---|---|---|---|")
    lines.extend(rows)
    orphans = [t for t in tests if not t["reqs"]]
    if orphans or unknown:
        lines += ["", "## Findings", ""]
        for t in orphans:
            lines.append(f"- Test `{t['id']}` is not linked to any requirement.")
        for r in sorted(unknown):
            lines.append(f"- Tests reference `{r}`, which is not in the requirements document.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")
    print(f"{n_pass}/{len(reqs)} requirements PASS, {n_fail} FAIL, {n_nv} NOT VERIFIED -> {args.out}")
    return 0 if (n_fail == 0 and n_nv == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
