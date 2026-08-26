# Changelog

All notable changes to Modelpin are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (breaking)
- **`--fixtures` is now required with `--provider fake`.** The fake provider no longer
  fabricates a trace when it has no canned one: it raises `MissingCannedTrace` and the CLI
  exits 1 with an error naming the flag. Previously a bare `mp baseline --provider fake`
  produced placeholder traces that were structurally indistinguishable from real ones, so a
  run could report `unchanged` — "safe to adopt" — about behaviour that was never measured.
  Any scripted offline invocation that omitted `--fixtures` will now fail. Pass
  `--fixtures <file>`; `modelpin init --demo` writes one.
  **If you ever ran a fixture-less `baseline` on 0.1.2, delete the stored baseline and
  re-record.** That run wrote placeholder traces to `.modelpin/baseline-*.json`, and this
  release does not detect them there — the guard is on the replay path, not the store. A
  fully correct `check --fixtures` against such a baseline reports regressions at
  confidence 1.00 that did not happen. See ADR-0015.

### Changed
- **The detection arm of `scripts/fp_measurement.py` now publishes a confidence interval, as
  the false-positive arm already did.** The two arms were asymmetric in the direction that
  flatters detection. `[M]` `fp_summary` printed `  False-positive rate: {fp}/{scored} =
  {rate}` *and* a one-sided 95% Clopper-Pearson **upper** bound; `recall_summary` printed only
  `  Detection: {d}/{checked} injected perturbations caught` — no interval — even though
  `README.md` and `docs/fp-measurement.md` both already published the **lower** bound. The
  tool understated what the documents conceded, and it is the tool a user runs.
  `[M]` The floor is the same helper by complement, `1 - upper_bound_95(misses, checked)`:
  the run of record, **2 of 3**, bounds the true rate at **13.5%**; a hypothetical `3/3`
  bounds it at only **36.8%**. `[M]` Nothing about the accounting moved — numerator,
  denominator and both exclusions are unchanged, `git diff` touches three hunks in that file
  (two docstrings and `recall_summary`), and no tally assertion in the suite was edited.
  **This arm** still prints no point percentage: `3/3 = 100%` would be the withdrawn
  `0/8 = 0%` mirrored (ADR-0022). The FP arm does print `= {rate}` beside its fraction —
  ADR-0022 kept it, with the bound and the exclusion counts alongside — so the two arms are
  now symmetric on the *interval*, not on the point estimate. The bound is over
  **perturbations applied**, not over behaviour changes — the denominator includes the case
  the model resisted, and ADR-0023 forbids this arm asserting a perturbation changed
  behaviour — and it carries the exchangeability caveat both documents carry: three
  perturbations chosen one per signal are by construction not exchangeable trials.
  No interval is printed when nothing was checked, `[M]` the one shape the document's
  verbatim quote provably could not see.
- **The false-positive rate now excludes trials that could not have produced a false alarm —
  and the previously published `0/8` result is WITHDRAWN.** `scripts/fp_measurement.py` counted
  a scenario as a passed trial even when the engine measured no effect on any channel. `[M]`
  `diff/stats.py:128-129` early-exits at `p=1.0` when two sides are distributionally identical,
  so such a trial scores `unchanged` *regardless of any change to the engine* — counting it as
  clean credits the engine for a test it could not fail. The temperature-0 held-out suite
  therefore published `0/8 = 0%`; re-scored (**not** re-run — no API call was made) the same
  recorded verdicts give **`0/0 = n/a`**, whose 95% upper bound is unbounded. A trial is now
  excluded iff the engine reports `unchanged` at confidence exactly 1.0 — under ADR-0001 that
  confidence is `min(p)`, so it holds precisely when nothing could have fired at ALPHA. This is
  **sound by construction**: a flagged verdict never carries `unchanged`-confidence, so the
  exclusion can never remove a false positive. Excluded counts are printed beside the rate,
  never folded into it.
  `[M]` The same correction applies to the golden-test row: the 4 "noisy-but-equivalent" pairs
  in `tests/test_diff.py` all return `unchanged` at confidence 1.0000, so `0/4` is also `0/0`.
  Two further rows in that table have not been re-scored and must not be quoted as an FP rate.
  The engine did not change — `git diff` over `modelpin/` for this work is comment-only, and no
  threshold moved. Only the accounting, and the claims resting on it, changed.
- **The published confidence interval was wrong for every non-zero result.** The harness printed
  `1 - alpha**(1/n)`, which is the Clopper-Pearson upper bound *only* at k=0. `[M]` at 1/8 it
  understated 47.1% as 31.2%; at 4/8 it printed 31.2% — an upper bound **below** the 50%
  observed rate. Replaced with an exact bisection bound.
- **`MIN_SEMANTIC_DELTA`'s evidence is restated from three independent conditions to one, and
  that one is weaker than first published.** The held-out re-validation contributed 0 scored
  trials, and `[M]` the two calibration runs share their scenarios and perturbations and both
  record `"judge": "gpt-4o-mini"`, differing only in the candidate model — so the judge, which
  is what this floor gates, provably did not vary. `[M]` Of the surviving run's 6 equivalent
  pairs, **5 return `p = 1.00`** and could not have fired, so the evidence is **0/1 (95% upper
  bound 95.0%)**, not 0/6 at 39.3% — that figure was itself the pre-MP-75 accounting, corrected
  here. The threshold value is unchanged; only the claimed evidence for it.

- **`mp baseline`, `mp check` and `mp report` state how many replays a run will make,
  before making them.** The pre-spend line was `provider=… model=… runs=…`; it now also
  carries `N scenario(s) from <dir> -> M replays, >=M paid calls`, and
  `+ up to 2M judge calls` when a `judge_model` is configured. The `>=` is deliberate: one
  replay is one adapter call, but a `kind: agent` scenario's replay drives a tool loop of up
  to `MAX_TOOL_TURNS` completions, so replays are a **floor** on the paid calls of a run that
  completes, never a ceiling. (A run cut short — a provider error, a skipped scenario — bills
  less.) `mp check` counts only scenarios that have a baseline, since the rest are skipped
  rather than replayed. `--provider fake` claims no cost at all — that path replays canned
  traces and bills nothing. See ADR-0019.
- **`mp report` discloses a two-sided run as two-sided.** It replays `--from` *and* `--to`,
  so its line reads `N scenario(s) from <dir> x 2 models -> M replays` and its replay and
  paid-call figures are **twice** what the same suite costs under `mp check` — exactly twice
  when every scenario has a baseline, since `check` counts only those. `[M]` the 14-scenario
  `examples/report-suite` queues **140** replays
  (`modelpin report --to B --from A --suite-dir examples/report-suite`, 14 × `runs: 5` × 2
  models). Before this release that line named the suite directory but not its size, so the
  user saw `runs=5` with no hint that 140 replays were queued, and learned the scenario count
  only from the results table — after the calls had been billed. The judge figure is
  deliberately **not** doubled: for `report` the semantic judge scores every run on both
  sides whether or not both were replayed live, so it is bounded by `2 × scenarios × runs`,
  the same figure `mp check` discloses over the same suite. The header also drops its
  now-redundant `suite=<dir>` token, which the disclosure line names.
- **`mp init` names the scenario set it adopted.** When a `modelpin.yaml` already exists,
  `init` honours its `scenarios_dir` (0.1.2 behaviour, unchanged) but now also prints how
  many scenarios that directory holds and where the config is, so an adopted config is
  visible before `mp baseline` acts on it.
- **The detection arm of `scripts/fp_measurement.py` is now a pure, tested function, and it
  counts provider errors instead of skipping them.** (Developer harness: `scripts/` ships in
  neither the wheel nor the sdist, so nothing about the installed package changes.) The arm was
  inline in `main()`, which needs a live provider, so no test reached it: `[M]` at `58c87dc`,
  eight of nine mutants applied to that inline arm left the then-302-test suite green —
  including `detected += int(caught)` -> `detected += 1`, which makes the harness print
  `Detection: 3/3` for an engine that flagged nothing, and replacing the entire loop body with
  `continue`, which makes it print `0/0`. The accounting now lives in `recall_outcome` /
  `recall_tally` / `recall_report` / `recall_summary` plus a shared `build_row`, with 30 tests
  over them — the same treatment the false-positive arm received in this release.
  `[M]` **Those original nine cannot be re-run: the extraction deleted every line they mutate.**
  A fresh **39-mutant** battery at the new anchors, covering all nine original defect classes,
  kills **38**. The one survivor deletes a source *comment* (the rationale block above
  `PERTURBATIONS`); comments are not pinned, and the vocabulary it explains is pinned
  behaviourally instead. Three of those 38 close a hole that was **symmetric in the
  false-positive arm and older than this change**: deleting either arm's per-scenario `print`
  loop, or discarding its lines at the call site, left the whole suite green — the lines'
  content was covered, their consumption was not. **The engine did not change** — `git diff`
  over `modelpin/` for this work is empty, and no calibrated constant moved.
  Three behaviour changes in the harness itself. A missed perturbation, which was already
  labelled `MISSED` per scenario and already counted in the denominator, is now also counted out
  in the summary with a note saying a miss is never an exclusion — and saying *why*: the harness
  cannot tell a candidate that resisted the injected instruction from an engine that is dead, so
  it always counts the miss and asserts nothing about whether behaviour changed (ADR-0022,
  whose closing rationale this corrects; ADR-0023). A run where every perturbed scenario errored,
  or abstained, now prints a banner saying so instead of a bare `0/0`. And provider errors,
  previously printed as they occurred but left out of the accounting, are now counted and
  published beside the fraction.
  The arm also stops calling the injected instructions "regressions". `[M]` 1 of the 3 in
  `PERTURBATIONS` (`decline_pii`) returned `unchanged` on the run of record; reading that trace
  by hand, the model still declined and emitted no email — **an analysis, not a measurement**,
  since the arm cannot tell resistance from a dead engine. So whether a perturbation is a
  regression is the thing being measured, not a premise. `README.md` already used this
  vocabulary; the harness now matches it.
- **`docs/fp-measurement.md` published a detection number the harness never printed, and it is
  now corrected.** The document stated *"Detection: every perturbation that actually changed
  behavior was caught"* and concluded *"2/2 real behavior changes were flagged"*. `[M]` The
  harness scored that same run **2/3**, before and after the change above: the document's own
  table lists three injected perturbations and shows `decline_pii` as `unchanged`, which
  `recall_outcome` maps to a **miss**. The third was removed from the denominator by argument —
  the model resisted the instruction and still declined, so behaviour did not change — which is
  exactly the exclusion **ADR-0023** rejects, and for the reason it gives: a perturbed pair
  reading `unchanged` is *either* an engine that failed to see a real change *or* a candidate
  that ignored the instruction, and this arm cannot tell them apart, so an arm permitted to
  exclude on that basis lets a dead engine post `0/0`. The document now publishes what the
  harness prints — `2/3`, the verbatim summary block including the `MISSED` note, and `[M]` the
  95% one-sided *lower* bound of **13.5%** — with the resistance reading demoted to a labelled
  correction note beneath it. `README.md` has carried this framing (*"2 of 3 injected
  perturbations were flagged"*) since the withdrawal above; **its interval sentence is
  corrected here too** — it published a 95% lower bound *for the withdrawn `2/2` denominator*
  (`22.4% at 2/2, 13.5% at 2/3`), offering the excluded reading as a co-equal alternative on
  the surface most readers meet first. `README.md` sends the reader to
  `docs/fp-measurement.md` as the "Full writeup".
  `[M]` The same defect appeared **twice more in the same document** and both are fixed here:
  `## Detection (control)` called the injections "three controlled regressions" and attributed
  `decline_pii` a *"(comply → … semantic change)"* — the forbidden assertion, stated as fact, on
  the one scenario the run shows the model resisted, together with an `Expected verdict` line
  naming the channel a reader would loosen to close the gap; and the Phase-0 DoD line read
  "detection met" with no interval and no mention of the miss. The published interval now
  carries the command that produces it (`1 - upper_bound_95(1, 3)`) and the note that
  Clopper-Pearson treats the three perturbations as exchangeable trials, which by construction
  they are not.
  **No number the tool computes changed** — `git diff` over `modelpin/` and `scripts/` for this
  work is empty. `[M]` Eleven tests tie the document to the function whose output it quotes,
  pinning the quoted block, the prose headline, the interval, the scenario set, the correction
  note's existence, the arm's one exclusion, the README's own detection fraction and test
  count, and a perfect-score tripwire. Two batteries, **30 mutants**, run against the final
  tree: 15 structural ones all red — including a fully self-consistent forgery that edits the
  table, the fraction, the recomputed interval and the note together — and 15 rewording ones,
  6 of which survive and are named below rather than left for a reader to discover.
  `[M]` **Seven holes found and closed, every one by RUNNING the battery rather than reading
  the tests** — recorded because the pattern is the point: a guard that reads as coverage and
  provides none is this project's recurring defect, and these guards exhibited it seven times
  before they stopped. The banned-claim scan split its section positionally, so re-asserting a
  withdrawn claim *below* the correction note passed; the ban was one-directional, refusing
  `2/2` while allowing the flattering `3/3`; after both fixes it was still section-scoped, so
  the same sentence one line *above* the headline passed; it then stripped *every* blockquote,
  so a `> **In brief.**` pull-quote re-asserted all three withdrawn claims in the most-read
  position on the page; then it stripped blockquotes *adjacent to* a withdrawal note too,
  because a blank line did not end the note; the `3/3` ban was a bare substring that misfired
  on ordinary fractions like `13/30` **and** was unsatisfiable beside the verbatim guard — a
  legitimate perfect score could not be published at all, and the cheapest repair would have
  silently gutted the scan; and finally the replacement pattern for *"a miss is a false
  negative"* was written through a shell heredoc that collapsed `\b` into the **backspace
  control character**, so the guard added specifically to catch that claim contained three
  literal `0x08` bytes and could never match anything. It passed review, and it passed its own
  green suite, while asserting nothing at all.
  The scan is now whitespace-normalised and emphasis-stripped, because `[M]` the assertion it
  was written to catch was sitting in the file the whole time — 65 lines below its own
  correction, invisible to a substring match for two independent reasons: markdown emphasis
  (`*negative*`) and a hard line wrap between "is" and "a false". The tripwire is now a state
  assertion over the document's own tally, so a legitimate perfect score turns exactly one test
  red: the deliberate review ADR-0023's falsifier calls for.
  **What these tests still cannot do**, stated because they read stronger than they are: they
  do not prove the table matches a run that happened — no artifact of the detection run is
  committed (MP-83). `[M]` **Six mutants still survive, all of them prose deletions rather
  than number forgeries:** dropping the `0/1 — 95% upper bound 95.0%` figure or the not-fitted
  gloss from `README.md`; removing the exclusion's soundness property, the `0 of 8` / `1 of 6`
  planning figures, or restoring "the conservative floor earns its keep" in
  `docs/fp-measurement.md`; and re-asserting a withdrawn claim *inside* a `> **Corrected**`
  note, where quoting it is the point and no guard can distinguish intent. Pinning every
  sentence of a document from a unit test is the wrong shape — the scope of what is and is not
  guarded, and why, is recorded in **ADR-0024** — so these are disclosed and tracked (MP-85)
  rather than papered over. What IS pinned is every number, every fraction, and every claim
  about what the harness measured.
  `[M]` The `299 tests passing` line — 42 short for several releases — is now pinned to the
  collected count and cannot drift again.

### Added
- **`scripts/arg_repertoire.py`** — measures the observed argument *repertoire* (distinct
  tool-call payloads per scenario per model across N runs), the number
  `README-arguments.md` asks for, **as a standalone pre-flight probe**.
  `scripts/fp_measurement.py::repertoire()` already reports distinct payloads per scenario per
  *side*, but only inside a paired FP run; this answers the question upstream of it — **can this scenario fire on this model at all?** It reports raw and
  key-sorted counts separately, so a raw-vs-canonical gap is visible as key-order jitter. `[M]` The
  component that would collapse such a gap in the engine, `diff/argkey.py`, is on the
  unmerged MP-04 branch — **no gating signal on `main` reads tool-call arguments at all**. `[M]` It carries an explicit error column because the first
  version of this probe reported a clean *"0 of 7 vary"* against a host whose key had been
  revoked: every call raised, every list stayed empty, and an empty set has one distinct
  element. A measurement harness without an error column manufactures its own null result.
- **The Google adapter can bill Vertex AI instead of an API key.** Set
  `GOOGLE_GENAI_USE_VERTEXAI=true` (or `GOOGLE_GENAI_USE_ENTERPRISE=true`, the SDK's current
  spelling — both are honoured) and `GOOGLE_CLOUD_PROJECT`, and Modelpin authenticates with
  Application Default Credentials — no key. **The API-key path is unchanged and remains the
  default**, and ADR-0008 still holds either way: Modelpin reads only what you put in your own
  environment and never hardcodes, stores or ships a credential.
  `GOOGLE_CLOUD_LOCATION` defaults to **`global`** — the SDK's own default, and `[M]` the only
  location serving current models: every `gemini-3.x` id returns 404 on regional endpoints such
  as `us-central1`, where only the legacy 2.5 family is available. Set a region only for data
  residency, which costs you the newer models.
  `[M]` This exists because the two doors are not interchangeable for billing. An AI Studio API
  key bills a **prepaid wallet separate from Google Cloud billing**: with Cloud credit available,
  every current model returned `429 RESOURCE_EXHAUSTED — "Your prepayment credits are depleted"`,
  and a freshly created key *in the credited project* was refused identically — prepay is a
  property of the billing account, not the project, so no new key can reach it. The same project
  on Vertex answered normally and billed the Cloud credit. `[M]` Vertex also rejects API keys
  outright (*"API keys are not supported by this API. Expected OAuth2 access token"*), so this
  could not be done by swapping a key.
  `[M]` The catalogues differ too: `gemini-2.5-flash` is `404 "no longer available to new users"`
  on AI Studio and still served on Vertex.
- **Tool-call *arguments* are now compared, not just tool names.** Up to and including 0.1.2
  the behavioral diff read only the tool-call *names*, so a candidate that called the right
  tool with a catastrophically wrong argument — `issue_refund(amount=49.99)` becoming
  `issue_refund(amount=4999.00)` — was reported as `unchanged` at **confidence 1.00**.
  Nothing else caught it: with the output text identical, the semantic judge short-circuits
  and is never invoked, so the tool signal was the only place an argument could register.
  The new signal is deliberately narrow, because widening a detector is how a tool starts
  crying wolf. It fires only when the two sides' argument payloads are fully **disjoint** (no
  run on one side used a payload any run on the other used), and only when the tool-name
  trajectory is stable across both sides, both sides recorded arguments on every run, and
  both used the same `--runs`. A single odd run is still noise. `DiffSignals` gains
  `tool_arg_match`, where `null` means the argument comparison did not run — which is not
  the same as `1.0`.
  **The signal ships ADVISORY.** It raises a scenario to `changed_minor` and can never reach a
  build-failing `regression` on its own, because its effect-size floor `MIN_TOOL_ARG_TVD` has
  no labelled calibration set behind it — the same cap the semantic judge shipped under until
  `37b63a1` calibrated it. `[M]` Uncapped, the gate failed a build with the **same model on
  both sides**: identical tool names, identical output text, no refusal, one optional argument
  field present on every baseline run and absent on every candidate run returned `regression`
  at confidence 0.992 at the shipped `runs: 5`, where 0.1.2 returned `unchanged` at 1.0.
  **State the cost plainly: the headline example above no longer fails a build.** `[M]`
  `mp check` exits **0** on an argument-only change (`cli.py` gates `exit 1` on `regression`
  alone), and `action.yml` gates its red X on that exit code — so a 100× refund argument now
  arrives as a warning a human must read, not as a stopped pipeline. `[M]` Nothing is
  *withheld*: `action.yml` posts the PR comment regardless of verdict, and a minor still gets a
  report header, a MINOR CHANGES section and a "Pin to … until resolved" line. `[M]` It also
  moves no published figure, because `scripts/fp_measurement.py` already scores
  `changed_minor` as a false positive. The
  falsifiable condition for promoting it later is in **ADR-0029**; `runs` is explicitly not a
  knob for this, as the cost is non-monotone in N.
  `tool_arg_match` is rendered as an **Arg match** column in the Report's per-scenario table,
  where `—` means the comparison did not run.
- **`insufficient_evidence` verdict, and exit code 3.** A scenario where **at least half**
  of one side's runs recorded no behaviour — no output, no tool call, no refusal — now
  abstains instead of reporting `unchanged`. Below that threshold (e.g. 2 of 5) the run
  still reports `unchanged`; the counts are recorded on `DiffSignals` but gate nothing. Previously two identically-degenerate sides scored `unchanged` at
  **confidence 1.00**, because every p-value was 1.0 and unchanged-confidence is `min(p)`:
  the engine's least-evidence case scored as its most confident. `mp check` exits 3 when a
  scenario abstains **and nothing regressed** — a regression still outranks it and exits 1
  — and the report omits "safe to adopt". See ADR-0018.

### Fixed
- **The argument gate's "cannot spend the FP metric" claim is retracted, and the gate is
  priced instead.** `[M]` `modelpin/diff/__init__.py` sets `verdict = regression` on
  `arg_regressed` with **no conjunction** with `tool_regressed`, so it is an independent
  verdict-raising path: same model both sides, identical tool names on every run, disjoint
  float jitter returns `regression` at confidence 0.9520 where `main` returns `unchanged` at
  1.0. `[M]` The `+0.0056 pp` defence (1.2654% -> 1.2710% over 72,072 relabelings) comes from a
  null carrying **two payloads**, and the gate fires only when the two sides are disjoint AND
  each side is internally concentrated — so that null prices a different signal from the one
  that ships. Retracted in all four code sites, with the narrower claim that survives put in
  its place and verified more strongly than the one withdrawn: `[M]` the two gates are mutually
  exclusive **per pool** (0 counterexamples over 191,808 pool pairs x 4 match modes), and the
  argument channel does not raise the name gate's worst-case **per-pool ceiling** — exhaustive
  over every payload multiset, N=3..6 read 0/20, 2/70, 12/252, 44/924 on both channels, with
  the N=5 argument worst pool reached at repertoire 6-7 rather than 2. **A ceiling is not a
  rate**, and conflating the two is how the retracted claim was born.
- **`scripts/arg_gate_price.py` prices that cost offline, with no API key.** `[M]` Dealing the
  committed repertoires' actual runs into two disjoint sides and driving the real
  `diff_scenario` through the repo's own ADR-0022 predicate: the worst cell is **4.18% of
  scored trials** (`arg_optional_fields`, `gpt-4.1-mini`, `--match subset`, N=3), with 26 of 48
  cells non-zero over a **0.08%-4.18%** range, against **0/0** on `main` where the harness
  prints *** THIS RUN MEASURED NOTHING ***. Three findings change how the gate should be
  described. `[M]` **Disjointness is not the firing condition** — a fully disjoint
  `a,a,a,b,c` vs `d,d,e,f,g` does not fire (p=0.0873) and ten pooled distinct payloads give
  p=1.0, because the permutation test is relabeling-invariant and the gate goes SILENT at
  maximum jitter. `[M]` **The match modes invert**: at N=3 the equivalence modes cannot fire at
  all while `subset` reaches 4.18%; by N=5 that reverses. `[M]` **Where the N-curve peaks is an
  artifact of the estimator** — without replacement the peak is N=6, with replacement N=5, on
  the same 16 runs. Deliberately published with **no interval over replicates**: those are
  draws from an assumed population, a Clopper-Pearson bound over them shrinks with compute
  (3.82% at 1k, 3.27% at 4k), and `[M]` `upper_bound_95` raises `OverflowError` past n~3,000 —
  now documented as a guard rail rather than a defect. The binding uncertainty is stated
  instead: Good-Turing puts **31-44%** of the payload mass on values the 16 runs never saw, and
  truncating that tail moves the rate in **both** directions.
- **Two guards on the argument gate that CI could not defend.** `[M]` `MIN_TOOL_ARG_TVD` could
  be mutated `1.0 -> 0.8`, `0.6` or `0.0` with the whole suite green, because the only
  assertion read `tvd >= MIN_TOOL_ARG_TVD` where `tvd` was literally `1.0`. `[M]` Deleting the
  equal-runs precondition also left the suite green, and by the branch's own numbers that
  clause separates a 0.1953% true-null rate from **1.9204%** at 5v2 — reachable as
  `mp baseline --runs 5` then `mp check --runs 2`. Both are now pinned by value and by
  behaviour, with the concrete harm named: at a 0.8 floor a candidate pool sharing **one** of
  five payloads with the baseline scores `tvd = 0.80, p = 0.04762` and fires, and overlap is
  precisely what the structural rule excludes. `[M]` 6 mutants, 6 red; the three that previously
  survived are caught by the new guards specifically.
- **MP-105's diagnosis of the argument corpus is withdrawn — refuted by its own transcript.**
  It read the first live `arg_*` run (7 scored trials, **all the same scenario**) and concluded
  that enum / key-order / list-order / rounding / optional-field arguments are schema-constrained
  and therefore deterministic *regardless of temperature*, proposing we concede in `docs/` that
  this method cannot test them. `[M]` Recounting the committed transcript, `gemini-2.5-flash`
  varied on **three** of seven shapes, not one: `arg_numeric_rounding` returned two distinct
  payloads on **5 of 8** trials and `arg_optional_fields` on **3 of 8**. Those trials were
  excluded because base and candidate **pools overlapped** — a 1-payload side against a
  2-payload side containing it gives `p = 1.00` on every channel, so ADR-0022 scores it *could
  not have fired*. That is the false-positive defence working, not a property of the model. The
  concession is withdrawn before publication.
- **How much an argument axis moves is model-dependent, within one vendor.** `[M]` Re-running
  the same seven files unchanged at `temperature: 0.7`, `arg_numeric_rounding` emits **1**
  distinct payload in each of two 16-run samples on `gpt-4o-mini`, and **7 then 9** on
  `gpt-4.1-mini` (10 at n=24, modal 8/24) — real computed-number jitter, `weight_kg` =
  `3.35658` / `3.35662` / `3.35664` / … `[M]` The count is itself a sample statistic and moved
  between two runs of the same model at the same n, so the docs publish the spread rather than
  one figure. `[M]` `arg_optional_fields` varies on **both** models and therefore discriminates
  neither. `[A]` **Which** axes can move still looks scenario-driven — four shapes held at one
  payload on every model and every run, and the two models that vary on three shapes vary on the
  *same* three across two vendors; three models cannot separate those factors. Three qualifications are documented rather than glossed:
  `[M]` nothing under `modelpin/diff/` reads tool-call arguments on `main` (`structural.py:29`
  is `tc.name` only), so condition 6 cannot be priced from `main` at any n; `[M]` a
  high-repertoire candidate is *not* pure good news, because disjoint rounding jitter is already
  recorded scoring `regression` at 0.992 — a false positive on the north-star metric; and one
  distinct payload is an **abstention** (ADR-0018), bounded only at **17.1%** by 16 identical
  runs, never evidence of quietness.
- **`examples/calibration/README-arguments.md` documented a command that no longer runs.** `[M]`
  MP-89 landed the role filter that section anticipated, as a *refusal* rather than a default,
  so the published line now exits with `error: 2 roles declared for this directory (fit,
  score)`. Corrected to pass `--role score` — and to name `gpt-4.1-mini`, because `[M]` on the
  `gpt-4o-mini` it used to name, six of the seven shapes cannot fire at all.

- **The Drift Map report no longer approximates counts it can derive exactly, no longer
  over-attributes its own refusal-detector bug, and no longer calls a pair's artifacts a
  genuine change.** Four drifts on the project's most-linked public page, each against the
  artifact published beside it ([`docs/reports/data/`](docs/reports/data/)).
  `[M]` The TL;DR said the engine "stayed quiet on **~50** of 60 comparisons" — it is exactly
  50. `[M]` The limitations section said "of 9 'regression' flags, **~6** ... are solid" —
  exactly 6; the body approximated a number its own next sentence pinned by complement, and
  the table footnote three screens up had already partitioned the same 9 precisely.
  `[M]` The self-critical paragraph claimed a `0% → 100%` refusal-rate regression *"in three
  pairs"* when only **two** carry `refusal_delta = 1.0`; the third (`gpt-4o` → `gpt-4.1`) has
  `refusal_delta = 0.0` and was flagged by the semantic judge. Over-attributing your own bug is
  still a wrong number on a public page, and the flattering direction is *away* from the reader,
  which is why no review caught it.
  `[M]` The headline said *"every single migration surfaced at least one **genuine** behavior
  change"*. `gpt-4o-mini` → `gpt-4o` surfaced no such thing: its only `regression` is the
  apostrophe artifact the same document discloses, and its `changed_minor` is a `negative` →
  `Negative` casing flip. All five pairs **do** carry a `regression` — the table says so — so
  the correction is a solidity qualifier, not a smaller count: **four of the five** carry one
  that survives a read of the raw traces.
  `6 of 9` is now published as an **enumeration, explicitly not a precision rate**: `[M]` the 9
  flags land on only **4 distinct scenarios** (`prompt_injection` in 4 pairs,
  `borderline_access` in 3, two singletons), so a binomial interval over them would count
  repeats of one scenario as separate evidence. No verdict, trace or published artifact
  changed — only what the prose claims about them.
- **The Drift Map now discloses the mechanism behind its only `changed_minor`.** `[M]`
  `sarcasm_sentiment` on `gpt-4o-mini` → `gpt-4o` is `negative` (5/5) → `Negative` (5/5),
  against a case-sensitive `must_contain: ["negative"]`. The verdict is **not** withdrawn — the
  flip is deterministic, it really would break a strict downstream parser, and `changed_minor`
  is the correct severity — but a report that discloses one of its own typography artifacts
  while silently shipping a second is not making the credibility move it claims to be making.
- **Every copy of the retracted Drift Map framing is corrected, not just the report.** `[M]`
  MP-72 shipped a correct paragraph while leaving ten copies of what it retracted, so this
  release swept for them: `README.md` and `examples/report-suite/README.md` (the latter ships
  in the sdist) both carried `~50/60` and "≥1 real/genuine behavior change on every pair"; two
  private launch drafts and two agent briefs carried the same. All corrected or banner-blocked.
- **`4/6` and `5/6` recall now travel with the bound this repo's own helper computes.** `[M]`
  Both shipped bare, and a bare `4/6` reads as 67%. The one-sided 95% Clopper-Pearson *lower*
  bound is **27.1%** at 4/6 and **41.8%** at 5/6 — `1 - upper_bound_95(misses, checked)`, the
  same reflection the detection arm already publishes through, so no second formula enters the
  repo. Corrected on both surfaces, `README.md` and `docs/fp-measurement.md`. `[M]` Even the
  self-judge run the doc already demotes as circular supports barely half of what its fraction
  implies: **41.8%** against a point estimate of 83.3%.
- **`docs/reports/*` gets its first guard**, narrowing a hole ADR-0024 recorded as knowingly
  accepted. `tests/test_report_claims.py` derives the verdict totals, the solid/soft split, the
  distinct-scenario counts, the refusal attribution, the solid-pair count and both recall
  bounds from the run artifact and from `upper_bound_95`. Four literals are pinned as
  deliberate tripwires (`(9, 3)`, `"Three of the nine"`, `` `6 of 9` ``, the 2/1 refusal split)
  so that a changed run fails loudly instead of silently re-deriving around the prose. It
  rejects any `~<digit>` anywhere in the report — the shape of the defect, not the two
  sentences that carried it — requires each published fraction's interval to sit **within 350
  characters of it** rather than merely somewhere in the same file, and fails if either
  self-disclosure (the Unicode-apostrophe writeup, the "Other caveats" block) is deleted.
  `[M]` **24 mutants applied, 24 red**, including all six that the claims-auditor gate found
  green against this guard's first version. **Named limits, per ADR-0024:** the precision-rate
  check is a banned-string list and does not generalise (ADR-0024:63 warns about this shape by
  name — a mutant spelling the rate "roughly two thirds" passed the first version and is
  blocked only because it is now on the list); and no guard reads the report's non-numeric
  causal prose.
- **The source distribution ships `scripts/`, so the test suite it also ships can import
  itself.** `[M]` Reproduced through the repo's own build backend before the fix: sdist build
  `rc 0`, **154 members**, `scripts/` entries **`[]`** — while `tests/test_recall_arm.py` and
  `tests/test_fp_measurement_repertoire.py` both shipped, and both do
  `from scripts.fp_measurement import ...`. Unpacked and collected, that is
  **`rc 2`, `297 tests collected, 2 errors`, `ModuleNotFoundError: No module named 'scripts'`**.
  `[M]` It was never only the tests: the Drift Map's own published reproduce command is
  `python scripts/drift_map.py --suite-dir examples/drift-suite`, and `README.md` links
  `scripts/fp_measurement.py` — so `pip download --no-binary :all: modelpin` gave a checkout
  that could run none of the three commands the docs tell you to run. `MANIFEST.in` states the
  promise this broke in its own header: *"a contributor can `pip download --no-binary` and get
  a runnable checkout with the suites and the test suite."* Fixed with `graft scripts`.
- **The sdist guard now asserts the shipped suite *runs*, not that four filenames are present.**
  `[M]` The existing check (`_SDIST_REQUIRED`) lists four paths and passed throughout — presence
  is not importability, and that gap is the whole defect. Four derived assertions are now the
  load-bearing ones. `pytest --collect-only` inside the **unpacked** sdist must exit 0 — closed,
  so it does not care whether the cause is a missing directory, a data file read at import time
  or a forgotten dependency. Every repo-root directory imported by a test that actually shipped
  must have shipped too, read out of the shipped sources rather than a list. Every `.py` under
  `tests/` and `scripts/` must reach the archive. And every `scripts/*.py` path the public docs
  tell a reader to **run** must be in there — the one check whose failure names the promise
  instead of a filename.
  `[M]` Each of the last two exists because a mutant survived without it, and both survivors
  were invisible for the same reason: **a directory can ship while the file you need is gone.**
  Deleting `graft tests` outright left the suite green, since setuptools already carries
  `tests/test*.py` — the only file that line adds is `tests/docs_extract.py`, a helper CI
  invokes as a script. And `[M]` the packaging-verifier gate found that dropping
  `scripts/drift_map.py` and `scripts/calibrate_thresholds.py` while keeping
  `fp_measurement.py` passed every other new guard, because no shipped test imports them —
  yet `python scripts/drift_map.py` is the Drift Map's own published reproduce command.
  `[M]` 5 mutants, 5 red. The sdist is built once per module and the whole file costs 5.2s.
- **A fresh clone no longer fabricates a regression against Modelpin's own baseline.** `[M]`
  Reproduced verbatim: clone the repo, run `mp init`, write your own
  `scenarios/refund_request.json`, run `mp check` — and get
  `REGRESSION refund_request: tool-call behavior changed: ['lookup_order', 'issue_refund'] -> []
  (confidence 0.99)`, **exit 1**, plus a `last-report.md` the GitHub Action posts as a PR
  comment. You never ran `mp baseline`; the traces on the left were Modelpin's own, recorded
  2026-06-24 for an unrelated scenario that merely shares a filename. Three innocent facts
  combined: the dogfood baseline is tracked on purpose (`.gitignore:19-20`) so it ships in every
  clone, `mp init` scaffolds `models: [gpt-4o-mini]` — the exact key it was stored under — and
  `mp check` pairs a baseline to a scenario by **id alone**. `refund_request` is not an unlucky
  guess: it is the name in the README's worked-example table and the file `mp init --demo`
  writes to disk. The dogfood baseline is now keyed under the fictional `modelpin-dogfood`, so
  no scaffolded config can name it; the traces inside are unchanged, genuine gpt-4o-mini
  recordings. See ADR-0021.
  **This fixes the instance, not the mechanism.** `mp check` still pairs on scenario id with no
  provenance recorded, so editing your own scenario after recording your own baseline hits the
  same path. That needs a content hash in the baseline payload — tracked, and specified as three
  `xfail(strict=True)` tests rather than prose.
- **This repo's own dogfood config no longer hijacks a cloned checkout.** `modelpin.yaml`
  sat at the repo root, which is the path the CLI loads by default, so anyone who cloned
  Modelpin and followed README "The real flow, on your own app" inherited it: `mp init`
  honoured its `scenarios_dir`, found the 8 scenarios already in `examples/suite`,
  scaffolded nothing, and still exited 0 reporting "Already initialised" — then
  `mp baseline` replayed Modelpin's own held-out public suite on the user's key: **40
  replays** (8 scenarios × the config's `runs: 5`), with no warning and no scenario of the
  user's own involved. `[M]` In the one recording we have — the traces committed at
  `.modelpin/baseline-modelpin-dogfood.json`, gpt-4o-mini, 2026-06-24 — those 40 replays cost
  **60 chat-completion calls**. Treat that as an observation, not a constant: three of the
  eight are `kind: agent` scenarios whose replays drive a tool loop of 1–6 turns
  (`MAX_TOOL_TURNS`), and the loop length is the model's decision, so the same 40 replays
  are bounded at 40–115 calls. The config now lives at `.github/modelpin.yaml` and the
  workflow passes it explicitly. **This affects people who cloned the repo, not
  `pip install modelpin` users** — no published artifact ever contained that file: it is
  absent from the 0.1.1 and 0.1.2 wheels and the 0.1.2 sdist on PyPI, and `MANIFEST.in`
  never listed it. A `tests/` guard now fails if a `modelpin.yaml` becomes tracked at the
  root again. Side effect: README's "`mp init` scaffolds `modelpin.yaml` + `scenarios/`" is
  now true in a fresh clone, where it previously was not.

### Planned
- Anthropic adapter (currently a stub; needs a paid key).
- Edge-probing scenario generator (helping users author discriminating scenarios — "scenario
  quality is the product").
- An "underpowered" annotation at low `--runs`. `insufficient_evidence` does **not** cover
  this: at `--runs 2` neither signal can reach `p ≤ ALPHA`, and at `--runs 3` the tool
  signal cannot, yet both still report `unchanged` and exit 0. See ADR-0018 non-goal 2.
- A verdict that reads tool-turn truncation. `Trace.incomplete_reason` now records it, but
  nothing gates on it — an exhausted tool loop implies a tool call, so the abstention
  predicate is provably unable to fire on it. See ADR-0018 non-goal 1.
- Expanded judge calibration (≥30 real-migration trace pairs + a non-OpenAI judge).

## [0.1.2] - 2026-08-22

### Changed (breaking)
- **`mp report --suite-dir` is now required.** There is no built-in default suite directory.
  The previous default (`examples/report-suite`) named a path that cannot exist after
  `pip install`, so a bare `mp report` told users "no scenarios found" for a directory they
  never chose. Bare `mp report` now exits 2 with an error naming the flag. The one documented
  invocation that omitted it (`examples/report-suite/README.md`) has been updated; every
  other documented invocation, including the published report's reproduce command, already
  passed the flag.

### Fixed
- **`pip install modelpin` could not run the README's own quickstart** ([#28]). The wheel
  shipped no `examples/` and no `data/models.json`, so the documented zero-key quickstart
  exited 1, `watcher.load_registry()` raised `FileNotFoundError`, and `mp report` failed on
  its own default. Fixed by making the *program* self-sufficient rather than the archive
  bigger: the wheel stays code-only, `mp init --demo` generates the demo, and the model
  registry is embedded.
- **The error message's remedy no longer loops.** `no scenarios in ...` used to say
  "Run `mp init` first" unconditionally. That was correct only when the missing directory
  happened to be the one `mp init` creates; otherwise it sent the user around a loop with no
  exit (— `mp init` succeeds, the identical command fails identically). The message now reports the
  absolute path it searched, which setting chose it, what it actually found there, and a
  remedy that is true in that state. `mp init` is named only when `mp init` would create
  exactly that path.
- **`mp init` honours `scenarios_dir` from an existing `modelpin.yaml`** instead of always
  scaffolding `scenarios/`, so following the error's advice now terminates.
- **`load_registry()` no longer reads `./data/models.json` from the working directory.** An
  unrelated file in a user's cwd could redefine which models Modelpin believed existed.
- The offline report no longer claims "using your API key" on a keyless `--provider fake` run
  — it says "from canned offline traces (no API key)". That line is printed inside the first
  artifact a new user reads, under a heading that says no key is needed.
- An unmanifested suite directory no longer publishes a report header claiming to be
  `modelpin-public-suite`; the fallback id is now `local-suite`.
- The documented PowerShell escape hatch is `Remove-Item Alias:mp -Force` — the alias is
  `ReadOnly, AllScope`, so the command as previously written failed.

### Added
- **`mp init --demo`** — generates a self-contained offline sandbox (`modelpin-demo/`: four
  scenarios, canned traces, config, README) that demonstrates every verdict the engine can
  reach, including `unchanged`. No key, no cost, and it exits 1 on a real regression because
  that non-zero exit is the CI gate.
- **`wheel-smoke` CI job** — builds the wheel, installs it into a fresh venv *outside* the
  checkout, extracts the quickstart *from `README.md`* (via `tests/docs_extract.py`, which
  fails closed), and runs it in an empty directory, on Linux and Windows. The previous CI
  installed `-e` and ran from the checkout, which removed both preconditions of #28 and made
  that entire bug class invisible to it.
- `MANIFEST.in` — the sdist now carries `tests/`, `examples/`, `docs/` and
  `data/models.json`, so a contributor can build and test from a source distribution. The
  wheel is unaffected and stays code-only.

[#28]: https://github.com/samarthputhraya/modelpin/issues/28

## [0.1.1] - 2026-06-24

### Security
- **GitHub Action hardened against script injection** — all caller-controlled inputs now flow
  through the step `env` as data and are never interpolated into `run:` shell text.
- **PR-comment markdown injection fixed** — report content is escaped before it is posted.
- **Secret scrubber widened** — added Groq `gsk_` and ensured `model_id` is scrubbed from
  error paths.

### Fixed
- `--match subset` / `--match superset` no longer silently collapse to `strict` (which could
  manufacture a false-positive regression on a change the mode is meant to permit). Directional
  modes now route through `trajectory_match` with a one-sided violation-rate test, and `--match`
  is validated at the CLI boundary.
- Refusal detector folds apostrophe-like glyphs to ASCII, so two identical polite declines that
  differ only by a curly vs. straight apostrophe are no longer scored as a refusal-rate change.

### Added
- `CONTRIBUTING.md`, issue templates, and a pull-request template.
- Discriminating public report suite (v2) and **Modelpin Drift Map #1** (an open suite run
  across five real migration pairs, fully reproducible).

## [0.1.0] - 2026-06-24

### Added
- **Behavioral-diff engine** — an exact two-sample permutation test (deterministic, no SciPy)
  over N runs, combined with structural signals (tool-call trajectory match in
  strict/unordered/subset/superset modes, refusal rate, output-format/assertion validity) and
  an optional calibrated low-temperature LLM-as-judge for semantic equivalence. A regression is
  flagged only when the distribution shifts (`p ≤ ALPHA`) **and** clears an effect-size floor —
  optimizing the north-star: a low false-positive rate.
- **CLI** (`modelpin`, alias `mp`): `init`, `scan`, `baseline`, `check`, `report`, `version`.
- **GitHub Action** — replays scenarios on a candidate model, posts a sticky PR comment, and
  fails the check on a real regression.
- **Providers** — OpenAI, Google/Gemini, and Groq/Llama (via the OpenAI-compatible `base_url`)
  through one engine. Anthropic adapter stubbed.
- **`mp report`** — runs an open public suite across two models into a reproducible,
  opinion-framed Markdown + JSON report.
- BYO-key throughout, with key-shaped-secret scrubbing on all output.

[Unreleased]: https://github.com/samarthputhraya/modelpin/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/samarthputhraya/modelpin/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/samarthputhraya/modelpin/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/samarthputhraya/modelpin/releases/tag/v0.1.0
