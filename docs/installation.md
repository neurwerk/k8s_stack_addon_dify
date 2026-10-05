# Installation status

This add-on is staged, **not installable**. No Dify instance is running.
Base must first provide agreed generic interfaces for the database, access,
approval, and managed-key stages. The new API and Web images must also be
published and verified; the current chart defaults retain legacy verified
image digests. Do not select this source in a client or pin unpublished images.

The package keeps five separate Flux paths:

| Path | Contents |
| --- | --- |
| `releases/namespaces/dify/` | Namespace |
| `releases/dify/secret-sync/` | OpenBao-backed runtime and OIDC secrets |
| `releases/dify/access/` | Dify-owned Keycloak roles and access groups |
| `releases/dify/oidc/` | Keycloak OIDC releases and defaults |
| `releases/dify/app/` | Dify component releases and defaults |

`releases/dify/` indexes only `app/`; do not reconcile both paths or overlap
their Flux inventories. The client owns the exact add-on source revision,
namespace-local values and stage dependencies. Only after the missing Base
interfaces and images are ready can the client order namespace, secret-sync,
access, OIDC, and application stages with explicit readiness checks. Existing
chart names, HelmRelease names, and GitRepository source references stay unchanged.

The access stage requires Base's `charts/keycloak/addon-access` and the existing
`auth-keycloak-secret` with `adminPassword` before its Job runs. It waits for
`keycloak-realm-roles`; the Job's configuration label and network policy allow
DNS and access to the Keycloak service after the server is ready. The selected
client must supply namespace-local `auth-keycloak/dify-keycloak-access-values`
(`values.yaml`) with `k8sTools.image` set to a published, verified image that
supports `KC_REALM_ROLE_COMPOSITE_OWNERSHIP`. That same image must be pinned in
Base's realm-role Job. Do not use an older image or select this stage before
Base removes its Dify role and group ownership; otherwise concurrent reconcilers
can undo one another's grants. Preserve the existing `dify-user`, `dify-admin`,
`/access/neurwerk-dify-users`, and `/access/neurwerk-dify-admins` identities.
`addonAccess.platformAdminGrant` defaults to `true`; a client may set only this
grant to `false` in the access values to remove Dify's own direct `dify-admin`
grant from `platform-admin` without changing other products' grants. Model and
MCP permissions remain separate. Client Flux stages must wait for the access
HelmRelease to become Ready before OIDC, then wait for OIDC before the app.
Neither removing the stage nor changing the grant removes existing group
memberships or already issued tokens.
