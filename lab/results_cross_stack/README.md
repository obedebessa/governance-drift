# Argo CD + Gatekeeper cross-stack replication

This directory preserves an executed, bounded replication on a separate Kind
cluster named `govdrift-cross`. The official Argo CD v3.4.2 and Gatekeeper
v3.22.2 manifests were downloaded from their tagged upstream repositories,
verified against the pinned SHA-256 values, and preserved under `upstream/`.

The campaign ran five repetitions each of S1, S3, and S4. It produced
15/15 exact projected singleton classifications over the declared
evaluated components. S1 used Argo CD's native
desired/live status; S3 used a fresh Gatekeeper background-audit violation
with `enforcementAction: dryrun`. Gatekeeper's violation record did not emit a
structural resource-UID field, so the controlled Rego rule embeds the
evaluated object's UID in the violation message. The adapter accepts a polar
policy result only when that engine-emitted UID equals the live Deployment
UID; absence or mismatch is undecidable.

Every scenario began after an explicit Argo sync to the validated Git revision.
All 15/15 restoration operations reached `Succeeded`, and a
leaf-by-leaf check of the pinned desired manifest found zero residual differences
after restoration. Instrumented Kubernetes reads recorded zero API errors, and
all 15 final observations had zero undecidable evaluated components.

S4 is deliberately labeled `shared-artifact-adapter`: the running image digest
was outside the approved digest set, but this is not independent authorization
validation by Argo CD or Gatekeeper. Intent and environment/inventory were not
evaluated in this replication.

The S3 subject link covers one object lifetime in this controlled campaign;
it does not claim continuation-safe identity across resource recreation.

The comparison with the primary Flux + Kyverno laboratory is descriptive. It
tests bounded realizability of corresponding evidence paths; it is not an
equivalence, non-inferiority, prevalence, reliability, or production-latency
study. First-honest DDL is time from operational onset to the first honest
non-consistent or undecidable verdict, so it may be epistemic rather than a
substantive class detection. First-substantive latency is reported separately.
ESC ends at the first exact projected classification over the declared
evaluated components; it is not watermark-qualified Stable-VCL.

The raw trace preserves injection/onset and baseline reference markers plus
evaluation-start, evaluation-completion, duration, and completion-classification
fields for every poll. The analyzer reconstructs DDL, first epistemic alert,
first substantive alert, and exact-classification latency from those fields.

The resource stop rule was 80% sustained for three five-second samples on host
CPU, host memory-pressure utilization, normalized Kind-node CPU, or Kind-node
memory. It did not trigger. Installation also had a strict 15-minute Ready
deadline. `cleanup.json` verifies that only `govdrift-cross` was deleted after
capture.

- `cross_stack_raw.ndjson`: every baseline and scenario poll.
- `cross_stack_observations.json` / `.csv`: one result per repetition.
- `install_events.ndjson`: manifest, readiness, and setup provenance.
- `resource_samples.ndjson`: stop-rule inputs.
- `manifest_checksums.csv`: upstream, local-stack, and campaign-source SHA-256 inventory.
- `platform.json`: exact platform, source state, images, clock, and validation boundary.
- `cross_stack_summary.json`: validated descriptive summary.
- `table_cross_stack.tex`: manuscript-ready table.
- `cleanup.json`: deletion command plus pre/post verification stdout, stderr,
  return codes, parsed cluster sets, exact target, and postcondition.
