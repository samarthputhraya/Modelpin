# The labelled tool-channel calibration set (`examples/calibration/tool/`)

> **Role: FIT. Thresholds and structural rules for the tool-NAME trajectory channel may be
> chosen here, and NO result may ever be claimed from here.** To be declared in
> [`../../roles.json`](../../roles.json) and pinned in
> `tests/test_suite_roles.py::EXPECTED_MEMBERS`; until both are true the examples tree fails
> `test_every_scenario_file_declares_a_role`, which is the intended behaviour.
>
> This set exists so that a rule can be chosen somewhere OTHER than `examples/fp-suite-v2/`.
> Nothing here may be scored, pooled with the published `fp-suite` bound, or cited as a
> false-positive rate. ADR-0025, ADR-0038 D2.

## Why this set exists

`[M] 2026-09-08 FP review` (ADR-0041, MP-220): the tool gate cannot be changed at all until a
labelled set exists, because every candidate rule so far has been priced against
`examples/fp-suite-v2/` — a corpus ADR-0038 declares role `score` forever. Choosing a structural
rule *because* it takes that corpus's tool alarms from 1 to 0 is ADR-0025's own prohibition with
one word changed, from "threshold" to "structural rule".

ADR-0002's revisit bar asks for **a labelled set of ≥30 real-migration pairs**. `[M]` What
existed was **4 distinct detection scenarios** (`agent_missing_param_ask`,
`agent_reschedule_two_step`, `refund_request`, `support_order_status`) replayed 20 times across
surfaces, so `n_effective = 4` and the one-sided 95% upper bound on a detection-loss rate is
**52.7%**, not the 25.9% that "0 in 10" suggests.

This set is **35 labelled pairs: 16 `equivalent`, 19 `changed`.**

## What a pair is

A pair is *(the scenario file as authored, a candidate produced from it)* plus a label. The
label is in [`labels.json`](labels.json), keyed by scenario id.

| label | how the candidate is produced | what the gate must do | count |
|---|---|---|---|
| `equivalent` | the **same scenario**, replayed. A same-model null. `perturbation` is `null` | **must NOT fire** | 16 (14 discretionary + 2 anchors) |
| `changed` | the scenario with `perturbation` as its **only** system message — exactly the `_perturb` shape in `scripts/fp_measurement.py` | **should fire** | 19 (13 Tier 1 + 3 Tier 2 + 3 Tier 3) |

`_perturb` **drops the original system message**, so every `perturbation` here is a complete
replacement system prompt, not a suffix. That is load-bearing; see the next section.

---

## Design constraint 1 — the SEMANTIC channel must stay quiet on both arms

This is the constraint that decides whether the set is worth anything.

`[M] 2026-09-08` (ADR-0041 Amendment 1): all **10** scored tool-channel detection rows in the
repo also name `semantic drift` in their explanation, and `semantic_diverged` appends to
`hard_pvalues` **independently of the tool channel**. So those rows cannot price a tool rule at
all — the verdict is carried by a different channel, and under a global `--match subset` the
tool channel goes quiet on 7 of 10 while the *detections lost are 0 of 10*.

A scenario whose verdict a second channel would carry anyway is worthless here. So:

**The CONTENT is pinned and only the TRAJECTORY is free.** Every scenario ends with the same
shape of reply constraint —

> `Reply to the <role> with one short sentence stating <an exhaustive list of facts>. Do not
> describe which tools you used or what steps you took.`

— where every listed fact is fully determined by the user message and the canned
`tool_results`, and the closing sentence forbids the model from narrating its own trajectory.
That last clause is the whole mechanism: without it a model that sends the optional courtesy
email says so in its reply, and the trajectory difference leaks into the semantic channel.

**How this was checked, per scenario, mechanically and offline.** For all 19 `changed` pairs:

1. the perturbation's **reply constraint is byte-identical** to the base scenario's — asserted
   with `p.endswith(reply)` where `reply` is sliced out of the base system message;
2. the perturbation's **intro clause is byte-identical** to the base scenario's;
3. therefore the *only* text that differs between the two sides is the tool-policy clause;
4. every snake_case token in a perturbation is a tool **that scenario declares** (so no
   perturbation can name a tool the model cannot call);
5. no `REFUSAL_MARKERS` entry appears in any prompt or perturbation, on either arm.

`[M]` All five pass on all 35 files. Mutation-checked, not assumed: rewriting the reply
constraint in each perturbation is caught on **19 of 19**.

For the 16 `equivalent` pairs the check is trivially satisfied — the two sides are the same
bytes — and the semantic risk is instead that the *model* narrates. That risk is why the "do not
describe which tools you used" clause is on the `equivalent` files too, and it is a **falsifier,
not a guarantee**: see "What failure looks like".

Two further semantic pins, chosen per scenario rather than asserted globally:

* Where a `changed` pair **substitutes** one tool for another, both tools' canned results carry
  the **same outcome fields** — `tc_cheese_delivery_route` returns `day: Thursday` from both
  `book_courier` and `book_own_van`; `tc_schoolbus_cover_route` returns `covered: true` from
  both; `tc_cidermill_reminder_channel` returns `sent: true` from both.
* Where a pair **drops a look-up**, the fact the look-up would have supplied is already in the
  user's own message (`tc_beekeeping_backorder_lookup`: *"Nothing on the shelf as far as I
  know"*; `tc_carwash_plan_change`: *"I am on Bronze at the moment"*), so the reply is
  unchanged whether or not the model verified it.

---

## Design constraint 2 — MP-225: rate shifts, not flips

`[M] 2026-09-08` All 4 existing tool detections are `5×A → 5×B` total flips, three with `()` on
one side. There are **zero** mixed-baseline tool detections, so a disjointness-style rule is
trivially satisfied by the whole corpus and "cannot price this rule, it can only fail to notice
it".

`[M]` The engine's arithmetic at the shipped `runs: 5`, computed with
`diff.stats.permutation_pvalue_distribution` and `total_variation_distance` against
`ALPHA = 0.05` and `MIN_TOOL_TVD = 0.5`. Of the **36** two-mode baseline→candidate shapes, the
gate fires on exactly **6**:

| baseline | candidate | `tool_tvd` | `tool_p` | status quo | +NOVELTY | +DISJOINT |
|---|---|---|---|---|---|---|
| 5A | 0A/5B | 1.000 | 0.007937 | FIRE | FIRE | FIRE |
| 0A/5B | 5A | 1.000 | 0.007937 | FIRE | FIRE | FIRE |
| 5A | 1A/4B | 0.800 | 0.047619 | FIRE | FIRE | **quiet** |
| 0A/5B | 4A/1B | 0.800 | 0.047619 | FIRE | FIRE | **quiet** |
| **4A/1B** | **0A/5B** | 0.800 | 0.047619 | FIRE | **quiet** | **quiet** |
| **1A/4B** | **5A** | 0.800 | 0.047619 | FIRE | **quiet** | **quiet** |

The last two rows are the entire reason this set exists. They are the only shapes on which the
three candidate rules disagree with each other in the *detection* direction, and both require a
**bimodal baseline**. `[M]` The last row is MP-220's own false positive, reproduced here from
ADR-0041's stated traces: baseline 4×`['update_order_status','notify_customer']` + 1×
`['update_order_status']`, candidate 5×`['update_order_status']` → `tool_tvd 0.8`,
`tool_p 0.047619`, `novel: False`, `disjoint: False`. The fifth row is the same shape wearing a
true label, and it is ADR-0041's **falsifier #1** in its exact form.

**The three MP-225 rate-shift `changed` pairs** — designed for a baseline pinned high on the
discretionary call and a candidate that keeps it on a minority of runs, i.e. `5A → 1A/4B` (the
shape MP-225's row names) with `4A/1B → 0A/5B` as the equally-informative near miss:

| id | the discretionary call | baseline nudge | perturbation narrows it to |
|---|---|---|---|
| `tc_planthire_offhire_collection` | `arrange_collection` | *"a fenced site and there is no float on it"* | only when the customer asks |
| `tc_climbinggym_freeze_refund` | `refund_prorata` | member paid to the 21st, freezing on the 1st | only members of >2 years (this one joined 11 months ago) |
| `tc_laundry_route_shortfall` | `flag_short_delivery` | *"two towels light against the docket"* | only shortfalls of ≥10 |

Each keeps **one** discretionary axis and nothing else, so the observed rate is readable. Each
is a **bet on the model's rate**, and the bet is pre-registered below rather than assumed.

**And three more that cover the OTHER discriminating cell.** The table above shows two rows on
which the rules disagree, and Tier 2 reaches only one of them. `4A/1B → 0A/5B` — a bimodal
baseline whose candidate commits fully to a mode the baseline already produced — is the row
where the status quo fires and **both** NOVELTY and DISJOINT go quiet, and it is ADR-0041's
falsifier #1 verbatim. It is reached by removing the discretion outright rather than narrowing
it:

| id | the discretionary call | baseline nudge | perturbation |
|---|---|---|---|
| `tc_glazier_survey_check` | `confirm_measurements` | *"one reveal 4 mm off the others"* | never call it; the survey is final |
| `tc_upholstery_fabric_hold` | `reserve_fabric` | *"that teal is the last roll we have"* | never call it; fabric is allocated at cutting |
| `tc_sailmaker_cloth_check` | `check_cloth_stock` | *"the heavy Dacron, which we do not always carry"* | never call it; stores check at the bench |

Tier 2 and Tier 3 are the same bet placed from opposite sides: both need the baseline to split,
and if it does not, Tier 2 degenerates to a near-flip and Tier 3 to a flip. Six pairs ride on
that one unobservable, which is why it is named in "What failure looks like" rather than
assumed.

---

## Design constraint 3 — one seam per scenario

* **No file declares `assertions`.** The format/assertion channel cannot fire here at all.
* **Nothing baits a refusal.** `[M]` No `REFUSAL_MARKERS` entry appears in any prompt or
  perturbation. Every task is a routine operator action with a canned success result.
* **No argument-only differences.** Every perturbation changes *which tools are called* or *how
  many times*, never only a payload. The argument channel is separate and advisory (ADR-0029)
  and this set is for the NAME trajectory.
* **No `match` field on any file.** MP-227's per-scenario `match` would pre-decide the very
  comparison this set is meant to price. The relations are described in the table below as
  *data*, so a rule can be tested against them rather than told about them.

---

## Design constraint 4 — the trajectory must actually vary on the null arm

This is what killed `examples/fp-suite-v2`'s assertion arm twice: a null with no denominator.

* **`temperature: 1.0`** (the API default) and **no `seed`** on all 35 files.
* Every tool is a **full function object** with a real `parameters` block. `[M]` Bare-string
  tools normalise to an empty parameters block in `providers/openai.py::_to_tools`, which is why
  three scenarios in `examples/suite/` cannot exercise this channel at all.
* Discretion is **stated in the prompt**, in the model's own terms — *"use it when you judge it
  would help"*, *"look it up first if you are not sure"*, *"either order is fine"*, *"use
  whichever you think is tidier"*. Seven distinct mechanisms are used, so the null arm is not
  one shape repeated fourteen times:

| mechanism | scenarios |
|---|---|
| optional verification before an action | `tc_marina_berth_assign`, `tc_boxoffice_seat_move`, `tc_carwash_plan_change`, `tc_brewery_keg_credit` |
| two required calls in free order | `tc_toollib_return_and_fee`, `tc_kennel_boarding_clearance`, `tc_skihire_boot_swap`, `tc_parking_permit_renew`, `tc_courier_locker_reassign` |
| forced head, free tail | `tc_dentallab_case_setup` |
| two routes to the same answer | `tc_signshop_price_route` |
| per-item calls or one batch call | `tc_bakery_standing_delivery` |
| optional enrichment after the action | `tc_campsite_pitch_hold`, `tc_windfarm_workorder` |
| pinned — the anchors | `tc_anchor_deeds_office_lookup`, `tc_anchor_ferry_book_two_step` |

`[M]` **What that variance is worth, as a false-positive denominator.** Exact enumeration over
the multinomial at `runs: 5`, weighting every count vector by its probability:

| trajectory modes, equiprobable | P(false alarm \| null trial), status quo | +NOVELTY | NOVELTY's reduction | P(novelty precondition met) |
|---|---|---|---|---|
| 2 | 0.0215 | 0.0117 | **-46%** | 0.061 |
| 3 | **0.0265** | 0.0225 | -15% | 0.334 |
| 4 | 0.0169 | 0.0154 | -9% | 0.621 |
| 8 | 0.0235 | 0.0234 | **-0.4%** | **0.963** |

And for a two-mode scenario as a function of the per-run rate *q* of the discretionary call:

| *q* | 0.05 | 0.1 | 0.2 | 0.3 | 0.4 | **0.5** | 0.6 | 0.7 | 0.8 | 0.9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P(sides differ) | 0.359 | 0.538 | 0.680 | 0.728 | 0.748 | **0.754** | 0.748 | 0.728 | 0.680 | 0.538 |
| P(fire), status quo | 0.0000 | 0.0005 | 0.0047 | 0.0121 | 0.0188 | **0.0215** | 0.0188 | 0.0121 | 0.0047 | 0.0005 |

Three consequences, written down before any run:

1. **The design target is `q ≈ 0.5`, and unlike the assertion channel it really is 0.5** — the
   tool gate has an effect-size floor, so the tails are not merely weaker, they are dead. At
   `q = 0.1` a null scenario fires once in two thousand trials; at `q = 0.5`, once in fifty.
2. **A high mode count does NOT make a quiet null, and NOVELTY's protection decays to nothing
   as the mode count rises.** `[M]` This retracts a prediction made while drafting this file --
   that the 8-mode scenarios (`tc_campsite_pitch_hold`, `tc_windfarm_workorder`) would
   contribute exposure but little firing power. They fire at **0.0235**, above the 4-mode rate
   and near the 3-mode peak. The mechanism is in the last column: NOVELTY fires unless the
   candidate's five runs are all trajectories the baseline's five runs also produced, and at 8
   modes that precondition is met on **96.3%** of null pairs, so the rule filters **0.4%** of
   the false alarms it filters **46%** of at two modes.

   **This is a finding about option C that ADR-0041 does not contain, and it is the mirror of
   its own D2 argument.** D2 rejects DISJOINT because multimodal baselines -- *"exactly what
   agentic migration looks like"* -- defeat it in the DETECTION direction. `[M]` The same
   population defeats NOVELTY in the FALSE-POSITIVE direction: option C's `7/39 reachable`
   headline is earned on low-mode scenarios and is worth almost nothing on an agent with
   several optional calls. It is not a reason to reject C, but any claim that C "keeps the
   channel alive while killing the MP-220 alarm" must be **stated per mode count**, and this
   set spans 2 to ~8 modes precisely so it can be.
3. **Acceptance arithmetic.** 16 `equivalent` scenarios × 1 surface × `--repeats 10` = 160 null
   trials. At the corpus-average `P(fire) ≈ 0.02` that is **≈ 3 expected false alarms** under
   the status quo and **≈ 2** under NOVELTY. A run returning 0 flagged of 160 does **not**
   discriminate the rules and must be reported as a non-result, not as a success.

---

## What is here — every pair, its relation, and what it prices

**Read "disjoint" precisely.** The rules in ADR-0041 read the two sides' sets of **trajectory
keys** — tuples of tool names, one per run — not the tools a scenario declares. `[M]` That
distinction inverts the obvious reading and is worth stating because the first draft of this
table got it wrong: a *unimodal* change like `(get_batch, move_batch)` → `(move_batch,)` is
**key-disjoint**, because the two tuples are different objects, even though the tool names are
nested. Non-disjointness requires a **shared realised trajectory**, which requires at least one
side to be multimodal on a mode the other also produces.

`A` = the trajectory key the baseline prefers.

### `equivalent` — 14 discretionary nulls

| id | tools | order | designed variance | trajectory modes |
|---|---|---|---|---|
| `tc_toollib_return_and_fee` | 2 | **free** | which of two required writes goes first | 2 |
| `tc_marina_berth_assign` | 3 | forced (get→assign) | optional `check_depth`, and where it lands | 2–3 |
| `tc_kennel_boarding_clearance` | 2 | **free** | which look-up goes first | 2 |
| `tc_signshop_price_route` | 2 | n/a | which of two tools returning the same number | 2 |
| `tc_campsite_pitch_hold` | 3 | partly forced | two independent optional calls | up to 8 |
| `tc_bakery_standing_delivery` | 2 | n/a | two per-account calls **or** one batch call | 2 |
| `tc_boxoffice_seat_move` | 3 | forced (get→move) | optional `check_seat_free` | 2–3 |
| `tc_carwash_plan_change` | 2 | forced if verified | verify the member's claim or trust it | 2 |
| `tc_skihire_boot_swap` | 3 | **free** | return/issue order + optional fit note | up to 6 |
| `tc_dentallab_case_setup` | 3 | forced head, **free** tail | tail order | 2 |
| `tc_windfarm_workorder` | **4** | partly forced | two independent optional calls | up to 8 |
| `tc_brewery_keg_credit` | 2 | forced if verified | 0, 1 or 3 verification calls | 3+ |
| `tc_parking_permit_renew` | 3 | **free** | order of two writes + optional receipt | up to 6 |
| `tc_courier_locker_reassign` | **4** | **free** pair | order of two writes + optional text | up to 6 |

**What the null arm can and cannot give a disjointness rule, stated honestly.** `[M]` ADR-0041
D2: **0 of 37** tool-exposed false-positive trials in the whole repo have disjoint key sets, so
that rule's bound is `0/0` — the `*** THIS RUN MEASURED NOTHING ***` shape. This set improves
that but **cannot fix it**, and the reason is structural rather than a corpus defect: a
same-model null is key-disjoint only when its two 5-run samples land on non-overlapping mode
subsets, which for a two-mode scenario at rate *q* has probability `2·q⁵·(1−q)⁵`:

| *q* | 0.3 | 0.4 | **0.5** | 0.6 | 0.7 |
|---|---|---|---|---|---|
| P(null pair is key-disjoint) | 0.00082 | 0.00159 | **0.00195** | 0.00159 | 0.00082 |

And that event coincides exactly with the total-flip cell, so it is also the only null on which
disjointness can fire. Over 160 null trials at the best possible *q* the expectation is **≈ 0.3
disjoint-reachable trials.** The honest conclusion to draw from this set will therefore be that
**disjointness is unfalsifiable on same-model nulls by construction**, not that it scored well —
which is a stronger statement of ADR-0041 D2 than D2 makes, and it must be reported that way
rather than as `0/n`.

`tc_signshop_price_route` and `tc_bakery_standing_delivery` are the two files that make even
that small number reachable: they are the only nulls whose two legitimate trajectories share no
tool at all, so a run of five that happens to land all-one-way is a *plausible* sample rather
than an astronomical one.

### `equivalent` — 2 quiet anchors

| id | tools | pinned trajectory | falsifier |
|---|---|---|---|
| `tc_anchor_deeds_office_lookup` | 2 | `('get_record',)` — one mandatory call, second tool explicitly ruled out | it moves at all |
| `tc_anchor_ferry_book_two_step` | 3 | `('get_crossing','book_crossing')` — forced order, third tool explicitly ruled out | it moves at all |

**If either anchor moves, no other number in this set is readable.** The variance would be in
the harness, the adapter, or basic instruction-following rather than in the deliberate
discretion of the other fourteen. `tc_anchor_ferry_book_two_step` also doubles as the control
that the forced-order machinery works at all before any collapse is called a regression.

### `changed` — 19 perturbations, in three tiers by what they can price

**Tier 1 — 13 unimodal changes. They prove the rules agree; they cannot separate them.**
Both sides are designed to be unimodal, so the realised shape is `5A → 5B`: `tvd 1.000`,
`p 0.007937`, and **status quo, NOVELTY and DISJOINT all fire**. That is worth having as a
detection floor — a rule that misses these is dead on arrival — but a set made only of these
would repeat the exact defect MP-225 names, so they are labelled as what they are.

| id | tools | order | migration shape | baseline → candidate |
|---|---|---|---|---|
| `tc_nursery_batch_move` | 2 | **forced** | two-step collapsed to one | `(get,move)` → `(move,)` |
| `tc_locksmith_charge_guard` | 3 | **forced** | guard dropped before an irreversible charge | `(get,check,charge)` → `(get,charge)` |
| `tc_tailoring_consent_log` | 3 | **forced** | consent record dropped before an irreversible cut | `(get,consent,record)` → `(get,record)` |
| `tc_filmkit_lens_swap` | 3 | **forced** | one step dropped from a three-call swap | `(get,release,reserve)` → `(get,reserve)` |
| `tc_beekeeping_backorder_lookup` | 3 | **forced** | required look-up dropped | `(get_stock,place)` → `(place,)` |
| `tc_cheese_delivery_route` | 3 | **forced** | action tool substituted | `(get,courier)` → `(get,van)` |
| `tc_schoolbus_cover_route` | 3 | **forced** | action tool substituted | `(get,relief)` → `(get,spare)` |
| `tc_scaffold_dismantle_check` | 3 | **forced** | **guard** tool substituted | `(licence,book)` → `(diary,book)` |
| `tc_cidermill_reminder_channel` | 2 | n/a | channel substituted, tool for tool | `(sms,)` → `(email,)` |
| `tc_archive_two_box_request` | 2 | n/a | two per-item calls collapsed to one batch call | `(box,box)` → `(batch,)` |
| `tc_solar_meter_swap` | **4** | **forced** | extra **destructive** call inserted | `(get,log,swap)` → `(get,log,disconnect,swap)` |
| `tc_cleaning_rate_uplift` | **4** | partly forced | extra **destructive** call inserted, over a bimodal baseline | `(get,update[,notify])` → `(get,void,update[,notify])` |
| `tc_taxifleet_service_booking` | 2 | n/a | confirmation look-up **added** | `(book,)` → `(get,book)` |

`tc_cleaning_rate_uplift` is the one Tier-1 row whose baseline is deliberately bimodal (the
optional `notify_client`). Because every candidate trajectory carries `void_old_schedule`, its
key sets stay disjoint anyway — it dilutes the effect size without changing which rules fire,
which makes it a test of whether a rule survives a noisy baseline rather than a discriminator.

**Tier 2 — 3 rate shifts (MP-225). The candidate keeps the baseline's trajectory on a minority
of runs, so DISJOINT goes quiet while the status quo fires.**

| id | tools | order | designed shape |
|---|---|---|---|
| `tc_planthire_offhire_collection` | 2 | n/a | `~5×(end,arrange)` → `~1×(end,arrange)/4×(end,)` |
| `tc_climbinggym_freeze_refund` | 3 | forced head | `~5×(get,freeze,refund)` → `~1×… /4×(get,freeze)` |
| `tc_laundry_route_shortfall` | 2 | **free** | `~5×(swap,flag)` → `~1×(swap,flag)/4×(swap,)` |

**Tier 3 — 3 discretion-removals. A BIMODAL baseline whose candidate commits fully to a mode
the baseline already produced. `4A/1B → 5B`: `tvd 0.800`, `p 0.047619`, status quo FIRES and
BOTH NOVELTY and DISJOINT are quiet.** These are the only pairs in the set on which the three
candidate rules disagree in the **detection** direction, and they are ADR-0041's falsifier #1
in its exact stated form.

| id | tools | the discretionary call the perturbation removes |
|---|---|---|
| `tc_glazier_survey_check` | 3 | `confirm_measurements` before an order that cannot be recalled |
| `tc_upholstery_fabric_hold` | 3 | `reserve_fabric` before booking the last roll into the diary |
| `tc_sailmaker_cloth_check` | 3 | `check_cloth_stock` before committing a loft slot |

**Tiers 2 and 3 are the same bet placed from both sides, and that is deliberate.** Both need a
bimodal baseline, and neither can be guaranteed by construction — the model decides. The
pre-registered branch table:

| baseline lands | Tier 2 realises | Tier 3 realises |
|---|---|---|
| unimodal 5/5 | `5A → 1A/4B` — SQ **fires**, NOV **fires**, DIS quiet. Prices DISJOINT's detection cost | `5A → 5B` — all three fire. Collapses into Tier 1 |
| bimodal 4/1 | `4A/1B → 1A/4B` — `tvd 0.600, p 0.206349`, **quiet under every rule including the status quo** | `4A/1B → 5B` — SQ **fires**, NOV **quiet**, DIS **quiet**. The discriminating cell |

`[M]` Two consequences that constrain what this set can deliver, stated before the run:

1. **A 4/1 → 1/4 rate shift does not fire under the shipped gate at N=5 at all.** MP-225's
   acceptance ("each detected under the shipped engine") is reachable for Tier 2 only through
   the `5A → 1A/4B` branch. If the baselines land bimodal, Tier 2 produces three *false
   negatives of the shipped engine* instead — which this repo also does not currently have, and
   which is worth more than a third confirmation that flips are caught.
2. **Six pairs ride on the same unobservable**, namely whether a stated judgement call actually
   splits. If all six baselines pin at 5/5, the set degenerates to 19 Tier-1 pairs and **cannot
   choose between the three rules at all.** That is the NULL-ARM FAILURE row below, arriving
   through the detection arm instead, and it must be reported as a failure rather than as
   "all three rules scored 19/19".

### Arity and order, as required

`[M]` **2-tool: 13 · 3-tool: 18 · 4-tool: 4.**

**Forced order — the correct trajectory is genuinely two or more calls in a required sequence,
so a collapse is a real regression** (10): `tc_marina_berth_assign`, `tc_boxoffice_seat_move`,
`tc_anchor_ferry_book_two_step`, `tc_nursery_batch_move`, `tc_locksmith_charge_guard`,
`tc_tailoring_consent_log`, `tc_filmkit_lens_swap`, `tc_solar_meter_swap`,
`tc_scaffold_dismantle_check`, `tc_beekeeping_backorder_lookup`. Plus five that force only the
first call — `tc_dentallab_case_setup`, `tc_climbinggym_freeze_refund`,
`tc_glazier_survey_check`, `tc_upholstery_fabric_hold`, `tc_sailmaker_cloth_check`.

**Free order — a reorder is NOT a regression** (6): `tc_toollib_return_and_fee`,
`tc_kennel_boarding_clearance`, `tc_skihire_boot_swap`, `tc_dentallab_case_setup` (its tail),
`tc_parking_permit_renew`, `tc_courier_locker_reassign`. These are also the files that make
`--match unordered` meaningful: under `strict` their two orders are two keys and under
`unordered` they are one, so any rule must be re-scored in both modes before it is chosen.

---

## Design rules

- **One seam per scenario.** No `assertions`, no refusal bait, no argument-only difference, no
  `match` field.
- **No key outside the consumed allowlist** (`messages`, `tools`, `tool_results`, plus the
  adapters' generation keys). `[M]` Verified with `tests/test_suite_roles.py`'s own
  `CONSUMED_INPUT_KEYS`.
- **Content-disjoint by id AND by content hash** from all **95** other declared scenario files (83 distinct content hashes -- `examples/report-suite` legitimately duplicates `examples/drift-suite`).
  `[M]` Verified with the test's own `_content_key`: 35 distinct hashes, 0 collisions. The
  check is mutation-proved rather than assumed — a copy of
  `examples/fp-suite-v2/optional_notify_after_status_update.json` with its `id`, `name` and
  `kind` changed **does** collide, so the guard is live.
- **Every canned `tool_results` entry names a declared tool and every declared tool has one**,
  so no branch of any trajectory hits `_DEFAULT_TOOL_RESULT` and silently becomes `{"status":
  "ok"}` for one side only.
- **Fresh domains throughout.** No print shop, bicycle repair, B&B, rail desk, dermatology
  clinic, retail bank, pharmacy, SQL/BI, citation, currency or Markdown scenario is reused from
  any existing set.

## Known traps this set inherits

1. **`REFUSAL_MARKERS` is a 9-marker English keyword list** substring-matched over the whole
   output. `[M]` No marker appears in any prompt here, but a model can still write one
   spontaneously. A refusal-driven verdict from this set is **soft evidence at best**: quote the
   matched marker out of the stored trace before anyone calls it drift.
2. **Repeated calls of one tool get the same canned result.** `tool_results` is keyed by tool
   NAME (`providers/openai.py:257`), so `tc_archive_two_box_request` and `tc_brewery_keg_credit`
   — the two scenarios whose correct trajectory repeats a tool — cannot distinguish box 4102
   from box 4118 in the result payload. That is fine for the NAME trajectory and would not be
   fine for the argument channel.
3. **`MAX_TOOL_TURNS = 6`.** The longest designed trajectory is 4 calls, so no scenario should
   reach the cap; a run recording `IncompleteReason.tool_turns` here is a defect in the
   scenario, not a signal.
4. **`kind` is inert.** Every file declares `kind: "agent"` for readability; `[M]` nothing in
   `replay/`, `diff/` or `providers/` reads it, and `_content_key` excludes it.

---

## What failure looks like — pre-registered, before any run

This set is not designed toward a foregone success. It can fail in four distinct ways and each
has a different consequence.

| Outcome | Criterion | What gets published |
|---|---|---|
| **SUCCESS** | ≥ 20 scored null trials **and** ≥ 12 of the 19 `changed` pairs realise a firing shape under the status quo, both anchors quiet | The FP count and the detection count for status quo / +NOVELTY / +DISJOINT with the MP-223 severity split, on this set. **A rule may then be chosen.** Still no rate may be claimed from here — role `fit` |
| **PARTIAL** | the two arms disagree in coverage: enough nulls but too few realised detections, or vice versa | The counts for whichever arm has a denominator, and an explicit statement that the other arm did not reach one. No rule is chosen on a one-armed set |
| **NULL-ARM FAILURE** | 0 null trials in which any rule could have fired — i.e. every `equivalent` scenario pinned to one trajectory | The set has reproduced `fp-suite-v2`'s failure in a new channel. `docs/fp-measurement.md` gains the stated limit and MP-224 reopens with the per-scenario `q̂`, not with a third corpus authored the same way |
| **UNREADABLE** | either anchor moves | **Stop.** Not a result of any kind |

**The discriminating evidence in every case is the per-scenario `q̂`** — the observed per-run
rate of each discretionary call, and the observed trajectory histogram per side — read straight
out of the traces and **never** off a verdict. `[M]` That distinction is not pedantry: MP-207's
pilot recorded a live literal as dead because it read the engine's verdict where it needed to
read the per-run rate, and `fp-suite-v3`'s README documents the cost. A scenario at `q̂ = 0` and
a scenario at `q̂ = 1` are both dead nulls, but they are *different* dead and the report must say
which.

**The specific bets that can lose, named in advance:**

1. **The discretion may not be taken.** A model that reads *"use it when you judge it would
   help"* as *"always"* pins the scenario at `q̂ = 1` and contributes nothing to the null
   denominator. `[M]` This is the likeliest single failure and it has precedent: `fp-suite-v2`'s
   `optional_notify_after_status_update` sat at 4/5, which is `q̂ = 0.8` — inside the usable band
   but near its edge, where P(fire) has already fallen to 0.0047.
2. **The reply constraint may not hold.** If a model narrates its trajectory despite *"do not
   describe which tools you used"*, the semantic channel moves with the tool channel and the
   pair is unusable for the same reason the existing 10 rows are. **Check this first**, by
   grepping the stored outputs of the `equivalent` arm for tool names; a set where that grep is
   non-empty must be reported as compromised rather than quietly scored.
3. **The six bimodal-baseline pairs (Tiers 2 and 3) may land unimodal**, in which case they
   collapse into Tier 1, MP-225 is not satisfied by this set, and **no cell on which the three
   candidate rules disagree has been observed at all** — so no rule may be chosen. Report the
   realised `(k_base, k_cand)` for each of the six and say so plainly. This is the single
   result that would send MP-224 back rather than forward.
4. **Three modes being the loudest null is a claim about the arithmetic, not the models.** It
   assumes the modes are roughly equiprobable. A scenario with three *available* trajectories
   and one at 90% behaves like a one-mode scenario.

## Before this set is used

1. Declared in `../../roles.json` (role `fit`) and pinned in
   `tests/test_suite_roles.py::EXPECTED_MEMBERS`. Until both are done,
   `pytest tests/test_suite_roles.py` fails, which is the intended behaviour.
2. **The recall arm is empty for this set by construction.** `scripts/fp_measurement.py` gates
   on `if scn.id in PERTURBATIONS`, and `[M]` the intersection with these 35 ids is empty. The
   `changed` arm is driven from `labels.json`, deliberately, so that a labelled `fit` set can
   never be scored by the same harness path that produces the published bound.
3. **Never write this set's artifacts under `reports/fp-runs/`** — `tests/test_fp_run_of_record.py`
   globs and pools that directory, and a `fit` set's numbers must never reach it.
4. Run a **free pilot first**. The pilot may KILL a scenario (a pinned `q̂` at sufficient n) and
   may never bless one: `[M]` a rate measured on the pilot model transfers to no paid model,
   as `fp-suite-v3`'s README establishes across three model families.
