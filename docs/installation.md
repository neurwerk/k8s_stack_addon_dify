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
## Managed API keys (draft; do not select)

The add-on owns `ConfigMap/dify-managed-key-grants` and the
`ExternalSecret/dify-managed-key-verifiers` in `auth-keycloak-api-key-bridge`.
The grant chart uses the same `authKeycloak.difyAgentgatewayClientRoles` and
effective OpenRouter catalog as the Dify service-account OIDC chart. The client
must supply `client-values`, `keycloak-product-values`, and (when selected)
`client-openrouter-catalog-values` in the bridge namespace for that HelmRelease.
It rejects duplicate, invalid, or ungranted permissions; the Dify API chart
separately requires every configured model's invoke permission.

The grant identities remain `dify-agentgateway-primary` and
`dify-agentgateway-secondary`, with service client `dify-agentgateway` by
default. The add-on's ExternalSecret copies only the existing primary and
secondary verifier properties from `auth-keycloak-api-key-bridge/internal`;
it does not generate, rotate, or print keys. An empty secondary verifier
disables that rotation entry. The existing Dify runtime Secret still receives
the raw key from `frontend-dify/internal` for its own namespace only.

In the client-owned bridge product values, configure the **generic** Base
`authKeycloakApiKeyBridge.managedRegistrations` list with these add-on entries
(alongside any other selected add-on registrations):

```yaml
authKeycloakApiKeyBridge:
  managedRegistrations:
    - grantConfigMap: dify-managed-key-grants
      grantKey: primary.json
      verifierSecret: dify-managed-key-verifiers
      verifierKey: difyAgentgatewayPrimaryVerifierSha256
    - grantConfigMap: dify-managed-key-grants
      grantKey: secondary.json
      verifierSecret: dify-managed-key-verifiers
      verifierKey: difyAgentgatewaySecondaryVerifierSha256
```

The bridge has no fixed Dify slot: each selected product contributes its own
entries and resources. The client Flux graph must wait for the add-on SecretStore,
ready ExternalSecret target, OIDC service-account reconciliation, and the
`dify-managed-keys` HelmRelease before reconciling the bridge; then wait for
the bridge before starting Dify. Do not enable this stage until the generic Base
bridge chart (#373), a published compatible bridge image (#26), Base Dify
ownership removal (#375), and Tooling verifier ownership (#99) are coordinated.
Those drafts do not authorize changing credentials, adopting a platform release,
or selecting this add-on.
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
