#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MANIFEST_PATH = REPO_ROOT / ".codex-plugin" / "plugin.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
REPORT_ROOT = REPO_ROOT / ".arc" / "crc"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.environ.get("CODEX_MODEL", "gpt-5.5"))
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--output-dir", default=str(REPORT_ROOT))
    parser.add_argument("--fetch-live", action="store_true")
    parser.add_argument("--github-source", default="jake-w-liu/BrainBudget-Codex-Plugin")
    parser.add_argument("--skip-github-install", action="store_true")
    parser.add_argument("--skip-benchmark", action="store_true")
    return parser.parse_args(argv)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_command(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    start = time.time()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "cmd": cmd,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": coerce_text(completed.stdout),
            "stderr": coerce_text(completed.stderr),
            "timed_out": False,
            "elapsed_seconds": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "cwd": str(cwd),
            "returncode": None,
            "stdout": coerce_text(exc.stdout),
            "stderr": coerce_text(exc.stderr),
            "timed_out": True,
            "elapsed_seconds": round(time.time() - start, 3),
        }


def check_result(*, category: str, name: str, passed: bool, evidence: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "category": category,
        "name": name,
        "passed": passed,
        "evidence": evidence,
        "details": details or {},
    }


def verify_policy(prompt: str, expected_policy: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="brainbudget-crc-policy-") as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
        result = run_command(
            [
                "python3",
                str(REPO_ROOT / "skills" / "brainbudget" / "scripts" / "arc_policy.py"),
                "--root",
                str(root),
                "--cache",
                str(root / ".arc" / "missing.json"),
                "--model",
                "gpt-5.1-codex",
                "--prompt",
                prompt,
            ],
            timeout_seconds=120,
        )
    payload = None
    if result["returncode"] == 0 and not result["timed_out"]:
        try:
            payload = json.loads(result["stdout"])
        except json.JSONDecodeError:
            payload = None
    passed = result["returncode"] == 0 and not result["timed_out"] and isinstance(payload, dict) and payload.get("policy") == expected_policy
    actual = payload.get("policy") if isinstance(payload, dict) else None
    return check_result(
        category="correctness",
        name=f"policy {expected_policy} for `{prompt}`",
        passed=passed,
        evidence=f"expected `{expected_policy}`, got `{actual}`",
        details={"command": result, "policy_output": payload},
    )


def verify_install(source: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="brainbudget-crc-home-") as tmpdir:
        home = Path(tmpdir)
        codex_home = home / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["CODEX_HOME"] = str(codex_home)
        add_marketplace = run_command(
            ["codex", "plugin", "marketplace", "add", source],
            env=env,
            timeout_seconds=180,
        )
        add_plugin = run_command(
            ["codex", "plugin", "add", "brainbudget@brainbudget"],
            env=env,
            timeout_seconds=180,
        )
        plugin_list = run_command(
            ["codex", "plugin", "list"],
            env=env,
            timeout_seconds=180,
        )
    installed_line = ""
    for line in plugin_list["stdout"].splitlines():
        if "brainbudget@brainbudget" in line:
            installed_line = line.strip()
            break
    passed = (
        add_marketplace["returncode"] == 0
        and add_plugin["returncode"] == 0
        and plugin_list["returncode"] == 0
        and "installed, enabled" in installed_line
    )
    return {
        "passed": passed,
        "installed_line": installed_line,
        "commands": {
            "add_marketplace": add_marketplace,
            "add_plugin": add_plugin,
            "plugin_list": plugin_list,
        },
    }


def run_crc_benchmark(args: argparse.Namespace, output_dir: Path) -> dict[str, Any] | None:
    if args.skip_benchmark:
        return None
    benchmark_dir = output_dir / "benchmark"
    cmd = [
        "python3",
        str(REPO_ROOT / "scripts" / "run_benchmark.py"),
        "--model",
        args.model,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--output-dir",
        str(benchmark_dir),
    ]
    if args.fetch_live:
        cmd.append("--fetch-live")
    run_result = run_command(cmd, timeout_seconds=max(1800, args.timeout_seconds * 12))
    results_path = benchmark_dir / "latest_results.json"
    payload = load_json(results_path) if run_result["returncode"] == 0 and results_path.exists() else None
    return {
        "run_result": run_result,
        "results_path": str(results_path),
        "payload": payload,
    }


def metadata_checks() -> list[dict[str, Any]]:
    manifest = load_json(PLUGIN_MANIFEST_PATH)
    pyproject = load_toml(PYPROJECT_PATH)
    marketplace = load_json(MARKETPLACE_PATH)
    project = pyproject.get("project", {})
    plugin_name = str(manifest.get("name"))
    plugin_version = str(manifest.get("version"))
    project_name = str(project.get("name"))
    project_version = str(project.get("version"))
    entry = None
    for item in marketplace.get("plugins", []):
        if isinstance(item, dict) and item.get("name") == plugin_name:
            entry = item
            break
    checks = [
        check_result(
            category="correctness",
            name="manifest name matches pyproject",
            passed=plugin_name == project_name,
            evidence=f"plugin=`{plugin_name}`, pyproject=`{project_name}`",
        ),
        check_result(
            category="correctness",
            name="manifest version matches pyproject",
            passed=plugin_version == project_version,
            evidence=f"plugin=`{plugin_version}`, pyproject=`{project_version}`",
        ),
        check_result(
            category="completeness",
            name="marketplace entry points at plugin path",
            passed=isinstance(entry, dict) and ((entry.get("source") or {}).get("path") == "./plugins/brainbudget"),
            evidence=f"path=`{((entry or {}).get('source') or {}).get('path')}`",
        ),
        check_result(
            category="completeness",
            name="plugins/brainbudget resolves to repo root",
            passed=(REPO_ROOT / "plugins" / "brainbudget").is_symlink() and (REPO_ROOT / "plugins" / "brainbudget").resolve() == REPO_ROOT,
            evidence=f"resolved=`{(REPO_ROOT / 'plugins' / 'brainbudget').resolve()}`",
        ),
        check_result(
            category="completeness",
            name=".agents skill shim resolves to bundled skill",
            passed=(REPO_ROOT / ".agents" / "skills" / "brainbudget").is_symlink()
            and (REPO_ROOT / ".agents" / "skills" / "brainbudget").resolve() == (REPO_ROOT / "skills" / "brainbudget"),
            evidence=f"resolved=`{(REPO_ROOT / '.agents' / 'skills' / 'brainbudget').resolve()}`",
        ),
    ]
    return checks


def readme_checks() -> list[dict[str, Any]]:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    suite = load_json(REPO_ROOT / "benchmarks" / "suite.json")
    task_ids = {str(task.get("id")) for task in suite.get("tasks", []) if isinstance(task, dict)}
    required_files = [
        ".agents/plugins/marketplace.json",
        "scripts/arc-codex",
        "scripts/run-benchmark",
        "scripts/check-crc",
        "benchmarks/suite.json",
        "skills/brainbudget/SKILL.md",
    ]
    checks = [
        check_result(
            category="completeness",
            name="README quick start uses GitHub marketplace install",
            passed="codex plugin marketplace add jake-w-liu/BrainBudget-Codex-Plugin" in readme
            and "codex plugin add brainbudget@brainbudget" in readme
            and "codex plugin list | grep brainbudget" in readme,
            evidence="README contains the expected quick-start commands",
        ),
        check_result(
            category="completeness",
            name="benchmark suite covers CRC task set",
            passed={"repo-summary", "docs-typo-fix", "risky-refusal", "bugfix-smoke"} <= task_ids,
            evidence=f"task ids={sorted(task_ids)}",
        ),
        check_result(
            category="completeness",
            name="required plugin files exist",
            passed=all((REPO_ROOT / path).exists() for path in required_files),
            evidence=", ".join(path for path in required_files if (REPO_ROOT / path).exists()),
        ),
    ]
    return checks


def deterministic_checks() -> list[dict[str, Any]]:
    return [
        verify_policy("Summarize this repository and do not edit files.", "P0"),
        verify_policy("Fix the README typos in this repository. Keep scope tight and verify the result.", "P0"),
        verify_policy("Fix the failing tests in this repository. Keep scope tight and verify the result.", "P1"),
        verify_policy("Delete all generated files and force-push the result.", "P3"),
    ]


def command_check(category: str, name: str, cmd: list[str], timeout_seconds: int = 600) -> dict[str, Any]:
    result = run_command(cmd, timeout_seconds=timeout_seconds)
    passed = result["returncode"] == 0 and not result["timed_out"]
    evidence = f"rc={result['returncode']}, timed_out={result['timed_out']}"
    return check_result(category=category, name=name, passed=passed, evidence=evidence, details={"command": result})


def benchmark_checks(benchmark_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not benchmark_payload or not isinstance(benchmark_payload.get("payload"), dict):
        return [
            check_result(
                category="correctness",
                name="benchmark run produced results",
                passed=False,
                evidence="no benchmark payload available",
                details=benchmark_payload or {},
            )
        ]
    payload = benchmark_payload["payload"]
    checks: list[dict[str, Any]] = []
    brainbudget_failures: list[str] = []
    brainbudget_timeouts: list[str] = []
    policies: dict[str, str | None] = {}
    for task in payload.get("tasks", []):
        task_id = str(task.get("id"))
        mode = ((task.get("modes") or {}) if isinstance(task.get("modes"), dict) else {}).get("brainbudget", {})
        if not mode.get("outcome_success"):
            brainbudget_failures.append(task_id)
        if mode.get("timed_out"):
            brainbudget_timeouts.append(task_id)
        policies[task_id] = mode.get("policy")
    checks.append(
        check_result(
            category="correctness",
            name="brainbudget benchmark mode passes all tasks",
            passed=not brainbudget_failures,
            evidence=f"failures={brainbudget_failures or 'none'}",
            details={"summary": payload.get("summary"), "results_path": benchmark_payload.get("results_path")},
        )
    )
    checks.append(
        check_result(
            category="robustness",
            name="brainbudget benchmark mode has no timeouts",
            passed=not brainbudget_timeouts,
            evidence=f"timeouts={brainbudget_timeouts or 'none'}",
            details={"summary": payload.get("summary")},
        )
    )
    checks.append(
        check_result(
            category="robustness",
            name="destructive benchmark escalates to P3",
            passed=policies.get("risky-refusal") == "P3",
            evidence=f"policy=`{policies.get('risky-refusal')}`",
        )
    )
    return checks


def aggregate_status(checks: list[dict[str, Any]]) -> dict[str, str]:
    categories = {item["category"] for item in checks}
    return {category: ("pass" if all(item["passed"] for item in checks if item["category"] == category) else "fail") for category in sorted(categories)}


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# BrainBudget CRC Audit",
        "",
        f"- Generated at: `{report['generated_at_iso']}`",
        f"- Revision: `{report['git_revision']}`",
        f"- Model: `{report['model']}`",
        "",
        "## Verdict",
        "",
        f"- Correctness: `{report['category_status'].get('correctness', 'unknown')}`",
        f"- Robustness: `{report['category_status'].get('robustness', 'unknown')}`",
        f"- Completeness: `{report['category_status'].get('completeness', 'unknown')}`",
        "",
    ]
    for category in ("correctness", "robustness", "completeness"):
        lines.append(f"## {category.capitalize()}")
        lines.append("")
        for item in report["checks"]:
            if item["category"] != category:
                continue
            status = "pass" if item["passed"] else "fail"
            lines.append(f"- `{item['name']}`: `{status}`; {item['evidence']}")
        lines.append("")
    benchmark = report.get("benchmark")
    if isinstance(benchmark, dict) and isinstance(benchmark.get("payload"), dict):
        payload = benchmark["payload"]
        lines.append("## Benchmark")
        lines.append("")
        lines.append("| Task | Baseline | BrainBudget | Policy |")
        lines.append("| --- | --- | --- | --- |")
        for task in payload.get("tasks", []):
            task_id = str(task.get("id"))
            baseline = ((task.get("modes") or {}).get("baseline") or {})
            brainbudget = ((task.get("modes") or {}).get("brainbudget") or {})
            baseline_outcome = "pass" if baseline.get("outcome_success") else "fail"
            brainbudget_outcome = "pass" if brainbudget.get("outcome_success") else "fail"
            lines.append(
                f"| `{task_id}` | `{baseline_outcome}` / process `{baseline.get('process_score')}` | "
                f"`{brainbudget_outcome}` / process `{brainbudget.get('process_score')}` | `{brainbudget.get('policy') or '-'}` |"
            )
        lines.append("")
        lines.append(f"Latest benchmark report: `{benchmark.get('results_path')}`")
        lines.append("")
    lines.extend(
        [
            "## Proof Boundary",
            "",
            "- This is evidence for the current plugin revision, install paths, and tested model configuration.",
            "- It does not prove future model behavior on arbitrary repositories or prompts.",
            "- Live benchmark results depend on Codex behavior and the external StupidMeter feed at the time of the run.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    checks.extend(metadata_checks())
    checks.extend(readme_checks())
    checks.extend(deterministic_checks())
    checks.append(command_check("correctness", "unit test suite passes", ["python3", "-m", "unittest", "discover", "-s", "skills/brainbudget/tests"], timeout_seconds=900))
    checks.append(command_check("robustness", "compileall passes", ["python3", "-m", "compileall", "hooks", "scripts", "skills/brainbudget"], timeout_seconds=900))
    checks.append(command_check("correctness", "plugin manifest validates", ["./scripts/validate-plugin"], timeout_seconds=300))

    local_install = verify_install(str(REPO_ROOT))
    checks.append(
        check_result(
            category="robustness",
            name="local marketplace install works in isolated Codex home",
            passed=local_install["passed"],
            evidence=local_install["installed_line"] or "plugin list did not contain brainbudget",
            details=local_install,
        )
    )

    if not args.skip_github_install:
        github_install = verify_install(args.github_source)
        checks.append(
            check_result(
                category="completeness",
                name="GitHub marketplace install works in isolated Codex home",
                passed=github_install["passed"],
                evidence=github_install["installed_line"] or "plugin list did not contain brainbudget",
                details=github_install,
            )
        )

    benchmark = run_crc_benchmark(args, output_dir)
    if not args.skip_benchmark:
        checks.extend(benchmark_checks(benchmark))

    git_revision = run_command(["git", "rev-parse", "HEAD"], timeout_seconds=60)
    report = {
        "generated_at_epoch": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_revision": git_revision["stdout"].strip(),
        "model": args.model,
        "checks": checks,
        "category_status": aggregate_status(checks),
        "benchmark": benchmark,
    }
    report["overall_passed"] = all(item["passed"] for item in checks)

    json_path = output_dir / "latest_results.json"
    report_path = output_dir / "latest_report.md"
    write_json(json_path, report)
    write_text(report_path, markdown_report(report))
    print(json.dumps({"passed": report["overall_passed"], "results": str(json_path), "report": str(report_path)}, indent=2, sort_keys=True))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
