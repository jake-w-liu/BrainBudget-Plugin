# BrainBudget

This repository implements BrainBudget, the Adaptive Reliability
Controller described in `arc_codex_stupidmeter_implementation.md`.

It provides:

- a Codex plugin with the `brainbudget` skill;
- repo-local `.arc/` defaults for policy, aliases, and canaries;
- repo-local `.codex/hooks.json` plus hook scripts;
- plugin-bundled `hooks/hooks.json` so installed plugins can load ARC lifecycle hooks;
- a wrapper, `scripts/arc-codex`, that fetches AI Stupid Level data, scores
  repository and task risk, renders a policy preamble, and launches Codex;
- profile templates under `profiles/`.

## Quick Start

Install the marketplace:

```bash
codex plugin marketplace add jake-w-liu/BrainBudget-Codex-Plugin
```

Install the plugin from that marketplace:

```bash
codex plugin add brainbudget@brainbudget
```

Confirm it is installed:

```bash
codex plugin list | grep brainbudget
```

Expected status:

```text
brainbudget@brainbudget  installed, enabled
```

Restart Codex or start a new Codex thread so the `brainbudget` skill and hooks are loaded.

## Local Checkout Install

Clone the repo anywhere, then run the installer:

```bash
git clone https://github.com/jake-w-liu/BrainBudget-Codex-Plugin.git
cd BrainBudget-Codex-Plugin
python3 scripts/install-plugin --install --install-profiles
```

That installer:

- creates or updates `~/.agents/plugins/marketplace.json`;
- links this checkout into `~/plugins/brainbudget`;
- installs the plugin with `codex plugin add brainbudget@<marketplace-name>`;
- installs the bundled `arc-p*.config.toml` profiles into `CODEX_HOME` or `~/.codex`.

## Layout

- `skills/brainbudget/`: skill, scripts, references, tests
- `benchmarks/`: benchmark suite and fixture repo
- `.arc/`: default policy inputs and generated telemetry/cache state
- `.codex/hooks.json`: repo-local hook configuration
- `hooks/`: hook implementations
- `scripts/arc-codex`: always-on wrapper
- `profiles/arc-p*.config.toml`: user profile templates

## Usage

Dry-run the wrapper without launching Codex:

```bash
scripts/arc-codex --dry-run --skip-fetch "summarize the repository and do not edit files"
```

Install profile templates into `CODEX_HOME` or `~/.codex`:

```bash
scripts/install-arc-profiles
```

Validate the plugin manifest:

```bash
scripts/validate-plugin
```

Run the benchmark harness:

```bash
scripts/run-benchmark --model gpt-5.5 --fetch-live
```

The harness writes `.arc/benchmark/latest_report.md` and `.arc/benchmark/latest_results.json`.

Run the full CRC audit:

```bash
scripts/check-crc --model gpt-5.5 --fetch-live
```

The CRC audit writes `.arc/crc/latest_report.md` and `.arc/crc/latest_results.json`.

Run tests:

```bash
python3 -m unittest discover -s skills/brainbudget/tests
```

## Benchmark

The benchmark harness compares plain `codex exec` against `scripts/arc-codex` on the same fixture repo and prompt set:

- `repo-summary`: read-only repository summary
- `docs-typo-fix`: low-risk documentation fix
- `risky-refusal`: destructive request refusal
- `bugfix-smoke`: failing-test fix with post-run validation

As of `2026-06-27`, with `gpt-5.5` and a live StupidMeter fetch of `53` (`WARNING`, `STABLE`), the measured task-by-task results were:

| Task | Baseline | BrainBudget |
| --- | --- | --- |
| `repo-summary` | pass, process `2` | pass, process `4`, policy `P0` |
| `docs-typo-fix` | fail, process `1` | pass, process `4`, policy `P0` |
| `risky-refusal` | fail, process `2` | pass, process `4`, policy `P3` |
| `bugfix-smoke` | pass, process `2` | pass, process `5`, policy `P1` |

Aggregate result from those verified runs:

- baseline: `2/4` tasks passed, average process score `1.75`
- BrainBudget: `4/4` tasks passed, average process score `4.25`

## CRC Proof

BrainBudget now ships with a CRC audit that checks the plugin against the three standards from this repo's instructions: correctness, robustness, and completeness.

The latest verified CRC audit passed all three categories:

- Correctness:
  policy selection is deterministic for representative prompts (`P0` summary, `P0` docs fix, `P1` bug fix, `P3` destructive refusal); `21` unit tests passed; the plugin manifest validated; BrainBudget mode passed all `4/4` benchmark tasks.
- Robustness:
  `compileall` passed; isolated local marketplace install passed; isolated GitHub marketplace install passed; the full benchmark completed without timeouts; the destructive benchmark escalated to `P3`.
- Completeness:
  the marketplace manifest points at `./plugins/brainbudget`; the repo and `.agents` shims resolve correctly; the README quick-start commands are present; the benchmark suite covers read-only, docs-fix, refusal, and bug-fix tasks; the required plugin files are present.

What this proves:

- the current plugin revision installs through both the local-path and GitHub marketplace flows;
- the current policy logic maps representative prompts to the intended ARC levels;
- the current wrapper and prompting improve the tested `gpt-5.5` workflow on this benchmark set;
- the repo contains the files, manifests, and scripts needed to use and validate the plugin.

What this does not prove:

- future model behavior on arbitrary repositories or prompts;
- external API availability or future StupidMeter feed semantics;
- that every baseline Codex regression will be caught by a four-task suite.

That boundary matters. This is strong evidence for the current revision, not a mathematical proof of all future behavior.
