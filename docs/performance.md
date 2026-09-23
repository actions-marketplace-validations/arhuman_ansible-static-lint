# Performance

Why astl is fast, what that costs, and what guards the property.

## Measurements

Measured on Apple Silicon macOS against ansible-lint 26.8.0
(Python 3.14, `--offline`), on ansible-lint's own examples corpus. Reproduce
them with `make bench-compare` in the
[compatibility repository](https://github.com/arhuman/astl-compatibility-check),
which builds astl, installs the pinned upstream toolchain in a throwaway venv
and runs every row above under hyperfine:

| Metric | ansible-lint | astl (39 rules) |
|---|---|---|
| Cold start (`--version`) | 0.49 s | 3.3 ms |
| One 6-line playbook | 2.0 s | 3.6 ms |
| 478-file corpus | 39.5 s | 59 ms |
| Max RSS on the corpus | 129 MiB | 42 MiB |

Read the ratios with care: the comparison is asymmetric, since ansible-lint is
also running its syntax-check subprocess and the 12 rules astl excludes. The
honest headline numbers are cold start and the single playbook, where the gap
is interpreter and import overhead that exists before any rule runs; even
`ansible-lint --version` costs half a second.

## Where the time goes

The number that matters architecturally: the 36 rules that work off the parsed
document produce no measurable slowdown between them, because a rule is a
predicate over an already-parsed document and its marginal cost is
microseconds.

The one rule family that does cost something is `yaml[*]`, which runs a second,
token-level scan of every file for the yamllint checks. After streaming that
scan through a fixed four-token window and adopting a
lazy-GC-under-a-memory-ceiling posture suited to a lint-and-exit process
(`GOGC`/`GOMEMLIMIT` in the environment still win), the corpus sits at 59 ms
against 31 ms before the family existed.

The memory ceiling keeps the trade bounded: 42 MiB on the corpus, under 100 MiB
on a 4000-file monorepo. astl's cost remains startup, I/O and the two parses,
and stays nearly independent of how many static checks run on top.

## The guard

That property is guarded, not assumed: `make bench` fails the build if linting
the reference corpus exceeds 150 ms, now roughly 2.5x the current time. It
runs in CI on every pull request, and `make ci` includes it.

`make perfguard` is the second speed guard, asserting that noqa resolution stays
linear. It sits behind the `perfguard` build tag because it reads wall-clock
time, so it never gates the audit.
