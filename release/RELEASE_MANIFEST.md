# Governance Drift v1.6.0 release manifest

- Version DOI: `10.5281/zenodo.21847543`
- Concept DOI: `10.5281/zenodo.21841458`
- Repository: `https://github.com/obedebessa/governance-drift`
- Tag: `v1.6.0`
- Scientific source-and-data commit: `fc6270e907b6bdf1cd9f22b5603a708ae6993816`

## Release assets

| File | SHA-256 |
|---|---|
| `governance-drift-v1.6.0-source.zip` | `93d1fe3285cc475bc392b3f75ddee8ad467d0e0daf4b049c22f28946b19f5e38` |
| `governance-drift-v1.6.0.pdf` | `db4e74d633ffa7244e687613f3772e5f7d4e18ba797c67c9b4d63eb1bdd92677` |
| `governance-drift-v1.6.0-anonymous-artifact.zip` | `018ba9cdd963f8f5e8ac95f29811258e4c18d0217b7565ea26698cfde474aa08` |

The source ZIP is produced with `git archive` from the source-and-data commit.
Repository export rules omit prior PDFs, local build/QA intermediates, and this
standalone release manifest. A clean extraction passed all 462 root-manifest
checks, the 133-file process-isolated trace verification, and the complete
97-test artifact verifier. The identified PDF is the visually inspected
31-page manuscript compiled from that source snapshot.

The anonymous artifact is a deterministic, text-only review package generated
from the same clean commit. Its internal verifier admitted 449 files with
payload-tree SHA-256
`5aad85e6a23ba93fc9bdc71360169aa0fe19fe5b8dc20dca5e9d149982c81cc1`.

Verify the downloadable assets locally with:

```bash
shasum -a 256 \
  governance-drift-v1.6.0-source.zip \
  governance-drift-v1.6.0.pdf \
  governance-drift-v1.6.0-anonymous-artifact.zip
```
