# Installation status

This add-on is staged, **not installable**. No Dify instance is running.
Base must first provide agreed generic interfaces for the database, roles,
approval, and managed-key stages. The new API and Web images must also be
published and verified; the current chart defaults retain legacy verified
image digests. Do not select this source in a client or pin unpublished images.

The package keeps four separate Flux paths:

| Path | Contents |
| --- | --- |
| `releases/namespaces/dify/` | Namespace |
| `releases/dify/secret-sync/` | OpenBao-backed runtime and OIDC secrets |
| `releases/dify/oidc/` | Keycloak OIDC releases and defaults |
| `releases/dify/app/` | Dify component releases and defaults |

`releases/dify/` indexes only `app/`; do not reconcile both paths or overlap
their Flux inventories. The client owns the exact add-on source revision,
namespace-local values and stage dependencies. Only after the missing Base
interfaces and images are ready can the client order namespace, secret-sync,
OIDC, and application stages with explicit readiness checks. Existing chart
names, HelmRelease names, and GitRepository source references stay unchanged.
