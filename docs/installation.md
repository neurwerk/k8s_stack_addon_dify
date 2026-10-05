# Installation status

This add-on is staged, **not installable**. No Dify instance is running.
Base must first provide agreed generic interfaces for the database, roles,
and managed-key stages. The new API and Web images must also be
published and verified; the current chart defaults retain legacy verified
image digests. Do not select this source in a client or pin unpublished images.

The package keeps separate Flux paths:

| Path | Contents |
| --- | --- |
| `releases/namespaces/dify/` | Namespace |
| `releases/dify/secret-sync/` | OpenBao-backed runtime and OIDC secrets |
| `releases/dify/oidc/` | Keycloak OIDC releases and defaults |
| `releases/dify/certificate-approval/` | Dify public certificate approval and use RBAC |
| `releases/dify/app/` | Dify component releases and defaults |

`releases/dify/` indexes only `app/`; do not reconcile both paths or overlap
their Flux inventories. The client owns the exact add-on source revision,
namespace-local values and stage dependencies. Only after the missing Base
interfaces and images are ready can the client order namespace, secret-sync,
OIDC, certificate approval, and application stages with explicit readiness
checks. Existing chart names, HelmRelease names, and GitRepository source
references stay unchanged.

The certificate stage installs in `infra-cert-manager` after the Base
approver-policy controller and CRD. It uses the existing `client-values`
ConfigMap in that namespace: `dify.enabled: true` and `dify.hostname` select
the exact public hostname; `frontendDify.hostname` must match the Gateway's
existing value. Disabled creates no policy or RBAC. The two policies keep the
old exact names and restrict issuance to `frontend-dify`, the selected hostname,
the staging or production ClusterIssuer, a 90-day RSA-2048 key, and non-CA
server usages. Only the cert-manager controller receives `use` permission.
Base cleanup must first stop owning these policy names; the add-on does not
install cert-manager, approver-policy, issuers, Gateway, or TLS Secret.

Client Flux must wait for the Base approver-policy and public issuer releases,
then apply this stage and verify its HelmRelease Ready at the current generation
before the Dify web Gateway starts requesting a certificate. The certificate
stage is independent of PostgreSQL; its database ingress rules belong to the
separate database stage. Other Dify workload egress and Keycloak configuration
Job egress are already owned by their workload charts; no additional
database-independent network grant is needed. Keep all stages unselected until
Base cleanup and a compatible signed release, client graph, and image pins are
ready. Do not manually approve a denied request: first verify policy readiness
and then request normal cert-manager renewal.
