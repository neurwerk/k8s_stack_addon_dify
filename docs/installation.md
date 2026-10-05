# Installation status

This add-on is staged, **not installable**. No Dify instance is running.
Base must first publish the generic database chart, remove its hardcoded Dify
provisioning, and provide agreed roles, approval, and managed-key interfaces.
The new API and Web images must also be published and verified; the chart
defaults retain legacy verified image digests. Do not select this source in a
client or pin unpublished images.

The package keeps five separate Flux paths:

| Path | Contents |
| --- | --- |
| `releases/namespaces/dify/` | Namespace |
| `releases/dify/secret-sync/` | OpenBao-backed runtime and OIDC secrets |
| `releases/dify/database/` | PostgreSQL role/databases and consumer network access |
| `releases/dify/oidc/` | Keycloak OIDC releases and defaults |
| `releases/dify/app/` | Dify component releases and defaults |

`releases/dify/` indexes only `app/`; do not reconcile both paths or overlap
their Flux inventories. The client owns the exact add-on source revision,
namespace-local values and stage dependencies. Only after the missing Base
interfaces and images are ready can the client order namespace, secret-sync,
database, OIDC, and application stages with explicit readiness checks. The secret
stage requires the existing operations namespace SecretStore and copied
`infra-postgres-operations/internal:difyPassword` from
`frontend-dify/internal:postgresPassword`; it must not wait for database readiness.
The database stage requires the namespace-local `frontend-dify-postgres-password`
Secret (key `password`) and a ready `postgres-operations` HelmRelease. Wait for
the `frontend-dify-postgres` HelmRelease to be Ready before application startup;
the API and plugin daemon also declare direct HelmRelease dependencies. Do not
select this stage while Base still provisions the Dify role and databases: the
generic chart rejects existing objects without its ownership marker. First
check actual database state and backup coverage and agree a safe handoff; do not
drop or assume empty databases. Existing chart names, HelmRelease names, and
GitRepository source references stay unchanged.
