# Argo CD + Gatekeeper cross-stack protocol

This stack is an isolated, controlled Kind replication target. Argo CD owns
the desired/live configuration relation and Gatekeeper supplies a dry-run
background-audit policy relation. The S4 digest check deliberately remains a
shared artifact-lineage adapter; it is not represented as an independent
authorization validation by Argo CD or Gatekeeper. Intent and inventory are
not evaluated in this replication.

`upstream-lock.json` pins the official Argo CD v3.4.2 and Gatekeeper v3.22.2
installation manifests by URL and SHA-256. The runner verifies and preserves
those exact manifests, hashes every local manifest, uses only the isolated
`govdrift-cross` cluster, and deletes only that cluster after result capture.
The in-cluster source is a reproducible bare repository built by the pinned
`alpine/git` init container. It is served read-only by `git daemon` from the
same pinned Argo CD v3.4.2 image installed by the verified upstream manifest;
no additional unpinned source-server image or external repository is used.

The Gatekeeper constraint uses `enforcementAction: dryrun`. Gatekeeper's
constraint-status schema does not provide a structural UID field, so the
controlled Rego rule includes `input.review.object.metadata.uid` in its
violation message. The adapter accepts a polar policy verdict only when that
engine-emitted UID equals the live Deployment UID; absence or mismatch is
`undecidable`. The raw trace retains the structural fields, embedded UID, live
UID, audit freshness, and join source. This validates one object lifetime; it
does not claim continuation-safe identity across recreation.
