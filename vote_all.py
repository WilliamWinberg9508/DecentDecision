#!/usr/bin/env python3
"""Run the fifteen test agents, each on a different model under 1B parameters.

    python vote_all.py --pull      # download the models first, about 7 GB
    python vote_all.py             # vote
    python vote_all.py --limit 5   # only the first 5 questions each

Pairs with reset_and_seed.sql, which creates the fifteen accounts and their
tokens. The two lists must match -- token dd-test-07 is qwen:0.5b-chat in both.
The tokens are predictable by design so this script needs no configuration,
which is exactly why the accounts must not exist on a public instance.

Eleven genuinely different sets of weights, plus four quantization variants
kept as comparisons. qwen2.5:0.5b runs three times -- at q2_K, the default
q4_K_M and fp16 -- so where those three disagree, quantization is the only
thing that differs. That is the site's claim about configuration, at the
smallest scale it can be shown.

**Expect dropped ballots.** The client accepts only a reply that parses as
exactly two booleans, and a 135M model often cannot manage that. A dropped
reply is the client refusing to guess, not a failure -- but it means this set
tests the pipeline rather than the judgement. The summary counts drops per
model, which at this size is the most interesting column.

It works through one model at a time rather than one question at a time.
Ollama keeps one model resident, so alternating would reload weights on every
ballot; model-major order loads each set once.
"""

import argparse
import subprocess
import sys
import time

import httpx

import agent_client as ac

# (token, model). Must match the list in reset_and_seed.sql. Every tag here was
# checked against the Ollama library rather than recalled -- a mistyped tag
# fails at `ollama pull`, the least useful moment to find out. llama3.2:1b is
# deliberately absent: at 1.24B it is over the line however it is marketed.
AGENTS = [
    ("dd-test-01", "qwen3:0.6b"),                      # qwen3-06b
    ("dd-test-02", "qwen3:0.6b-q8_0"),                 # qwen3-06b-q8
    ("dd-test-03", "qwen2.5:0.5b"),                    # qwen25-05b
    ("dd-test-04", "qwen2.5:0.5b-instruct-q2_K"),      # qwen25-05b-q2k
    ("dd-test-05", "qwen2.5:0.5b-instruct-fp16"),      # qwen25-05b-fp16
    ("dd-test-06", "qwen2:0.5b"),                      # qwen2-05b
    ("dd-test-07", "qwen:0.5b-chat"),                  # qwen15-05b
    ("dd-test-08", "gemma3:270m"),                     # gemma3-270m
    ("dd-test-09", "gemma3:270m-it-q8_0"),             # gemma3-270m-q8
    ("dd-test-10", "smollm2:360m"),                    # smollm2-360m
    ("dd-test-11", "smollm2:135m"),                    # smollm2-135m
    ("dd-test-12", "smollm:360m-instruct-v0.2-q8_0"),  # smollm-360m
    ("dd-test-13", "smollm:135m-instruct-v0.2-q8_0"),  # smollm-135m
    ("dd-test-14", "granite4:350m"),                   # granite4-350m
    ("dd-test-15", "granite4:350m-h"),                 # granite4-350m-h
]

QUADRANT = {(True, False): "supported", (True, True): "contested",
            (False, True): "opposed", (False, False): "irrelevant"}


def pull_all() -> None:
    for _, model in AGENTS:
        print(f"pulling {model} …", flush=True)
        subprocess.run(["ollama", "pull", model], check=False)


def run_agent(token: str, model: str, system: str, version, limit: int,
              forums: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    queue = ac.fetch_queue(headers, forums)
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
    ap.add_argument("--forums", default="",
                    help="only these forums, comma-separated slugs (e.g. "
                         "japan,brazil). Default: every forum")
    ap.add_argument("--list-forums", action="store_true",
                    help="print the forums and their open questions, then exit")
    args = ap.parse_args()

    if args.list_forums:
        for f in ac.list_forums():
            print(f"{f['slug']:<18}{f['open_questions']:>5} open   {f['name']}")
        return

    if args.pull:
        pull_all()

    if args.forums:
        # Check the names once, up front, rather than letting all fifteen
        # agents discover the same typo one after another.
        real = {f["slug"] for f in ac.list_forums()}
        wrong = [s for s in args.forums.split(",") if s.strip() and s.strip() not in real]
        if wrong:
            sys.exit(f"unknown forum: {', '.join(wrong)} "
                     f"(python vote_all.py --list-forums shows the real ones)")

    system, version = ac.load_prompt()
    print(f"site: {ac.API}")
    print(f"agents: {len(AGENTS)}")
    print(f"forums: {args.forums or 'all'}")
    print(f"prompt: {'v' + str(version) if version else 'local'}\n")

    results = []
    for token, model in AGENTS:
        print(f"  {model}", flush=True)
        results.append(run_agent(token, model, system, version, args.limit, args.forums))

    print(f"\n{'model':<34}{'cast':>6}{'drop':>6}{'fail':>6}"
          f"{'sup':>6}{'con':>6}{'opp':>6}{'irr':>6}{'sec':>8}{'s/vote':>8}")
    print("-" * 94)
    for s in results:
        if "error" in s:
            print(f"{s['model']:<34}  error: {s['error']}")
            continue
        per = s["seconds"] / s["cast"] if s["cast"] else 0
        print(f"{s['model']:<34}{s['cast']:>6}{s['dropped']:>6}{s['failed']:>6}"
              f"{s['supported']:>6}{s['contested']:>6}{s['opposed']:>6}"
              f"{s['irrelevant']:>6}{s['seconds']:>8.0f}{per:>8.1f}")

    total = sum(s.get("cast", 0) for s in results)
    drops = sum(s.get("dropped", 0) for s in results)
    print(f"\n{total} ballots cast, {drops} replies dropped as unparseable.")
    if drops:
        print("Dropped replies are models failing to produce two booleans. That "
              "is the client refusing to guess, not an error -- and at this size "
              "it is expected, sometimes for most of a model's attempts.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nstopped")
