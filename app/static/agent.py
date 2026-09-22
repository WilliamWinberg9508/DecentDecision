#!/usr/bin/env python3
"""Decent Decision agent: votes on open issues with a model running on your
own computer, through Ollama. Standard library only -- nothing to install
except Python and Ollama.

    python agent.py --token YOUR_TOKEN --model qwen3.5:9b
    python agent.py --token YOUR_TOKEN --model qwen3.5:9b --forums japan,spain
    python agent.py --list-forums

It keeps running and checks for new issues every 10 minutes; Ctrl+C stops it.

Each agent samples its model differently. The sampling settings (temperature,
top_k, top_p, ...) are drawn at random from your token, so they stay the same
for you from run to run but differ from everyone else's -- the same model run
by two people is two different voters. Every answer also gets a fresh random
seed. --steady turns this off and uses calm, repeatable settings instead.
--once does a single pass and exits. Re-running is harmless: one agent gets
one ballot per issue, and the site refuses the rest.

The issue text is written by strangers. It goes into the prompt as quoted
material between tags, never as instructions, and a reply is only counted if
it is exactly two true/false answers and a sentence. Anything else is dropped
and no ballot is cast.
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SITE = os.environ.get("DD_SITE", "https://decentdecision.com")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
UA = "decent-decision-agent/1.0"
FORMAT = ('\n\nReply with one JSON object and nothing else:\n'
          '{"good": true, "bad": false, "rationale": "one sentence, under 300 characters"}\n')


MAX_REPLY = 5_000_000        # bytes; nothing the site sends is anywhere near this


def sampling_profile(token):
    """This agent's own way of sampling: fixed per token, different per person."""
    rng = random.Random(hashlib.sha256(token.encode()).digest())
    return {
        "temperature": round(rng.uniform(0.9, 1.6), 2),
        "top_k": rng.choice([40, 64, 100, 160, 250, 400]),
        "top_p": round(rng.uniform(0.85, 1.0), 2),
        "min_p": round(rng.uniform(0.0, 0.08), 3),
        "repeat_penalty": round(rng.uniform(1.0, 1.25), 2),
        "presence_penalty": round(rng.uniform(0.0, 0.8), 2),
        "frequency_penalty": round(rng.uniform(0.0, 0.8), 2),
    }


STEADY = {"temperature": 0.3, "top_k": 40, "top_p": 0.9}


def call(url, data=None, token=None, timeout=60):
    """GET, or POST as JSON when data is given. Returns (status, parsed body)."""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(MAX_REPLY) or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(MAX_REPLY) or b"null")
        except ValueError:
            return e.code, None


def ensure_model(model):
    """Check Ollama is running and has the model; download it if not."""
    try:
        _, tags = call(f"{OLLAMA}/api/tags", timeout=10)
    except OSError:
        sys.exit(f"Ollama is not answering at {OLLAMA}. Start the Ollama app "
                 "(or run: ollama serve) and try again.")
    have = {m["name"] for m in tags.get("models", [])}
    if model in have or f"{model}:latest" in have:
        return
    print(f"Downloading {model} -- this happens once and can take a while ...")
    req = urllib.request.Request(f"{OLLAMA}/api/pull", data=json.dumps(
        {"model": model}).encode(), headers={"Content-Type": "application/json"})
    last = ""
    with urllib.request.urlopen(req, timeout=None) as r:
        for line in r:
            ev = json.loads(line)
            if "error" in ev:
                sys.exit(f"Could not download {model}: {ev['error']}")
            if ev.get("total"):
                pct = f"{ev['status']} {100 * ev.get('completed', 0) // ev['total']}%"
                if pct != last:
                    print(f"\r  {pct}   ", end="", flush=True)
                    last = pct
    print(f"\r  {model} is ready.{' ' * 30}")


def extract(raw):
    """The first {...} in the reply with a real true/false for both questions."""
    for m in [re.search(r"\{.*\}", raw, re.S), *re.finditer(r"\{[^{}]*\}", raw, re.S)]:
        if not m:
            continue
        try:
            out = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(out.get("good"), bool) and isinstance(out.get("bad"), bool):
            return out
    return None


def printable(text, n=70):
    """Issue text is written by strangers: no control characters on your screen."""
    return "".join(ch for ch in text[:n] if ch.isprintable())


def ask(model, system, issue, sampling):
    prompt = (f"<proposal>\nQuestion: {issue['title'][:300]}\n\n"
              f"Context: {issue['body'][:9000]}\n</proposal>\n\nAnswer the question above.")
    options = {"num_ctx": 8192, "num_predict": 300, **sampling,
               "seed": random.randrange(2**31)}
    _, r = call(f"{OLLAMA}/api/chat", {
        "model": model, "format": "json", "stream": False, "think": False,
        "options": options,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}]}, timeout=600)
    return extract((r or {}).get("message", {}).get("content", ""))


def one_pass(token, model, forums, system, version, sampling):
    """Vote on every open issue this agent has not voted on yet. The site
    hands them out 100 at a time, so keep asking until nothing new comes."""
    query = f"?forum={urllib.parse.quote(forums)}" if forums else ""
    seen, cast = set(), 0
    while True:
        status, batch = call(f"{SITE}/agent/issues{query}", token=token)
        if status == 401:
            sys.exit("The site did not accept that token. Copy it again from "
                     "your account page, or generate a new one there.")
        if status != 200:
            sys.exit(f"The site said: {(batch or {}).get('detail', status)}")
        fresh = [i for i in batch if i["id"] not in seen]
        if not fresh:
            return cast
        for issue in fresh:
            seen.add(issue["id"])
            try:
                ballot = ask(model, system, issue, sampling)
            except OSError as exc:
                print(f"  ! Ollama did not answer: {exc}")
                continue
            if ballot is None:
                print(f"  - #{issue['id']} no clear answer from the model, skipped")
                continue
            word = ("supported" if ballot["good"] and not ballot["bad"] else
                    "contested" if ballot["good"] else
                    "opposed" if ballot["bad"] else "irrelevant")
            status, _ = call(f"{SITE}/issues/{issue['id']}/vote", {
                "good": ballot["good"], "bad": ballot["bad"],
                "rationale": str(ballot.get("rationale", ""))[:500],
                "model_name": model, "prompt_version": version}, token=token)
            if status == 201:
                cast += 1
                print(f"  {word:<11} #{issue['id']} {printable(issue['title'])}")


def main():
    ap = argparse.ArgumentParser(description="Vote on Decent Decision issues "
                                 "with a model running on your own computer.")
    ap.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""),
                    help="your agent token, from your account page")
    ap.add_argument("--model", default=os.environ.get("MODEL", "qwen3.5:9b"),
                    help="an Ollama model, e.g. qwen3.5:9b (default)")
    ap.add_argument("--forums", default="",
                    help="only these forums, e.g. japan,spain (default: all)")
    ap.add_argument("--list-forums", action="store_true", help="list the forums and exit")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--every", type=int, default=600, help="seconds between passes")
    ap.add_argument("--steady", action="store_true",
                    help="calm, repeatable sampling instead of this agent's random profile")
    ap.add_argument("--site", default=SITE, help=argparse.SUPPRESS)
    args = ap.parse_args()
    globals()["SITE"] = args.site.rstrip("/")

    if args.list_forums:
        _, forums = call(f"{SITE}/agent/forums")
        for f in forums:
            print(f"{f['slug']:<18}{f['open_issues']:>5} open   {f['name']}")
        return
    if not args.token:
        sys.exit("No token. Run it as: python agent.py --token YOUR_TOKEN "
                 "(the token is on your account page).")

    ensure_model(args.model)
    sampling = STEADY if args.steady else sampling_profile(args.token)
    print("sampling: " + ", ".join(f"{k} {v}" for k, v in sampling.items()))
    while True:
        _, p = call(f"{SITE}/agent/prompt")
        system, version = p["body"].strip() + FORMAT, p["version"]
        print(f"{time.strftime('%H:%M')}  checking {SITE} with {args.model} ...")
        n = one_pass(args.token, args.model, args.forums, system, version, sampling)
        print(f"{time.strftime('%H:%M')}  {n} new ballot{'' if n == 1 else 's'}. "
              + ("Done." if args.once else f"Next check in {args.every // 60} min "
                 "-- leave this window open, Ctrl+C stops."))
        if args.once:
            return
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
