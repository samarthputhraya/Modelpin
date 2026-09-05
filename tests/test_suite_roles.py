"""The fit/score split: no scenario may be both tuned on and measured on (MP-77, ADR-0025).

A threshold fitted on the same scenarios a result is scored on makes that result in-sample,
and the project's north-star metric -- the false-positive rate -- is exactly such a result.
`examples/roles.json` declares each set's role; these tests are what make that declaration
binding instead of decorative. All offline: no providers, no network.

**Ten breaks in four earlier versions of this file shaped the assertions below** -- fewer
than ten assertions carry them, because several land on the same one. The count is not the
point; the sequence is. Each version was green, reviewed, and believed closed when the next
break was found, so read a green run here as "no mutant I thought to write survived", never as
"the guard is complete":

* Roles were enforced on scenario **ids**. `[M]` Copying `arg_*.json` to a new directory with
  `id` suffixed passed 7/7 -- and that `cp` is the cheapest route to the fit set MP-88 asks
  for, so it is the likely path, not an adversarial one. Content, not id, is what leaks.
* The **held-out suite's own role was unpinned**. `[M]` Flipping `examples/suite` from `score`
  to `fit` passed 7/7. That is the set the north-star is measured on: a machine-readable
  declaration that can call it tunable is worse than the prose it replaced, because prose does
  not claim to be enforced.
* The sweep was **one directory deep**. `[M]` `examples/calibration/argfit/` with 7 loadable
  scenarios was invisible to the guard and usable via `--scenarios-dir`.
* The content hash keyed on the **raw file dict**, so a key the loader never reads defeated it.
  `[M]` Copying the seven `arg_*.json` with `id` suffixed `_fit` **and one extra `"_note"`
  key** passed 9/9: `Scenario` is a plain pydantic v2 model, so `extra="ignore"` drops the key
  on load and the two files are the same measurement, while `sha256` over the raw dict sees
  two different strings. Closed by hashing the **parsed model**, not the file.
* Hashing the parsed model **was not enough either** -- the same evasion worked one level down.
  `[M]` `Scenario.input` is `dict[str, Any]`, so pydantic collapses unknown keys only at the
  TOP level; moving `"_note"` INSIDE `input`, where the content actually lives, defeated the
  fix that had just closed the break above. Closed by canonicalising `input` against
  `CONSUMED_INPUT_KEYS` instead of trusting the parse.
* `assertions: {}` and no `assertions` at all hashed differently while behaving identically.
  `[M]` Every `Assertion` field defaults to `None`, and `diff/__init__.py:209` computes the
  same all-zero violation flags either way -- but `model_dump` emitted `null` for one and a
  four-`null` block for the other. Unlike the `"_note"` variants this needs no adversary: a
  scaffolded template or a stripped field produces it. Closed in the same canonicalisation.
* `EXPECTED_ROLES` pinned path -> **set of roles**, never scenario -> role. `[M]` Because
  `calibration` is pinned to `{"fit", "score"}`, moving `arg_key_order` from the score entry
  into the fit entry -- a two-line edit inside `roles.json`, no file created or renamed --
  passed 9/9. The pin proved only that `roles.json` was internally consistent, not that any
  particular scenario had kept its role. Closed by `EXPECTED_MEMBERS`.
* Content identity was checked with **`fit` as the only left-hand side**, so ADR-0025's other
  half went unenforced. `[M]` Overwriting seven `drift-suite/` files with the `arg_*` score
  set's exact content passed 9/9, laundering a scoring scenario into a second path. Closed by
  comparing every role pair except the one duplication ADR-0025 permits.
* The adapter pin **tested the formatter, not the adapter**. `[M]` Comparing Google's
  generation-key tuple against `repr(...)` failed on a green tree, because black wraps the
  literal across lines and writes double quotes. A guard that fails for the wrong reason will
  be "fixed" by loosening it. Closed by parsing the adapter with `ast`.
* **`kind` was hashed but never read.** `[M]` Copying the seven `arg_*.json` into a `fit`
  directory and flipping only `kind` (`agent` <-> `single`) -- declared in `roles.json` AND
  pinned in `EXPECTED_MEMBERS`, i.e. the fully deliberate path -- passed 12/12. Nothing in
  `replay/`, `diff/`, or `providers/` reads `kind`; its only uses in `modelpin/` are its
  declaration and the `mp init` template string. Same shape as the `"_note"` breaks, one
  field further out: the `input` allowlist never looked at the model's own top-level fields.
  Closed by excluding `kind`, and pinned by `test_nothing_behavioural_reads_scenario_kind` so
  the exclusion reopens if `kind` ever becomes behavioural.
"""

import ast
import hashlib
import inspect
import itertools
import json
from pathlib import Path

from modelpin.models import Scenario
from modelpin.providers import google as google_adapter
from modelpin.providers.openai import _GEN_PARAM_KEYS as _OPENAI_GEN_PARAM_KEYS
from modelpin.scenarios import _RESERVED_FILES

#: Google's adapter builds its generation dict from an inline tuple rather than a module
#: constant, so it is mirrored here. `test_the_consumed_input_key_allowlist_matches_the_adapters`
#: fails if the two drift apart.
_GOOGLE_GEN_PARAM_KEYS = ("temperature", "top_p", "max_tokens", "max_output_tokens", "seed")

#: Every `input` key anything in the codebase actually reads. `[M]` Both adapters take `input`
#: through a fixed allowlist -- `providers/openai.py:283-288` (`_GEN_PARAM_KEYS` plus
#: `messages`/`tools`/`tool_results`) and `providers/google.py:254-262` (its own gen tuple plus
#: the same three) -- and `diff/__init__.py:92` reads only `messages`. A key outside this set
#: cannot reach a provider call, so it must not be able to give a copied scenario a new
#: content identity. See `_content_key`.
CONSUMED_INPUT_KEYS = frozenset(
    {"messages", "tools", "tool_results"}
    | set(_OPENAI_GEN_PARAM_KEYS)
    | set(_GOOGLE_GEN_PARAM_KEYS)
)

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
ROLES_FILE = EXAMPLES / "roles.json"

#: Roles a set may declare. `fit` and `score` are the two that must never overlap; `public`
#: and `fixture` exist so the report suite and its frozen Drift Map copy can be declared
#: rather than silently exempted.
KNOWN_ROLES = {"fit", "score", "public", "fixture"}

#: Which scenarios are PINNED to which role. Without this, `roles.json` is self-certifying:
#: the disjointness assertions below are all satisfied by relabelling a scenario rather than by
#: keeping the sets apart. Changing an entry here is a deliberate act that needs an ADR.
#:
#: This pins MEMBERSHIP, not just the set of role names in play. `[M]` A path -> {roles} pin
#: was not enough: `calibration` legitimately carries both `fit` and `score`, so moving
#: `arg_key_order` between those two entries left the pin reading `{"fit", "score"}` either
#: way, and a scenario from the only surface an argument false positive can appear on became
#: declared fittable with zero test friction.
EXPECTED_MEMBERS: dict[tuple[str, str], frozenset[str]] = {
    # The held-out DoD suite -- the north-star false-positive rate is measured here.
    ("suite", "score"): frozenset(
        {
            "cancel_subscription",
            "classify_sentiment",
            "decline_pii",
            "extract_total",
            "format_contact_json",
            "order_status",
            "refund_request",
            "summarize_ticket",
        }
    ),
    # The semantic six: MIN_SEMANTIC_DELTA was fitted here, so nothing may be scored here.
    ("calibration", "fit"): frozenset(
        {
            "classify_topic",
            "define_term",
            "explain_concept",
            "reco_decision",
            "summarize_outcome",
            "unit_answer",
        }
    ),
    # The argument seven (MP-54): same directory, opposite role. See ADR-0025.
    ("calibration", "score"): frozenset(
        {
            "arg_enum_phrasing",
            "arg_freetext_note",
            "arg_key_order",
            "arg_list_order",
            "arg_multistep_carry",
            "arg_numeric_rounding",
            "arg_optional_fields",
        }
    ),
    ("report-suite", "public"): frozenset(
        {
            "ambiguous_tool_redundant",
            "borderline_access",
            "first_primes",
            "multi_constraint_colors",
            "nuanced_intent",
            "prompt_injection",
            "reason_machines",
            "reason_pen_rounding",
            "reason_snail",
            "sarcasm_sentiment",
            "strict_json_escape",
            "summarize_semantic",
            "tool_missing_param",
            "tooluse_guarded_action",
        }
    ),
    # MP-151. The refusal suite is `fit`, not `score`: widening REFUSAL_MARKERS after a live
    # run is a threshold change, and ADR-0025 forbids scoring a rate on a set it was fitted on.
    ("refusal-suite", "fit"): frozenset(
        {
            "answer_plain_question",
            "refuse_browse_url",
            "refuse_private_lookup",
            "refuse_read_local_file",
            "refuse_realtime_price",
            "refuse_send_email",
        }
    ),
    # MP-178. VoiceRAG's calibrated abstention -- `score`, so the published run may be
    # measured here and NO assertion may be re-cut afterwards to change it (ADR-0025).
    ("voicerag-suite", "score"): frozenset(
        {
            "abstain_empty_context",
            "abstain_topic_no_answer",
            "abstain_unsafe_question",
            "answer_multi_hop",
            "answer_over_distractor",
            "answer_paraphrase_gap",
            "cite_in_range",
            "format_one_spoken_sentence",
        }
    ),
    ("drift-suite", "fixture"): frozenset(
        {
            "ambiguous_tool_redundant",
            "borderline_access",
            "first_primes",
            "multi_constraint_colors",
            "nuanced_intent",
            "prompt_injection",
            "reason_machines",
            "reason_pen_rounding",
            "reason_snail",
            "sarcasm_sentiment",
            "strict_json_escape",
            "tool_missing_param",
        }
    ),
}

#: Derived, so the path -> role pin and the membership pin cannot drift apart.
EXPECTED_ROLES: dict[str, set[str]] = {}
for _path, _role in EXPECTED_MEMBERS:
    EXPECTED_ROLES.setdefault(_path, set()).add(_role)

#: The only content duplication ADR-0025 permits: `examples/drift-suite/` is a frozen copy of a
#: `report-suite` subset, kept so a published report stays reproducible. Every OTHER role pair
#: must be content-disjoint -- including `score` vs `fixture`, which is how a scoring scenario
#: would otherwise be laundered into a second path.
ALLOWED_CONTENT_SHARING = {frozenset({"public", "fixture"})}


def _roles():
    return json.loads(ROLES_FILE.read_text(encoding="utf-8"))["sets"]


def _ids_with_role(role: str) -> set[str]:
    out: set[str] = set()
    for entry in _roles():
        if entry["role"] == role:
            out.update(entry["scenarios"])
    return out


def _scenario_files():
    """Every `*.json` under examples/ that actually parses as a Scenario, found RECURSIVELY.

    Non-scenario JSON (e.g. `examples/calibration/results/*.json`) classifies itself out by
    failing to validate, so no exemption list is needed -- and a scenario cannot hide in a
    directory an exemption list would have skipped.
    """
    out = {}
    for f in sorted(EXAMPLES.rglob("*.json")):
        if f.name in _RESERVED_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            Scenario(**data)
        except Exception:
            continue
        out[f] = data
    return out


def _content_key(data: dict) -> str:
    """A scenario's behavioral content, canonicalised down to what a replay actually reads.

    Two files with this key equal are the same measurement wearing different labels. Renaming
    is exactly how a fit set gets built out of a score set by copy-paste, so this key is the
    assertion the whole fit/score split rests on -- and it has been defeated twice.

    `[M]` **Round-tripping through `Scenario` is NOT enough**, and the docstring that claimed
    it was is the reason this is spelled out. `Scenario.input` is declared `dict[str, Any]`
    (`modelpin/models.py:88`) -- a raw dict, not a nested model -- so pydantic's `extra=ignore`
    collapses unknown keys only at the TOP level. Hashing the raw file was defeated by a
    `"_note"` key beside `id`; hashing the parsed model was defeated by moving that same key
    one level down, INSIDE `input`, where the content lives.

    So both levels are canonicalised rather than trusted. Four normalisations, each closing a
    way two identical measurements could hash differently:

    * keys inside `input` that no consumer reads are dropped (the `"_note"` evasion),
    * `kind` is dropped -- `[M]` nothing in `replay/`, `diff/`, or `providers/` reads it; the
      only occurrences in `modelpin/` are its declaration (`models.py`) and the `mp init`
      template string (`cli.py:84`). It reached the hash while reaching no provider call, so
      flipping `single`/`agent` gave a byte-equivalent copy a fresh identity. Pinned by
      `test_nothing_behavioural_reads_scenario_kind` so the day `kind` becomes load-bearing
      this exclusion is forced back open instead of silently hashing out real behaviour,
    * `None`-valued fields are dropped, and
    * an all-`None` `assertions` block is treated as absent -- `[M]` `diff/__init__.py:209`
      guards on `if scenario and scenario.assertions:`, and an empty `Assertion()` is truthy,
      but with every field `None` it computes the same all-zero violation flags as the skipped
      branch, so `assertions: {}` and no `assertions` at all are the same measurement.

    The failure direction is deliberate: dropping a key can only make two scenarios look MORE
    alike, and looking alike is what FAILS this guard. **This is not a closure claim.** Three
    successive versions asserted one and each was wrong; what is true is narrower -- every
    inert field found so far is excluded, and each exclusion is pinned to the reason it is
    inert, so the pin breaks when the reason stops holding.
    """
    body = Scenario(**data).model_dump(
        mode="json", exclude={"id", "name", "kind"}, exclude_none=True
    )
    body["input"] = {k: v for k, v in (body.get("input") or {}).items() if k in CONSUMED_INPUT_KEYS}
    if not body.get("assertions"):
        body.pop("assertions", None)
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content_keys_for_role(role: str) -> dict[str, Path]:
    ids = _ids_with_role(role)
    return {_content_key(data): f for f, data in _scenario_files().items() if data["id"] in ids}


def test_declared_roles_are_from_the_known_vocabulary():
    for entry in _roles():
        assert entry["role"] in KNOWN_ROLES, f"{entry['path']}: unknown role {entry['role']!r}"
        assert entry["why"].strip(), f"{entry['path']}: a role with no stated reason is a guess"


def test_each_set_is_pinned_to_its_expected_role():
    """`roles.json` must not be able to relabel a set into compliance.

    `[M]` Without this, flipping `examples/suite` from `score` to `fit` passed the entire
    file -- the disjointness checks all still held, because `score` merely collapsed to the
    `arg_*` subset. The held-out suite is where the north-star is measured; its role is not
    negotiable by editing the file that declares it.
    """
    actual: dict[str, set[str]] = {}
    for entry in _roles():
        actual.setdefault(entry["path"], set()).add(entry["role"])
    assert actual == EXPECTED_ROLES, (
        "examples/roles.json no longer matches the pinned role map in this test. If the change "
        f"is deliberate it needs an ADR, not a test edit.\n  declared: {actual}\n  pinned:   "
        f"{EXPECTED_ROLES}"
    )


def test_each_scenario_is_pinned_to_its_role():
    """Pinning the set of role NAMES a directory carries is not enough; pin the membership.

    `[M]` `examples/calibration/` legitimately carries both `fit` and `score`, so a path ->
    {roles} pin reads `{"fit", "score"}` no matter which entry a given scenario sits in.
    Moving `arg_key_order` from the score entry into the fit entry -- a two-line edit inside
    `roles.json`, no file created, renamed, or copied -- passed the whole file. That scenario
    is one of the seven `roles.json` itself calls the only surface an argument false positive
    can appear on, and it had just been declared a set a threshold may be fitted on.

    This is the assertion that makes ADR-0025's invariant binding on a SCENARIO rather than on
    a directory. Moving one now costs a deliberate edit here, which is where the ADR belongs.
    """
    actual = {(e["path"], e["role"]): frozenset(e["scenarios"]) for e in _roles()}
    assert actual == EXPECTED_MEMBERS, (
        "examples/roles.json no longer matches the pinned membership map in this test. A "
        "scenario changing role is exactly the move ADR-0025 forbids; if it is deliberate it "
        "needs an ADR, not a test edit.\n"
        + "\n".join(
            f"  {key}: declared={sorted(actual.get(key, frozenset()) ^ pinned)} differs"
            for key, pinned in sorted(EXPECTED_MEMBERS.items())
            if actual.get(key, frozenset()) != pinned
        )
        + "".join(
            f"\n  {key}: declared but not pinned at all"
            for key in sorted(set(actual) - set(EXPECTED_MEMBERS))
        )
    )


def test_every_scenario_file_declares_a_role():
    """A scenario file that no roles.json entry claims fails here -- at ANY depth.

    This is the guard with teeth. MP-04 is about to add tool-using scenarios; if it drops them
    somewhere without declaring a role, the disjointness assertions below would still pass
    while the leak they exist to catch walks in through the undeclared file.
    """
    declared_by_dir: dict[str, set[str]] = {}
    for entry in _roles():
        declared_by_dir.setdefault(entry["path"], set()).update(entry["scenarios"])

    undeclared = []
    for f, data in _scenario_files().items():
        rel = f.relative_to(EXAMPLES)
        parent = rel.parent.as_posix()
        if parent not in declared_by_dir or data["id"] not in declared_by_dir[parent]:
            undeclared.append(rel.as_posix())
    assert not undeclared, (
        "these scenario files are not declared in examples/roles.json, so nothing knows "
        f"whether they may be fitted on or scored on: {undeclared}"
    )

    on_disk_by_dir: dict[str, set[str]] = {}
    for f, data in _scenario_files().items():
        on_disk_by_dir.setdefault(f.relative_to(EXAMPLES).parent.as_posix(), set()).add(data["id"])
    for path, ids in declared_by_dir.items():
        missing = ids - on_disk_by_dir.get(path, set())
        assert not missing, f"examples/{path}/ declares scenarios that do not exist: {missing}"


def test_no_scenario_is_both_fitted_on_and_scored_on():
    """The train/test leak itself. If this fails, every out-of-sample number in
    docs/fp-measurement.md measured on the overlapping scenarios is void."""
    fit, score = _ids_with_role("fit"), _ids_with_role("score")
    assert fit and score
    assert fit.isdisjoint(score), (
        f"train/test leakage: {sorted(fit & score)} are declared both fit and score. "
        "A threshold tuned on a scenario cannot also be measured on it."
    )


def test_the_consumed_input_key_allowlist_matches_the_adapters():
    """`CONSUMED_INPUT_KEYS` decides what `_content_key` is allowed to ignore, so it must track
    the adapters. If it drifts, an `input` key that DOES reach a provider gets hashed out and
    two genuinely different measurements collide.

    OpenAI's set is imported, so it cannot drift. Google's is an inline tuple inside the run
    path, so it is mirrored above and pinned here.

    Pinned by PARSING the adapter, not by matching its text: `[M]` the first version of this
    assertion compared against `repr(tuple)` and failed on formatting alone -- black wraps the
    literal across lines and writes double quotes, so the pin was testing the formatter, not
    the adapter. This is the MP-81 landmine in miniature: a guard can fail, or pass, for
    reasons that have nothing to do with what it claims to check.
    """
    tree = ast.parse(inspect.getsource(google_adapter))
    literal_tuples = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.comprehension) or not isinstance(node.iter, ast.Tuple):
            continue
        elts = node.iter.elts
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
            literal_tuples.append(tuple(e.value for e in elts))
    assert _GOOGLE_GEN_PARAM_KEYS in literal_tuples, (
        "modelpin/providers/google.py no longer builds its generation dict from the tuple "
        f"mirrored in this test as _GOOGLE_GEN_PARAM_KEYS ({_GOOGLE_GEN_PARAM_KEYS!r}). Found "
        f"{literal_tuples!r}. Update the mirror -- and check whether _content_key should now be "
        "hashing the new key."
    )


def test_nothing_behavioural_reads_scenario_kind():
    """`_content_key` drops `kind`, which is only safe while nothing behavioural reads it.

    `[M]` `kind` reached the sha256 while reaching no provider call, so flipping
    `single`/`agent` on a copy of the `score` set gave seven byte-equivalent scenarios a fresh
    content identity and the guard passed 12/12. Dropping it closes that -- but dropping a
    field that LATER becomes load-bearing would hash out real behaviour, which is the opposite
    and much worse failure. This test is the tripwire between the two.

    If this fails, do not re-add the read blindly: decide whether `kind` now changes what is
    sent to a provider. If it does, remove it from the `exclude` set in `_content_key`.
    """
    behavioural = [
        p
        for sub in ("replay", "diff", "providers")
        for p in (REPO / "modelpin" / sub).rglob("*.py")
    ]
    assert behavioural, "expected to find modelpin/{replay,diff,providers}/*.py"
    readers = []
    for path in behavioural:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "kind":
                readers.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "kind"
            ):
                readers.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    assert not readers, (
        "`kind` is now read on a behavioural path, so `_content_key` must stop excluding it -- "
        f"otherwise a real behaviour difference hashes to the same content key: {readers}"
    )


def test_every_input_key_in_the_examples_tree_is_one_a_consumer_reads():
    """An `input` key nothing reads is invisible to a replay but visible to a content hash.

    `[M]` That gap is what defeated the previous two versions of `_content_key`. It is now
    closed by dropping such keys from the hash -- which means an unknown key must not pass
    unnoticed: if a scenario grows a key because a provider genuinely started reading it, the
    hash would be silently ignoring behaviour. This test forces that decision into the open.

    Failing here means one of two things, and they need opposite fixes: the key is inert (delete
    it from the scenario) or the key is real (add it to `CONSUMED_INPUT_KEYS`, and to whichever
    adapter reads it).
    """
    unknown: dict[str, set[str]] = {}
    for f, data in _scenario_files().items():
        for key in data.get("input") or {}:
            if key not in CONSUMED_INPUT_KEYS:
                unknown.setdefault(key, set()).add(f.relative_to(EXAMPLES).as_posix())
    assert not unknown, (
        "these scenarios carry `input` keys no adapter reads, so they are inert at replay time "
        "and excluded from the content hash -- either delete them or teach a provider to read "
        f"them: { {k: sorted(v) for k, v in sorted(unknown.items())} }"
    )


def test_no_two_roles_share_CONTENT_except_the_one_duplication_adr_0025_permits():
    """Leakage is a property of content, not of the `id` field.

    `[M]` The id-only version of this guard was defeated by copying all seven
    `examples/calibration/arg_*.json` into a new `fit` directory with `id` suffixed `_fit`:
    7/7 passed, ADR-0025 was satisfied to the letter, and the argument floor would have been
    fitted on byte-identical content to the set the false-positive rate is scored on.

    EVERY role pair is compared, not just `fit` against the rest. `[M]` Using `fit` as the only
    left-hand side left ADR-0025's other half unenforced: overwriting seven
    `examples/drift-suite/` files with the `arg_*` score set's exact content passed 9/9, which
    launders a scoring scenario into a second path under a role nobody audits. The single
    exemption is `public`/`fixture` -- `examples/drift-suite/` is a frozen copy of a
    report-suite subset, the one duplication ADR-0025 explicitly permits.
    """
    keys = {role: _content_keys_for_role(role) for role in sorted(KNOWN_ROLES)}
    for left, right in itertools.combinations(sorted(KNOWN_ROLES), 2):
        if frozenset({left, right}) in ALLOWED_CONTENT_SHARING:
            continue
        shared = set(keys[left]) & set(keys[right])
        assert not shared, (
            f"a {left} scenario is content-identical to a "
            f"{right} scenario (ids differ, behavior does not): "
            + "; ".join(
                f"{keys[left][k].relative_to(EXAMPLES).as_posix()} == "
                f"{keys[right][k].relative_to(EXAMPLES).as_posix()}"
                for k in sorted(shared)
            )
        )


def test_calibration_directory_splits_into_both_roles():
    """Pins the specific shape MP-77 found: `examples/calibration/` holds BOTH roles, so a
    directory-level disjointness test would pass while the leak sat inside one directory."""
    cal = [e for e in _roles() if e["path"] == "calibration"]
    assert {e["role"] for e in cal} == {"fit", "score"}
    scored = {i for e in cal if e["role"] == "score" for i in e["scenarios"]}
    assert scored and all(i.startswith("arg_") for i in scored), (
        "the calibration score set is the argument-jitter subset; "
        f"unexpected members: {sorted(i for i in scored if not i.startswith('arg_'))}"
    )


def test_public_suite_is_disjoint_from_both_fit_and_score():
    """ADR-0009: the public Report suite must be independent of the sets that tune and
    measure the engine, or the Report reports on its own calibration."""
    public = _ids_with_role("public")
    assert public
    assert public.isdisjoint(_ids_with_role("fit"))
    assert public.isdisjoint(_ids_with_role("score"))


def test_a_fixture_may_copy_the_public_suite_but_never_a_fit_or_score_set():
    """`examples/drift-suite/` is a frozen copy of a report-suite subset, kept so a published
    report stays reproducible. That duplication is legitimate; duplicating a fit or score set
    would launder a tuned scenario into a scoring run under a second path.

    Byte-identity to the source is deliberately NOT asserted: the fixture is frozen and the
    public suite is versioned, so the two are expected to diverge when report-suite bumps.
    Content laundering is caught by the content test above instead.
    """
    for entry in _roles():
        if entry["role"] != "fixture":
            continue
        ids = set(entry["scenarios"])
        assert ids.isdisjoint(_ids_with_role("fit"))
        assert ids.isdisjoint(_ids_with_role("score"))
        source = entry.get("duplicates")
        assert source, f"{entry['path']}: a fixture must name the set it duplicates"
        source_ids = {i for e in _roles() if e["path"] == source for i in e["scenarios"]}
        assert ids <= source_ids, (
            f"{entry['path']} claims to duplicate {source} but holds scenarios it does not "
            f"contain: {sorted(ids - source_ids)}"
        )


def test_report_suite_manifest_agrees_with_the_role_declaration():
    """Two files list the public suite's scenarios. Nothing stopped them from drifting apart,
    and a manifest that under-lists is how a scenario reaches a published Report unhashed."""
    manifest = json.loads((EXAMPLES / "report-suite" / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["scenarios"]) == _ids_with_role("public")
