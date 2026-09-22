"""Every t("...")/tn("...")/msg("...") key used in the code exists in the texts,
and every text is used somewhere. Run: python3 tools_check_texts.py [file-or-dir]
(default: texts.toml; a directory means all *.toml parts in it, merged)."""
import pathlib, re, sys, tomllib
root = pathlib.Path(__file__).parent
src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else root / "texts.toml"
files = sorted(src.glob("*.toml")) if src.is_dir() else [src]
keys = {}
def flat(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict): flat(v, p + k + ".")
        else: keys[p + k] = v
for f in files:
    flat(tomllib.loads(f.read_text(encoding="utf-8")))
USE = re.compile(r"""\b(t|tn|msg)\(\s*["']([a-z0-9_.]+)["']""")
# Keys built at run time, like t("issue.quadrant_%s" | format(q)): every key
# with that prefix counts as used.
DYN = re.compile(r"""\b(?:t|tn|msg)\(\s*["']([a-z0-9_.]+)%s""")
used = set()
for f in [p for p in (root / "app").rglob("*.py") if p.name != "texts.py"] + list((root / "app" / "templates").glob("*.html")):
    for kind, k in USE.findall(f.read_text(encoding="utf-8")):
        if kind == "tn":
            used |= {k + "_one", k + "_other"}
        else:
            used.add(k)
prefixes = set()
for f in [p for p in (root / "app").rglob("*.py") if p.name != "texts.py"] + list((root / "app" / "templates").glob("*.html")):
    prefixes |= set(DYN.findall(f.read_text(encoding="utf-8")))
used = {k for k in used if k not in prefixes} | {k for k in keys if any(k.startswith(p) for p in prefixes)}
missing = sorted(used - keys.keys()); unused = sorted(keys.keys() - used)
print(f"{len(keys)} texts, {len(used)} used")
if missing: print("MISSING:", *missing, sep="\n  ")
if unused: print("UNUSED:", *unused, sep="\n  ")
sys.exit(1 if missing else 0)
