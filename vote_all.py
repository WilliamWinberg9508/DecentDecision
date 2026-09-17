#!/usr/bin/env python3
"""Run all ten test agents, each on a different small model.

    python vote_all.py --pull     # download the ten models first (~17 GB)
    python vote_all.py            # vote
    python vote_all.py --limit 10 # only the first 10 questions each

Pairs with reset_and_seed.sql, which creates the ten accounts and their
tokens. Those tokens are predictable by design so this script needs no
configuration -- which is exactly why the accounts must not exist on a public
instance.

It works through one model at a time rather than one question at a time. That
matters on a single GPU: Ollama keeps one model resident, so alternating
between ten of them would reload weights on every single ballot. Model-major
order loads each set of weights once.
"""

import argparse
import subprocess
import sys
import time

import httpx

import agent_client as ac

# (token, model). One account per model, matching reset_and_seed.sql.
AGENTS = [
    ("dd-test-01", "qwen3:0.6b"),
    ("dd-test-02", "qwen3:1.7b"),
    ("dd-test-03", "qwen3:4b"),
    ("dd-test-04", "llama3.2:1b"),
    ("dd-test-05", "llama3.2:3b"),
    ("dd-test-06", "gemma3:1b"),
    ("dd-test-07", "gemma3:4b"),
    ("dd-test-08", "phi4-mini:3.8b"),
    ("dd-test-09", "qwen2.5:1.5b"),
    ("dd-test-10", "qwen2.5:3b"),
]

QUADRANT = {(True, False): "supported", (True, True): "contested",
            (False, True): "opposed", (False, False): "irrelevant"}


def pull_all() -> None:
    for _, model in AGENTS:
        print(f"pulling {model} …", flush=True)
        subprocess.run(["ollama", "pull", model], check=False)


def run_agent(token: str, model: str, system: str, version, limit: int) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    queue = httpx.get(f"{ac.API}/agent/issues", headers=headers, timeout=60).json()
    if isinstance(queue, dict):                       # an error body, not a list
        return {"model": model, "error": queue.get("detail", "unknown")}
    queue = queue[:limit] if limit else queue

    stats = {"model": model, "cast": 0, "dropped": 0, "failed": 0,
             "supported": 0, "contested": 0, "opposed": 0, "irrelevant": 0}
    started = time.perf_counter()

    for n, issue in enumerate(queue, 1):
        try:
            ballot = ac.ask_model(issue["title"], issue["body"], system, model)
        except Exception as exc:
            stats["failed"] += 1
            print(f"    ! {model}: {type(exc).__name__}", flush=True)
            continue

        if ballot is None:
            # Not a crash: the model said something that was not two booleans,
            # so no ballot is cast. A dropped vote beats a guessed one.
            stats["dropped"] += 1
            continue

        ballot["prompt_version"] = version
        r = httpx.post(f"{ac.API}/issues/{issue['id']}/vote",
                       headers=headers, json=ballot, timeout=60)
        if r.status_code == 201:
            stats["cast"] += 1
            stats[QUADRANT[(ballot["good"], ballot["bad"])]] += 1
        elif r.status_code != 409:                    # 409 = already voted
            stats["failed"] += 1

        if n % 10 == 0:
            print(f"    {model}: {n}/{len(queue)}", flush=True)

    stats["seconds"] = time.perf_counter() - started
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true", help="download the models first")
    ap.add_argument("--limit", type=int, default=0, help="questions per agent")
    args = ap.parse_args()

    if args.pull:
        pull_all()

    system, version = ac.load_prompt()
    print(f"site: {ac.API}")
    print(f"prompt: {'v' + str(version) if version else 'local'}\n")

    results = []
    for token, model in AGENTS:
        print(f"  {model}", flush=True)
        results.append(run_agent(token, model, system, version, args.limit))

    print(f"\n{'model':<18}{'cast':>6}{'drop':>6}{'fail':>6}"
          f"{'sup':>6}{'con':>6}{'opp':>6}{'irr':>6}{'sec':>8}{'s/vote':>8}")
    print("-" * 78)
    for s in results:
        if "error" in s:
            print(f"{s['model']:<18}  error: {s['error']}")
            continue
        per = s["seconds"] / s["cast"] if s["cast"] else 0
        print(f"{s['model']:<18}{s['cast']:>6}{s['dropped']:>6}{s['failed']:>6}"
              f"{s['supported']:>6}{s['contested']:>6}{s['opposed']:>6}"
              f"{s['irrelevant']:>6}{s['seconds']:>8.0f}{per:>8.1f}")

    total = sum(s.get("cast", 0) for s in results)
    drops = sum(s.get("dropped", 0) for s in results)
    print(f"\n{total} ballots cast, {drops} replies dropped as unparseable.")
    if drops:
        print("Dropped replies are the smaller models failing to produce two "
              "booleans. That is the client refusing to guess, not an error.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nstopped")
