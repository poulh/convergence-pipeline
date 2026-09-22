# Convergence Pipeline

A Claude Code skill (planned, not built yet) that turns a project idea into converged
requirements, an architecture, deliverables and milestone files before any code gets written.
It then builds one milestone at a time. `convergence-pipeline.md` holds the full design.

## Where things stand (2026-09-20)

- Working: `schema.sql`, `pipeline.yaml`, the `convergence` package, `pyproject.toml`,
  `setup.sh`, and the eleven agent charters in `.claude/agents/` plus `protocol.md`. The
  twelve `cp-*` tools run and their rules are tested end to end against the employee-hub
  walkthrough. The orchestrator skill is `.claude/skills/converge/SKILL.md`. No milestone
  template and no phase-3 tools yet, so phases 3 and 4 are described but not runnable.
- Charter file names match `agents.role` in `pipeline.yaml` and the `name:` in each
  charter's frontmatter; `protocol.md` is the shared turn protocol every charter points to.
- `./setup.sh` creates `.venv` and installs the package editable; re-running it is safe, and
  the orchestrator skill should run it when `.venv` is missing. Tools take `--db` or `$CP_DB`.
- `pipeline.yaml` holds both the agent roster and every enumerated vocabulary (concern kinds,
  requirement kinds, answer kinds, statuses, cost flags, link relations), each value with a
  natural-language description: the tools enforce the values, the descriptions help an agent
  pick the right one.
- `tools/init.py` validates the config and builds the database deterministically
  (`--check` validates only, `--force` replaces). It needs PyYAML, which is **not installed**
  on this machine yet.
- The design was substantially revised on 2026-09-19: shared state moved from a Markdown
  concerns log to a SQLite database, the interview merged into the convergence loop, the
  round budget removed, and a deliverables layer added above milestones.
- Design artifact (HTML version of the *pre-revision* design, now out of date):
  https://claude.ai/code/artifact/994f8496-641d-4112-83a1-106665c010fd
- `~/git/convergence-pipeline` is a symlink to this directory, not a second copy.

## The rule that governs the rest

**`pipeline.yaml` is the single source of truth**, and every rule is stored on the object it
describes — `requirement_kinds.goal.decided_by`, `answer_kinds.accepted.requires`,
`agents.ttm.owns`, `policy.stall_replies`. `cp-init` seeds the database; the tools read the
rules back out; the charters point at `cp-policy` instead of restating them. Nothing is
hardcoded in Python and nothing is duplicated in prose. Run `cp-doctor` after any edit — it
cross-checks the YAML, the charters, the skill and the database, and reports drift.

## Settled decisions

- **Four phases:** 1 Converge (concerns → requirements), 2 Architect (whole picture at once),
  3 Slice (deliverables → milestones), 4 Build (one milestone at a time).
- **SQLite, not a log file.** Tables: `agents`, `concerns`, `answers`, `requirements`,
  `requirement_concerns`, `deliverables`, `milestones`, `project_state`. Everything is
  stamped with its round.
- **"Concern", not "question"** — kinds: brief, question, risk, objection, proposal,
  staffing, appeal. Concerns are the conversation; requirements are the outcome; every
  requirement links back to the concerns that produced it.
- **One addressee per concern**, as a foreign key. No bitmask (considered and rejected: the
  database cannot validate a bitmask, and per-recipient state gets awkward).
- **Answers are typed**: accepted (names requirement ids), rejected (with reason), or
  escalated. The raiser sets `satisfied` and may `reply`; a follow-up is a new answer row with
  the same `concern_id`.
- **Status changes update the requirement row in place**; only a change to the `statement`
  text creates a new row with `supersedes_id`. `requirement_events` keeps the history, so a
  requirement deferred, revived and deferred again still reads back.
- **The table is called `concerns`** — considered renaming it after the scope grew to include
  brief, staffing and appeal kinds, and kept it: "raise a concern" prompts a better-formed
  row from an agent than a neutral name like `threads` would.
- **The tables are the state; documents are printouts.** `requirements.md` and
  `milestone-N.md` are generated. Only `architecture.md` is authored, by Arty.
- **The goal lives in `requirements`** as rows of kind goal / non_goal / success_criterion,
  revised through `supersedes_id`. There is no separate brief table; the human's original
  words are concern #1, kind `brief`.
- **No round budget.** The loop pauses only when a concern is addressed to the human, and
  finishes when every active agent signs off on the requirement set.
- **Agents join on triggers**, recorded in the `agents` table (`join_trigger`,
  `join_rationale`) — that table is the project's config file. Round 1 is the human, Peter
  and the Scribe only.
- **Staffing needs the human's approval.** A staffing concern goes to the human with its
  trigger and its likely cost; the human approves the agent, defers the triggering
  requirement, or drops it. `auto_staff` defaults to 0.
- **Peter decides ordinary requirements and disputes between others**; the human decides the
  goal, invariants, feature cuts, staffing, disputes Peter is party to, and appeals. Any agent
  may appeal one Peter decision once, straight to the human.
- **The Scribe has no opinions** — records, routes, reports, escalates. Split out of Peter so
  that nobody reports on their own decisions.
- **Arty joins early** (as soon as a feature list exists) because invariants decided late mean
  rework. He writes concerns, invariants and cost flags each round; the architecture document
  comes once, in Phase 2.
- **Dana is not in the requirements loop.** He checks milestone files for buildability at the
  end of Phase 3, then builds in Phase 4.
- **The human picks each milestone to build** and tests after each merge. Phase 4 never runs
  "everything".

## Open threads

1. K — rounds of back-and-forth before a thread counts as stalled.
2. The spend cap, and what the Scribe reports when it trips.
3. Whether the Scribe checks in on a cadence or only when something needs the human.
4. Whether dormant agents wake automatically when their area is touched again.
5. Where the database lives relative to the project repo, and whether it is committed.

## The tools

Agents never write SQL; the rules live in the tools, not in the prompts.

| Tool | What it does / refuses |
|---|---|
| `cp-init` | Validates `pipeline.yaml`, builds the database. `--check`, `--force` |
| `cp-queue` | An agent's turn: concerns to answer, answers to review, requirement changes since `last_seen_change`. `--json`, `--mark-seen` |
| `cp-concern` | Raise one. Refuses `staffing` (use `cp-staff`), an appeal aimed anywhere but the human, self-addressing, inactive agents |
| `cp-answer` | Refuses anything that is not accepted-with-requirement-ids, rejected-with-a-reason, or escalated-to-someone. Escalation reassigns the concern |
| `cp-review` | The raiser judges an answer. A "no" needs a reply; a "yes" resolves the concern |
| `cp-propose` | New requirement. Cost flags are the architect's only. `--supersedes` rewords |
| `cp-decide` | Refuses: a goal/non_goal/success_criterion/invariant decided by anyone but the human; Peter cutting something the human asked for (including via their answers); a defer/reject with no reason |
| `cp-staff` | `request` (states trigger and cost, goes to the human), `approve`, `decline` — human only |
| `cp-signoff` | Refuses while the agent still has mail |
| `cp-state` | Round, phase, what it is paused on, who has not signed off, whether it has converged |
| `cp-round` | `--advance` refuses while a concern sits with the human; `--phase N` moves phase |
| `cp-deliverable` | `propose` (owns deliverables), `decide` (rules_on) |
| `cp-milestone` | `propose`, `decide`, `review --ok` (reviews slicing), `check --ok` (checks buildability), `assign` |
| `cp-render` | Generates `requirements.md`; sections come from the vocabularies |
| `cp-policy` | The rules in force: thresholds, who decides what, duties. `--agent <role>` for one agent |
| `cp-doctor` | Cross-checks config, charters, skill and database for drift |

## Next steps

1. Write `cp-render milestone` and the `milestone-N.md` template (only `cp-render
   requirements` exists).
2. Add the phase-3 tools for deliverables and milestones — nothing writes to those tables yet.
3. Dry-run phase 1 on one real, low-stakes project.
