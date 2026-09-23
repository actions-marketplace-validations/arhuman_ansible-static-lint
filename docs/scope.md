# The static frontier, measured

astl implements the ansible-lint rules that can be decided from the YAML
source alone: 39 of the 51 built-in rule IDs. This document quantifies what the
other 12 would cost, so that "static only" is a measured boundary rather than
a slogan.

## Method

On ansible-lint's own test corpus (478 YAML files), ansible-lint 26.8.0
(`--offline`) reports **2648 findings**. Filtering that output
to the 39 rules astl implements leaves **2398 findings**; the frozen golden
carries 2387 of them and astl reproduces all 2387 byte for byte (see the
compatibility harness; the 11-finding difference is environment drift in
upstream itself, accounted below). The gap analyzed here is the remaining
**250 findings**: the per-rule distribution below is computed by subtracting
the in-scope output from the full output, and the "what it requires" column
comes from reading each upstream rule's implementation and imports, not from
guessing.

## Distribution of the 250 out-of-scope findings

| What reproducing the rule requires | Rules | Findings |
|---|---|---|
| Running Ansible's loader: syntax check, module and role resolution | `syntax-check`, `fqcn`, `args`, `internal-error`, `load-failure` | 131 |
| Upstream's JSON Schema bundle | `schema` | 56 |
| Evaluating Jinja templates with Ansible's filters | `jinja` | 31 |
| Ansible's argument splitter semantics as data | `no-free-form` | 30 |
| Upstream environment drift: module resolution changes what one rule sees | `risky-file-permissions` | 11 |
| Nothing on this corpus: three are opt-in, one matches no fixture | `deprecated-module`, `no-same-owner`, `only-builtins`, `role-argument-spec` | 0 |
| Output artifact of the corpus run | | 1 |

## Reading the table

- **99% of the gap requires capabilities astl deliberately does not have.**
  `syntax-check` is a subprocess running `ansible-playbook`; `fqcn` resolves
  module names through Ansible's plugin loader; `args` validates against
  module argument specs; `internal-error` and `load-failure` are Ansible's own
  loader failures surfaced as findings. `jinja` evaluates templates with
  Ansible's filter set, and `schema` validates against the JSON Schema bundle
  upstream maintains.
- **`yaml[*]` and `var-naming` moved inside the boundary in 2026-08.** Both
  were previously excluded, `yaml` because upstream delegates it to an
  embedded yamllint and `var-naming` because it tracks ansible-core internals
  as data. Real-world CI evidence showed those two families dominate what
  actually blocks pipelines, so astl now ports yamllint's checks (over its own
  pyyaml-equivalent scanner) and vendors var-naming's reserved-name tables.
  A repository's own `.yamllint` is honoured, layered over ansible-lint's
  bundled policy the way ansible-lint layers it. The design and the parity
  evidence are in
  [docs/design/static-yaml-and-var-naming.md](design/static-yaml-and-var-naming.md).
- **Four rules cost nothing measurable here, which is not the same as being
  free.** `no-same-owner`, `only-builtins` and `role-argument-spec` are opt-in,
  so a default run never raises them, and `deprecated-module` matches no fixture
  in this corpus. They are listed at 0 so the twelve unsupported IDs can all be
  found in one place; on a repository that enables the opt-ins or uses a
  deprecated module, they would report. `deprecated-module` needs the module
  list ansible-core ships, the same class of vendored data `var-naming` uses,
  and is the cheapest of the twelve to add.
- **`no-free-form` stays out for now** on the same argument-splitter grounds
  that `var-naming` used to sit on; it is the next candidate of that class.
- **`latest` moved inside the boundary in 2026-09**, and with it the last row
  that required nothing but the work. Every finding left in the table above now
  names a capability astl does not have, rather than one it has not written.
- **The `risky-file-permissions` lines measure upstream, not astl.** The same
  pinned ansible-lint version reports 11 findings more or fewer on one fixture
  depending on which collections its environment resolves. The frozen golden
  pins one environment's answer; astl agrees with it.

## What astl gains at the same boundary

Because it has no runtime to fail, astl keeps linting files whose references
Ansible cannot resolve. ansible-lint abandons such files and reports only the
load failure; astl reports what the YAML says. On the corpus this produces 48
findings ansible-lint does not emit, all on files upstream's embedded runtime
rejects. They are pinned line for line as an exact set in the compatibility
harness, which fails if the set changes in either direction, so a genuine
false positive cannot hide among them.

The harness, the corpus, the frozen golden output, and the classification of
every extra finding live in the
[astl-compatibility-check](https://github.com/arhuman/astl-compatibility-check)
repository (GPL-3.0-or-later, because it contains ansible-lint test data;
astl itself carries none of it and stays MIT). Its `PARITY.md` also documents
every deliberately emulated upstream quirk, including the stray `[/]` that
ansible-lint's rich markup leaks into pep8 tags.
