#!/usr/bin/env python3
"""
Closed-world detector study for the Governance Drift paper (v3).

v3 extends the vector semantics and scoring:
  * Twelve drift scenarios include three compound cases. Detectors emit the
    full set decidable at each tier; exact-set agreement, subset-only partial
    diagnosis, and Hamming loss replace member-of-set single-label scoring.
  * Evidence drift is modeled as an epistemic output: missing evidence makes
    affected substantive components undecidable rather than inconsistent.

v2 established the definition-faithful baseline:
  * Class set identical to the paper's taxonomy (configuration, policy,
    authorization, intent, evidence, environment) -- no extra classes.
    Ground truth is a SET per scenario (component distances are a vector;
    scenarios may drift in more than one component).
  * Evidence drift exercised: scenario S9 removes authenticated live status
    required by a continuing approval while retaining immutable proof; the
    detector reports an explicit undecidable/evidence verdict, not a polar
    authorization verdict.
  * T1 implements Definition 3 (admission re-evaluation): policy versions
    carry requirement sets evaluated against the running manifest; a policy
    revision the running state satisfies raises NO alarm (benign supersession
    exists in the churn model and must not alarm).
  * T4 compares observed environment against the recorded assumptions
    sigma_0 in the approved snapshot (not literals).
  * Benign GOVERNANCE churn in the control and drift streams: satisfied
    policy revisions, approved updates (new revision+digest+approval,
    everything consistent), and hygienic exceptions removed at expiry.
    False-alarm rates for T1..T4 are therefore measured under governance
    noise, not vacuously zero.
  * Approvals are a set with subject digests, covered revisions, windows,
    and revocation; "legitimate successors" are modeled (approved updates
    extend coverage).
  * Expiry check uses >= (latency 0 semantics); latencies printed with one
    decimal; the emitted LaTeX table is fully programmatic including the
    tier subheader.
  * Churn-rate sensitivity for the naive comparator's false-alarm rate.

Tiers (cumulative): T0 naive config comparison (runtime-owned fields
included), T0n normalized, T1 +policy evaluation, T2 +authorization records,
T3 +artifact lineage, T4 +environment inventory vs sigma_0.

Scenarios: S0 control; S1--S9 the single/paired live-lab cases; S10 policy
plus expired authorization; S11 artifact substitution plus environment
change; S12 rollback plus loss of required continuing-authority status.

Counterfactual scoring: drift is detected iff the alarm sequence differs
from a paired same-seed no-drift control (identical churn); an
always-alarming detector carries no information and scores zero.

Deterministic. Pure stdlib. Outputs: data/matrix.csv, data/detector_raw.csv,
data/fp_sensitivity.csv, data/table_matrix_full.tex.
"""

import csv, os, random, statistics

SEEDS = list(range(20))
N_EVENTS = 400
ONSET = 150
CHURN_RATE = 0.30          # runtime churn probability per event
GOV_CHURN_RATE = 0.02      # benign governance-event probability per event
EXC_WINDOW = 40

SCENARIOS = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9",
             "S10", "S11", "S12"]
TIERS = ["T0", "T0n", "T1", "T2", "T3", "T4"]
CLASS_ORDER = ("configuration", "policy", "intent", "authorization", "environment", "evidence")

# ground-truth drift-class SETS (taxonomy classes only)
CLASSES = {
    "S0": set(),
    "S1": {"configuration"},
    "S2": {"authorization"},
    "S3": {"policy"},
    "S4": {"authorization"},          # digest no valid approval covers
    "S5": {"environment"},
    "S6": {"intent", "authorization"},  # both components drift (Defs 4, 5)
    "S7": {"environment"},
    "S8": {"authorization"},
    "S9": {"evidence"},
    "S10": {"policy", "authorization"},
    "S11": {"authorization", "environment"},
    # The rollback lacks an integrity-valid approval path (intent), while
    # missing live status makes current authorization undecidable (evidence).
    "S12": {"intent", "evidence"},
}

# policy versions as requirement sets over manifest keys
POLICIES = {
    "pi-7":  set(),                 # baseline requirements (satisfied)
    "pi-7b": set(),                 # benign editorial revision (satisfied)
    "pi-8":  {"team_owner"},        # requires a field the manifest lacks
}


# ----------------------------------------------------------------- state model
def initial_state(rng):
    d0 = "sha256:aaa111"
    approved = dict(
        manifest=dict(image_tag="svc:1.4.2", replicas=3, env="prod",
                      resources="std"),
        policy_version="pi-7",
        sigma0=dict(iam_scope="least-priv", cloud_lb="standard-config"),
    )
    world = dict(
        git=dict(approved["manifest"]),
        git_revision="rev-42",
        observed=dict(approved["manifest"]),
        observed_digest=d0,
        registry={"svc:1.4.2": d0},
        attested_digests={d0},        # tool-local trust input, not admitted-basis history
        policy_version="pi-7",
        env=dict(iam_scope="least-priv", cloud_lb="standard-config"),
        exceptions=[],               # dicts: id, expires, removed_at
        approvals={"APR-1": dict(subjects={d0}, revisions={"rev-42"},
                                 mode="continuing", executed_at=0,
                                 valid_at_execution=True, proof_available=True,
                                 live_status_available=True, revoked=False,
                                 revocation_effect="prospective")},
        chain_approval="APR-1",
        pod_hash="h0", replicas=3,
        next_rev=43, next_dig=0,
    )
    return approved, world


def benign_runtime_churn(world, rng):
    if rng.random() < 0.5:
        world["replicas"] = rng.choice([2, 3, 4, 5])
    else:
        world["pod_hash"] = f"h{rng.randrange(1_000_000)}"


def benign_governance_churn(world, rng, k):
    kind = rng.choice(["policy_flip", "approved_update", "hygienic_exc"])
    if kind == "policy_flip":
        world["policy_version"] = ("pi-7b" if world["policy_version"] == "pi-7"
                                   else "pi-7")
    elif kind == "approved_update":
        rev = f"rev-{world['next_rev']}"; world["next_rev"] += 1
        dig = f"sha256:new{world['next_dig']:03d}"; world["next_dig"] += 1
        aid = f"APR-{rev}"
        world["approvals"][aid] = dict(subjects={dig}, revisions={rev},
                                       mode="continuing", executed_at=k,
                                       valid_at_execution=True,
                                       proof_available=True,
                                       live_status_available=True,
                                       revoked=False,
                                       revocation_effect="prospective")
        world["git_revision"] = rev
        world["registry"][world["git"]["image_tag"]] = dig
        world["observed_digest"] = dig
        world["attested_digests"].add(dig)
        world["chain_approval"] = aid
    else:
        world["exceptions"].append(dict(id=f"EXC-b{k}", expires=k + 10,
                                        removed_at=k + 10))


def inject(scn, world, k):
    if scn == "S1":
        world["observed"]["resources"] = "patched-by-hand"
    elif scn == "S2":
        world["exceptions"].append(dict(id="EXC-1", expires=k + EXC_WINDOW,
                                        removed_at=None))  # never removed
    elif scn == "S3":
        world["policy_version"] = "pi-8"
    elif scn == "S4":
        new = "sha256:bbb222"
        world["registry"][world["git"]["image_tag"]] = new
        world["observed_digest"] = new
    elif scn == "S5":
        world["env"]["iam_scope"] = "broadened"
    elif scn == "S6":
        world["git"] = dict(world["git"], image_tag="svc:1.3.9")
        world["git_revision"] = "rev-37"
        world["observed"] = dict(world["git"])
        world["observed_digest"] = "sha256:old999"
    elif scn == "S7":
        world["env"]["cloud_lb"] = "tls-policy-downgraded"
    elif scn == "S8":
        world["observed_digest"] = "sha256:ccc333"
    elif scn == "S9":
        for approval in world["approvals"].values():
            approval["live_status_available"] = False
    elif scn == "S10":
        world["policy_version"] = "pi-8"
        world["exceptions"].append(dict(id="EXC-c1", expires=k,
                                        removed_at=None))
    elif scn == "S11":
        world["observed_digest"] = "sha256:compound"
        world["env"]["cloud_lb"] = "tls-policy-downgraded"
    elif scn == "S12":
        world["git"] = dict(world["git"], image_tag="svc:1.3.9")
        world["git_revision"] = "rev-37"
        world["observed"] = dict(world["git"])
        world["observed_digest"] = "sha256:old999"
        for approval in world["approvals"].values():
            approval["live_status_available"] = False


# -------------------------------------------------------------------- detector
def satisfies(manifest, policy_version):
    return all(req in manifest for req in POLICIES[policy_version])


def applicable_approvals(world, k):
    """Return (applicable records, decidable) under mode-aware semantics."""
    applicable_records = []
    for approval in world["approvals"].values():
        if not approval.get("proof_available", False):
            return [], False
        mode = approval.get("mode", "one-shot")
        if mode == "one-shot":
            if not approval.get("valid_at_execution", False):
                continue
            if (approval.get("revocation_effect") == "retroactive" and
                    not approval.get("live_status_available", False)):
                return [], False
            if approval.get("revoked") and approval.get("revocation_effect") == "retroactive":
                continue
            applicable_records.append(approval)
        elif mode in {"continuing", "temporary-exception"}:
            if not approval.get("live_status_available", False):
                return [], False
            if not approval.get("revoked") and k <= approval.get("expires", 10**18):
                applicable_records.append(approval)
        else:
            raise ValueError(f"unknown authorization mode: {mode}")
    return applicable_records, True


def intent_coverage(world):
    """Return (covered, decidable) from immutable execution-time lineage.

    Current continuing-authority status belongs to authorization, not to the
    historical question whether the driving revision has a recorded approved
    path. Missing or invalid proof still makes intent undecidable.
    """
    if not world.get("basis_available", True):
        return False, False
    approvals = list(world["approvals"].values())
    if not approvals or any(not row.get("proof_available", False) for row in approvals):
        return False, False
    return (
        any(world["git_revision"] in row.get("revisions", set()) for row in approvals),
        True,
    )


def detect(tier, approved, world, k):
    """Pure function of the tier's visible streams.
    Returns the complete class set decidable at that tier. Evidence denotes
    an epistemic failure to decide one or more substantive components."""
    classes = set()
    diffs = [f for f in world["git"]
             if world["git"][f] != world["observed"].get(f)]
    if tier == "T0":
        if diffs or world["replicas"] != approved["manifest"]["replicas"]:
            classes.add("configuration")
        ordered = tuple(x for x in CLASS_ORDER if x in classes)
        return bool(ordered), ordered
    if diffs:
        classes.add("configuration")
    if tier == "T0n":
        ordered = tuple(x for x in CLASS_ORDER if x in classes)
        return bool(ordered), ordered

    # T1: admission re-evaluation against the current policy version
    if not satisfies(world["observed"], world["policy_version"]):
        classes.add("policy")
    if tier == "T1":
        ordered = tuple(x for x in CLASS_ORDER if x in classes)
        return bool(ordered), ordered

    # T2: authorization validity + evidence decidability + intent coverage
    for exc in world["exceptions"]:
        if k >= exc["expires"] and \
           (exc["removed_at"] is None or exc["removed_at"] > k):
            classes.add("authorization")
    va, authorization_decidable = applicable_approvals(world, k)
    if not authorization_decidable:
        classes.add("evidence")      # current authorization is undecidable
    elif not va:
        classes.add("authorization")
    intent_covered, intent_decidable = intent_coverage(world)
    if not intent_decidable:
        classes.add("evidence")
    elif not intent_covered:
        classes.add("intent")
    if tier == "T2":
        ordered = tuple(x for x in CLASS_ORDER if x in classes)
        return bool(ordered), ordered

    # T3: artifact lineage (running digest covered by a valid approval)
    if va:
        if not any(world["observed_digest"] in a["subjects"] for a in va):
            classes.add("authorization")
        tag_dig = world["registry"].get(world["git"]["image_tag"])
        if tag_dig is not None and \
           not any(tag_dig in a["subjects"] for a in va):
            classes.add("authorization")
    if tier == "T3":
        ordered = tuple(x for x in CLASS_ORDER if x in classes)
        return bool(ordered), ordered

    # T4: environment inventory vs recorded assumptions sigma_0
    if world["env"] != approved["sigma0"]:
        classes.add("environment")
    ordered = tuple(x for x in CLASS_ORDER if x in classes)
    return bool(ordered), ordered


# ------------------------------------------------------------------------- run
def alarm_stream(scn, tier, seed, churn=CHURN_RATE):
    rng = random.Random(10_000 + seed)
    approved, world = initial_state(rng)
    out = []
    for k in range(N_EVENTS):
        if rng.random() < churn:
            benign_runtime_churn(world, rng)
        if rng.random() < GOV_CHURN_RATE:
            benign_governance_churn(world, rng, k)
        if scn != "S0" and k == ONSET:
            inject(scn, world, k)
        out.append(detect(tier, approved, world, k))
    return out


def run(scn, tier, seed):
    drift = alarm_stream(scn, tier, seed)
    control = alarm_stream("S0", tier, seed)
    false_alarms = sum(1 for a, _ in control if a)
    first_diff = None
    alarm_classes = ()
    for k in range(N_EVENTS):
        if drift[k] != control[k]:
            first_diff = k
            alarm_classes = drift[k][1] if drift[k][0] else ()
            break
    onset = ONSET + EXC_WINDOW if scn == "S2" else ONSET
    detected = first_diff is not None and scn != "S0"
    latency = (first_diff - onset) if detected else None
    observed_set = set(alarm_classes)
    correct = detected and observed_set == CLASSES[scn]
    subset_correct = detected and bool(observed_set) and observed_set <= CLASSES[scn]
    return dict(scn=scn, tier=tier, seed=seed, detected=int(detected),
                correct=int(bool(correct)),
                subset_correct=int(bool(subset_correct)),
                latency=latency if latency is not None else "",
                false_alarms=false_alarms,
                alarm_class_set="|".join(alarm_classes),
                first_priority=(alarm_classes[0] if alarm_classes else ""),
                hamming_loss=len(observed_set ^ CLASSES[scn]) / 6.0)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "data")
    os.makedirs(out, exist_ok=True)
    rows = [run(s, t, seed) for s in SCENARIOS for t in TIERS
            for seed in SEEDS]
    with open(os.path.join(out, "detector_raw.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader(); w.writerows(rows)

    agg = {}
    for r in rows:
        agg.setdefault((r["scn"], r["tier"]), []).append(r)
    with open(os.path.join(out, "matrix.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["scn", "tier", "det_rate", "exact_set_rate", "subset_rate", "lat_mean",
                    "lat_sd", "fp_mean"])
        for (s, t), rs in sorted(agg.items()):
            det = statistics.mean(x["detected"] for x in rs)
            cor = statistics.mean(x["correct"] for x in rs)
            sub = statistics.mean(x["subset_correct"] for x in rs)
            lats = [x["latency"] for x in rs if x["latency"] != ""]
            lm = statistics.mean(lats) if lats else ""
            ls = statistics.stdev(lats) if len(lats) > 1 else 0.0
            fp = statistics.mean(x["false_alarms"] for x in rs)
            w.writerow([s, t, det, cor, sub, lm, ls, fp])

    # naive-comparator FP sensitivity to churn rate
    with open(os.path.join(out, "fp_sensitivity.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["churn", "tier", "fp_mean"])
        for churn in (0.1, 0.3, 0.5):
            for tier in ("T0", "T0n"):
                fps = [sum(1 for a, _ in alarm_stream("S0", tier, seed,
                                                      churn=churn) if a)
                       for seed in SEEDS]
                w.writerow([churn, tier, statistics.mean(fps)])

    # fully programmatic LaTeX matrix
    def cell(s, t):
        rs = agg[(s, t)]
        det = statistics.mean(x["detected"] for x in rs)
        cor = statistics.mean(x["correct"] for x in rs)
        if det == 0:
            return "\\xmark"
        subset = statistics.mean(x["subset_correct"] for x in rs)
        mark = "\\cmark" if cor == 1.0 else ("$\\triangle$" if subset == 1.0 else "$\\sim$")
        lats = [x["latency"] for x in rs if x["latency"] != ""]
        lm = statistics.mean(lats) if lats else 0.0
        if lm >= 0.05:
            mark += f"$^{{+{lm:.1f}}}$"
        return mark
    header = ("\\begin{tabular}{@{}lcccccc@{}}\n\\toprule\n"
              "\\textbf{Scenario} & \\textbf{T0} & \\textbf{T0n} & "
              "\\textbf{T1} & \\textbf{T2} & \\textbf{T3} & "
              "\\textbf{T4} \\\\\n"
              " & {\\scriptsize naive cfg} & {\\scriptsize norm.\\ cfg} & "
              "{\\scriptsize $+$policy} & {\\scriptsize $+$authz} & "
              "{\\scriptsize $+$lineage} & {\\scriptsize $+$env} \\\\\n"
              "\\midrule\n")
    lines = []
    for s in SCENARIOS:
        if s == "S0":
            fps = [f"{statistics.mean(x['false_alarms'] for x in agg[(s,t)]):.1f}"
                   for t in TIERS]
            lines.append("S0 control (churn only) & FP: " +
                         " & FP: ".join(fps) + " \\\\\n")
            continue
        lines.append(f"{s} & " + " & ".join(cell(s, t) for t in TIERS)
                     + " \\\\\n")
    with open(os.path.join(out, "table_matrix_full.tex"), "w") as f:
        f.write(header + "".join(lines) + "\\bottomrule\n\\end{tabular}\n")
    print("wrote detector_raw.csv, matrix.csv, fp_sensitivity.csv, "
          "table_matrix_full.tex")


if __name__ == "__main__":
    main()
