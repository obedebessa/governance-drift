# Third-party notices

The repository's MIT license applies to the original manuscript, code,
protocols, and data authored for this project. The following exact upstream
deployment manifests are preserved for reproducibility under their own
licenses:

- `lab/results_cross_stack/upstream/argocd-v3.4.2.yaml` — Argo CD v3.4.2
  `manifests/install.yaml`, SHA-256
  `69114b8c9eb48a1d08598e6f654a0869b10ae902456ea4b70796cb563760f5ec`,
  distributed by the Argo Project under the Apache License 2.0.
- `lab/results_cross_stack/upstream/gatekeeper-v3.22.2.yaml` — Gatekeeper
  v3.22.2 `deploy/gatekeeper.yaml`, SHA-256
  `72683f57fdfa4c34d4a892e5e6f457a5a7e533eba0293d781d53d08dd6614a5a`,
  distributed by the Open Policy Agent project under the Apache License 2.0.

The exact source URLs and hashes are machine-readable in
`lab/stacks/argocd-gatekeeper/upstream-lock.json`. The complete Apache License
2.0 text is included at `third_party/LICENSE-APACHE-2.0.txt`; the corresponding
tagged upstream copies are available at:

- <https://github.com/argoproj/argo-cd/blob/v3.4.2/LICENSE>
- <https://github.com/open-policy-agent/gatekeeper/blob/v3.22.2/LICENSE>

No change to those two preserved upstream manifest files is represented as
original project authorship.
