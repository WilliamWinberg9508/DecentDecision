#!/usr/bin/env python3
"""Run the five test agents, each on a different model that fits an RTX 3060.

    python vote_all.py --pull           # download the models first, about 38 GB
    python vote_all.py                  # vote on everything open
    python vote_all.py --forums japan   # only some forums
    python vote_all.py --limit 5        # only the first 5 questions each

Pairs with reset_and_seed.sql, which creates the five accounts, and
make_test_tokens.py, which gives them random tokens and saves them in
test_tokens.json next to this script. The account names must match.

Five labs, one model each, all at the default 4-bit quantization and all under
10 GB, so each loads whole into 12 GB of VRAM with room for the context. A
model that spills into system RAM runs several times slower, which is the
difference between an evening and a weekend for 1250 ballots.

It works through one model at a time rather than one question at a time.
Ollama keeps one model resident, so alternating would reload weights on every
ballot; model-major order loads each set once.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time

import httpx

import agent_client as ac

# (account, model). Must match the list in reset_and_seed.sql. The tokens are
# not here: make_test_tokens.py generates random ones and keeps them in
# test_tokens.json, which is on your computer only (it is in .gitignore).
AGENTS = [
    ("gemma4-12b",     "gemma4:12b"),        # Google,    7.6 GB
    ("qwen35-9b",      "qwen3.5:9b"),        # Alibaba,   6.6 GB
    ("ministral3-14b", "ministral-3:14b"),   # Mistral,   9.1 GB
    ("phi4-14b",       "phi4:14b"),          # Microsoft, 9.1 GB
    ("granite42-8b",   "granite4.2:8b"),     # IBM,       5.3 GB
]
TOKENS_FILE = pathlib.Path(__file__).with_name("test_tokens.json")


def load_tokens() -> dict:
    try:
        return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit("No test_tokens.json yet. Run: python make_test_tokens.py")

QUADRANT = {(True, False): "supported", (True, True): "contested",
            (False, True): "opposed", (False, False): "irrelevant"}


def pull_all() -> None:
    for _, model in AGENTS:
        print(f"pulling {model} …", flush=True)
        subprocess.run(["ollama", "pull", model], check=False)


def queue_all(headers: dict, forums: str, limit: int):
    """Yield every open question this agent has not voted on. The server hands
    out at most 100 at a time, so ask again after each batch. A question the
    model failed to answer stays in the queue and comes back; it is skipped
    rather than asked forever, and the loop ends when a batch has nothing new."""
    seen: set[int] = set()
    while True:
        batch = ac.fetch_queue(headers, forums)
        if isinstance(batch, dict):                   # an error body, not a list
            raise RuntimeError(batch.get("detail", "unknown"))
        fresh = [i for i in batch if i["id"] not in seen]
        if not fresh:
            return
        for issue in fresh:
            if limit and len(seen) >= limit:
                return
            seen.add(issue["id"])
            yield issue


def run_agent(token: str, model: str, system: str, version, limit: int,
              forums: str = "") -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    stats = {"model": model, "cast": 0, "dropped": 0, "failed": 0,
             "supported": 0, "contested": 0, "opposed": 0, "irrelevant": 0}
    started = time.perf_counter()

    try:
        for n, issue in enumerate(queue_all(headers, forums, limit), 1):
            try:
                ballot = ac.ask_model(issue["title"], issue["body"], system, model)
            except Exception as exc:
                stats["failed"] += 1
                print(f"    ! {model}: {type(exc).__name__}", flush=True)
                continue

            if ballot is None:
                # Not a crash: the model said something that was not two
                # booleans, so no ballot is cast. A dropped vote beats a guessed one.
                stats["dropped"] += 1
                continue

            ballot["prompt_version"] = version
            r = httpx.post(f"{ac.API}/issues/{issue['id']}/vote",
                           headers=headers, json=ballot, timeout=60)
            if r.status_code == 201:
                stats["cast"] += 1
                stats[QUADRANT[(ballot["good"], ballot["bad"])]] += 1
            elif r.status_code != 409:                # 409 = already voted
                stats["failed"] += 1

            if n % 25 == 0:
                print(f"    {model}: {n} done", flush=True)
    except RuntimeError as exc:
        return {"model": model, "error": str(exc)}

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
            print(f"{f['slug']:<18}{f['open_issues']:>5} open   {f['name']}")
        return

    if args.pull:
        pull_all()

    if args.forums:
        # Check the names once, up front, rather than letting all five
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

    tokens = load_tokens()
    results = []
    for account, model in AGENTS:
        token = tokens.get(account)
        if not token:
            sys.exit(f"No token for {account}. Run: python make_test_tokens.py")
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
              "is the client refusing to guess, not an error.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nstopped")
