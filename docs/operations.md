# Operations

The add-on is staged and not installable. No Dify instance is running; there
is no live Dify workload to inspect or migrate.

For local package checks, run `mise exec -- make check`. It verifies source
provenance, lint, chart templates and the separate namespace, secret-sync,
OIDC and application Kustomize packages without installing anything.

Before any future authorized installation, verify the agreed Base interfaces,
published and verified API/Web image digests, exact add-on Git revision,
client-owned values, secret delivery, and Flux dependency/readiness graph.
After an authorized rollout, inspect the source revision, Kustomizations,
HelmReleases, warning events, application login and model access, and
persistent state. Do not print Secret values or treat a ready Pod as proof that
Dify works. Image builds, publication, source selection, and deployment need
separate operator approval.
