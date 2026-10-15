<!-- Thanks for contributing to the Agentic Robotics Benchmark. Keep what applies. -->

## What this pull request adds

One task folder at `tasks/<platform>/<slug>/`. One or two sentences on the robotics problem
and the artifact the agent has to leave in `/home/user/submission/`.

## Checklist

- [ ] The folder sits at `tasks/<platform>/<slug>/`. `<slug>` equals `name` in `task.yaml`
      and `<platform>` equals `metadata.platform`.
- [ ] `ale lint <folder>` passes on the engine commit pinned at `vendor/ale`.
- [ ] `ale validate <folder>` passes. The `ok` line from my run:

  ```
  ok    <slug>: untouched={'reward': 0.0}, oracle={'reward': 1.0}
  ```

- [ ] `verify/anchor.json`: `value` is my own measured number from that run, never a
      published figure; `source` says where it came from; `full_at` is sized from the spread
      of several oracle runs.
- [ ] `verify/robotics_grader/` is the canonical copy from `shared/robotics_grader/`, unmodified.
- [ ] Nothing under `setup/payload/` reads the anchor or `verify/`; the practice grader prints
      the raw metric only.
- [ ] No `__pycache__`, `.ale-cache`, `assets/` or large binaries in the folder. Large data is
      fetched by `image/Dockerfile` at build time.
- [ ] `metadata.platform` is one of the keys in `tasks/platforms.json` (a new platform needs a
      maintainer: say so below), and `verify/anchor.json` declares `metric` and `direction`;
      the grader reports that metric under the same name (`metric_name=` in `verify/verify.py`).

## Notes for the maintainers

How long the oracle runs, GPU needs, licences of fetched assets, anything else a reviewer
should know before running `ale validate`.
