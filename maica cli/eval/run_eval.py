#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monika character-fidelity evaluation runner.

Drives the real reply path (``MaicaEngine.chat``) over a fixed scenario set on
isolated temporary databases, scores each reply with an LLM judge anchored to
real Monika reference lines, and prints a scorecard with a diff against the
previous run.

Usage:
    python "maica cli/eval/run_eval.py"                 # real run, uses config.json
    python "maica cli/eval/run_eval.py" --offline       # plumbing self-test, no API
    python "maica cli/eval/run_eval.py" --subset comfort
    python "maica cli/eval/run_eval.py" --judge-model deepseek-reasoner

Results are written to ``maica cli/eval/results/`` (git-ignored). This script
never prints API keys; it relies on the client's own secret redaction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
CLI_DIR = EVAL_DIR.parent
RESULTS_DIR = EVAL_DIR / "results"
SCENARIO_PATH = EVAL_DIR / "scenarios.jsonl"
DATA_DIR = CLI_DIR / "data"

if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from judge import DIMENSION_KEYS, judge_reply  # noqa: E402


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_scenarios(subset: str = "") -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    with SCENARIO_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if subset and str(row.get("category", "")) != subset:
                continue
            scenarios.append(row)
    return scenarios


def load_gold(category: str, intent: str, language: str, limit: int = 3) -> list[str]:
    lang = "zh" if str(language).startswith("zh") else "en"
    path = DATA_DIR / f"dialogue_examples_{lang}.jsonl"
    if not path.exists():
        return []
    matches: list[tuple[int, int, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("category", "")) != category:
                continue
            assistant = str(row.get("assistant", "")).strip()
            if not assistant:
                continue
            intent_match = 1 if str(row.get("intent", "")) == intent else 0
            quality = int(row.get("quality", 0) or 0)
            matches.append((intent_match, quality, assistant))
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [text for _, _, text in matches[:limit]]


# ---------------------------------------------------------------------------
# Engine setup
# ---------------------------------------------------------------------------

def build_base_config(offline: bool) -> dict[str, Any]:
    from config_defaults import DEFAULT_CONFIG
    from config_io import load_json

    if offline:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "api_key_required": False,
                "jsonl_logs_enabled": False,
                "auto_backup_enabled": False,
                "mfocus_mode": "rule",
                "mtrigger_mode": "rule",
                "embedding_enabled": False,
                "memory_embedding_enabled": False,
                "embedding_service_enabled": False,
            }
        )
        return config

    config_path = CLI_DIR / "config.json"
    config = load_json(config_path, DEFAULT_CONFIG)
    # Keep eval runs from mutating the user's real working state.
    config = dict(config)
    config.update({"jsonl_logs_enabled": False, "auto_backup_enabled": False})
    return config


class _FakeChatClient:
    """Deterministic, vaguely in-character replies for offline plumbing tests."""

    def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
        return self.chat_with_usage(messages, overrides)["content"]

    def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user = str(msg.get("content", ""))
                break
        zh = any("一" <= ch <= "鿿" for ch in user)
        if zh:
            text = "我一直在这儿陪着你呢，慢慢说，我都听着。"
        else:
            text = "I'm right here with you, okay? Tell me everything~"
        content = json.dumps({"text": text, "emotion": "smile", "action": {}}, ensure_ascii=False)
        return {"content": content, "usage": {"total_tokens": 7}, "model": "offline-fake"}


class _FakeJudgeClient:
    def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
        scores = {key: {"score": 4, "reason": "offline fake judge"} for key in DIMENSION_KEYS}
        scores["overall_comment"] = "offline plumbing check; scores are not meaningful"
        return json.dumps(scores, ensure_ascii=False)


def run_scenario(scenario: dict[str, Any], base_config: dict[str, Any], offline: bool) -> dict[str, Any]:
    from engine import MaicaEngine

    config = dict(base_config)
    config["language"] = "zh" if str(scenario.get("language", "en")).startswith("zh") else "en"

    with tempfile.TemporaryDirectory(prefix="maica-eval-") as temp_dir:
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / "eval.db", app_dir=CLI_DIR)
        if offline:
            engine.client = _FakeChatClient()
        try:
            seed = scenario.get("seed") or {}
            if "affection" in seed:
                engine.store.set_affection(float(seed["affection"]))
            for turn in seed.get("history") or []:
                try:
                    role, content = turn[0], turn[1]
                except Exception:
                    continue
                engine.store.add_message(str(role), str(content))
            result = engine.chat(str(scenario.get("user_input", "")))
        finally:
            engine.close()
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(CLI_DIR.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [r for r in records if r.get("judgement") and r["judgement"].get("overall")]
    dim_totals: dict[str, list[float]] = {key: [] for key in DIMENSION_KEYS}
    cat_totals: dict[str, list[float]] = {}
    overall: list[float] = []
    for r in judged:
        j = r["judgement"]
        overall.append(j["overall"])
        cat_totals.setdefault(r["scenario"]["category"], []).append(j["overall"])
        for key in DIMENSION_KEYS:
            score = j["dimensions"].get(key, {}).get("score") or 0
            if score:
                dim_totals[key].append(score)

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    return {
        "overall": mean(overall),
        "by_dimension": {key: mean(vals) for key, vals in dim_totals.items()},
        "by_category": {cat: mean(vals) for cat, vals in sorted(cat_totals.items())},
        "scored": len(judged),
        "total": len(records),
    }


def latest_previous_result() -> dict[str, Any] | None:
    if not RESULTS_DIR.exists():
        return None
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _delta(current: float, previous: float | None) -> str:
    if previous is None:
        return "   —  "
    diff = round(current - previous, 2)
    if diff == 0:
        return "  0.00"
    return f"{diff:+.2f}"


def print_scorecard(summary: dict[str, Any], records: list[dict[str, Any]], previous: dict[str, Any] | None) -> None:
    prev_summary = (previous or {}).get("summary") or {}
    prev_dims = prev_summary.get("by_dimension") or {}
    prev_cats = prev_summary.get("by_category") or {}

    print("\n" + "=" * 60)
    print("MONIKA CHARACTER-FIDELITY SCORECARD")
    print("=" * 60)
    print(f"scenarios scored: {summary['scored']}/{summary['total']}")
    if previous:
        print(f"baseline: {previous.get('commit', '?')} @ {previous.get('timestamp', '?')}")
    print(f"\nOVERALL: {summary['overall']:.2f} / 5     (Δ {_delta(summary['overall'], prev_summary.get('overall'))})")

    print("\nby dimension:")
    for key in DIMENSION_KEYS:
        cur = summary["by_dimension"].get(key, 0.0)
        print(f"  {key:<14} {cur:.2f}   (Δ {_delta(cur, prev_dims.get(key))})")

    print("\nby category:")
    for cat, cur in summary["by_category"].items():
        print(f"  {cat:<14} {cur:.2f}   (Δ {_delta(cur, prev_cats.get(cat))})")

    # Lowest-scoring replies for human calibration.
    judged = [r for r in records if r.get("judgement") and r["judgement"].get("overall")]
    judged.sort(key=lambda r: r["judgement"]["overall"])
    print("\nlowest-scoring replies (eyeball these to calibrate the rubric):")
    for r in judged[:3]:
        j = r["judgement"]
        print(f"\n  [{r['scenario']['id']}]  overall {j['overall']:.2f}")
        print(f"  user : {r['scenario'].get('user_input', '')}")
        print(f"  reply: {r.get('reply', '')}")
        print(f"  judge: {j.get('overall_comment', '')}")
    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Monika character-fidelity evaluation")
    parser.add_argument("--offline", action="store_true", help="fake engine + fake judge; no API calls")
    parser.add_argument("--subset", default="", help="run only scenarios in this category")
    parser.add_argument("--judge-model", default="", help="override the judge model id")
    parser.add_argument("--no-save", action="store_true", help="do not write a results file")
    args = parser.parse_args()

    scenarios = load_scenarios(args.subset)
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 1

    base_config = build_base_config(args.offline)
    if args.offline:
        judge_client: Any = _FakeJudgeClient()
    else:
        from client import OpenAICompatibleClient

        judge_client = OpenAICompatibleClient(base_config)

    records: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        sid = scenario.get("id", f"scenario_{index}")
        print(f"[{index}/{len(scenarios)}] {sid} ...", flush=True)
        result = run_scenario(scenario, base_config, args.offline)
        if not result.get("ok"):
            print(f"    chat failed: {result.get('error', 'unknown error')}", file=sys.stderr)
            records.append({"scenario": scenario, "reply": "", "result_error": result.get("error", ""), "judgement": {}})
            continue
        reply = result.get("text", "")
        gold = load_gold(scenario.get("category", ""), scenario.get("intent", ""), scenario.get("language", "en"))
        try:
            judgement = judge_reply(judge_client, scenario, reply, gold, args.judge_model or None)
        except Exception as exc:
            print(f"    judge failed: {exc}", file=sys.stderr)
            judgement = {}
        records.append(
            {
                "scenario": scenario,
                "reply": reply,
                "emotion": result.get("emotion", ""),
                "gold_count": len(gold),
                "judgement": judgement,
            }
        )

    summary = aggregate(records)
    previous = latest_previous_result()
    print_scorecard(summary, records, previous)

    if not args.no_save and not args.offline:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        payload = {
            "timestamp": stamp,
            "commit": git_commit(),
            "subset": args.subset,
            "summary": summary,
            "records": records,
        }
        out_path = RESULTS_DIR / f"{stamp}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
