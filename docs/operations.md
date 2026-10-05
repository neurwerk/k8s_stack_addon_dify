# Operations

The add-on is staged and not installable. No Dify instance is running; there
is no live Dify workload to inspect or migrate.

For local package checks, run `mise exec -- make check`. It verifies source
provenance, lint, chart templates and the separate namespace, secret-sync,
database, OIDC and application Kustomize packages without installing anything.

Before any future authorized installation, verify the agreed Base interfaces,
published and verified API/Web image digests, exact add-on Git revision,
client-owned values, secret delivery, and Flux dependency/readiness graph.
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
