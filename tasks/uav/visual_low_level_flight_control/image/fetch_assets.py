"""Fetch a task's large agent-visible files from the benchmark's asset repository at image build time.

    python3 fetch_assets.py <assets.sha256> <dest_dir>

The canonical copy is `shared/task_assets/fetch_assets.py` in the benchmark repository. Every task that has assets
carries a byte-identical copy in its `image/` (the image's sole build context); `scripts/task_assets.py check`
enforces the identity.

`assets.sha256` (in image/, so in the build context) holds a header, then one `<sha256>  <relative path>` line per
file, as `sha256sum` writes them:

    # endpoint: https://huggingface.co
    # repo: grounded-intelligence-initiative/agentic-robotics-last-exam
    # revision: <40-hex commit of the dataset repository>
    # prefix: tasks/<platform>/<task name>/payload
    <sha256>  photos/photo_01.jpg

`endpoint` and `repo` are written from the repository's `task-assets.toml` by `scripts/task_assets.py sync`: change
that file, not these lines (an absent `endpoint` means https://huggingface.co). Every file is downloaded from
`<endpoint>/datasets/<repo>/resolve/<revision>/<prefix>/<relative path>` and written to `<dest_dir>/<relative path>`
only if its sha256 matches; any mismatch or failed download fails the build. Stdlib only (runs before any venv).
"""
import hashlib
import os
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = "https://huggingface.co"
URL = "{endpoint}/datasets/{repo}/resolve/{rev}/{path}"


def read_list(path):
    head, files = {}, []
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                k, _, v = line[1:].partition(":")
                head[k.strip()] = v.strip()
                continue
            digest, rel = line.split(None, 1)
            files.append((digest.lower(), rel.strip().lstrip("*")))
    for k in ("repo", "revision", "prefix"):
        if not head.get(k):
            sys.exit("assets list %s has no '# %s:' header" % (path, k))
    return head, files


def fetch(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001  (retried, then fatal)
            if k == tries - 1:
                sys.exit("download failed: %s (%s)" % (url, exc))
            time.sleep(5 * (k + 1))


def main():
    lst, dest = sys.argv[1], sys.argv[2]
    head, files = read_list(lst)
    endpoint = (head.get("endpoint") or DEFAULT_ENDPOINT).rstrip("/")
    for digest, rel in files:
        if os.path.isabs(rel) or ".." in rel.split("/"):
            sys.exit("unsafe path in assets list: %s" % rel)
        path = urllib.parse.quote(head["prefix"] + "/" + rel)
        data = fetch(URL.format(endpoint=endpoint, repo=head["repo"], rev=head["revision"], path=path))
        got = hashlib.sha256(data).hexdigest()
        if got != digest:
            sys.exit("sha256 mismatch for %s: %s != %s" % (rel, got, digest))
        out = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(data)
    print("[assets] %d file(s) from %s/%s@%s verified into %s" % (len(files), endpoint, head["repo"],
                                                                 head["revision"][:12], dest))


if __name__ == "__main__":
    main()
