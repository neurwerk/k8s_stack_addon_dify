# Operations

The add-on is staged and not installable. No Dify instance is running; there
is no live Dify workload to inspect or migrate.

For local package checks, run `mise exec -- make check`. It verifies source
provenance, lint, chart templates and the separate namespace, secret-sync,
database, access, OIDC, managed-key, certificate-approval and application
Kustomize packages without installing anything.

The API and Web images are publicly published and independently verified at
their chart pins; see the [README](../README.md#package-layout) for digests and
source revision. Before any future authorized installation, verify the agreed
Base and Tooling interfaces, a compatible published bridge image, exact add-on
Git revision, client-owned values, safe database handoff, secret delivery, and
Flux dependency/readiness graph.
After an authorized rollout, inspect the source revision, Kustomizations,
HelmReleases, warning events, application login and model access, and
persistent state. Do not print Secret values or treat a ready Pod as proof that
Dify works. Image builds, publication, source selection, and deployment need
separate operator approval.

## PostgreSQL password rotation

The Dify provisioner reads `frontend-dify-postgres-password:password` directly
in `infra-postgres-operations`; the Dify runtime receives the matching password
through its own OpenBao-backed Secret in `frontend-dify`. A Secret change alone
does **not** rerun the provisioner's Helm post-upgrade Job. In an authorized
maintenance window, coordinate the OpenBao password update and ExternalSecret
delivery to both namespaces, confirming both targets are ready without reading
their values. Then force one install/upgrade of the provisioner:

```bash
flux reconcile helmrelease frontend-dify-postgres -n infra-postgres-operations --force
```

Wait for the provisioning Job and HelmRelease to become Ready, then verify Dify
database connections. Expect a brief outage while the consumer and database
passwords converge. Do not reset the shared PostgreSQL instance or its data.
