# Installation status

This add-on is staged, **not installable**. No Dify instance is running.
The API and Web images are public and independently verified; the API, beat,
worker, and web chart defaults pin their exact versioned tags and OCI index
digests (see [README](../README.md#package-layout)). Base must first publish
compatible generic database and Keycloak access interfaces, remove its Dify
resource and certificate policy ownership, and provide a compatible published
managed-key bridge image. Tooling must support the access and verifier contracts.
The client must validate its source, values, safe database handoff, and Flux
dependencies before selecting the add-on. Do not select this source yet.

The package has these separate Flux paths:

| Path | Contents |
| --- | --- |
| `releases/namespaces/dify/` | Namespace |
| `releases/dify/secret-sync/` | OpenBao-backed runtime and OIDC secrets |
| `releases/dify/database/` | PostgreSQL role/databases and consumer network access |
| `releases/dify/access/` | Dify-owned Keycloak roles and access groups |
| `releases/dify/oidc/` | Keycloak OIDC releases and defaults |
| `releases/dify/managed-keys/` | Dify-owned bridge grants |
| `releases/dify/certificate-approval/` | Public certificate approval and use RBAC |
| `releases/dify/app/` | Dify component releases and defaults |

`releases/dify/` indexes only `app/`; do not reconcile both paths or overlap
their Flux inventories. The client owns the exact add-on source revision,
namespace-local values and stage dependencies. The client Flux graph must apply
the namespace and namespace-local SecretStores before secret-sync, and wait for
materialized Secrets before their consumers. It orders the database after
secrets and the shared PostgreSQL release, access after Keycloak realm roles,
OIDC after access, managed-key grants after OIDC, the bridge after grants and
verifier Secret delivery, and the application after database, OIDC, bridge and
certificate approval. Wait for current-generation Ready conditions; directory
order alone does not provide readiness. The secret stage requires the existing
operations namespace SecretStore and copied
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

## Certificate approval

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

Client Flux waits for the Base approver-policy and public issuer releases,
then applies this stage and verifies its HelmRelease Ready at the current
generation before the Dify web Gateway requests a certificate. The certificate
stage is independent of PostgreSQL; database ingress belongs to the database
stage. Other Dify workload egress and Keycloak configuration Job egress already
belong to their workload charts. Do not manually approve a denied request:
first verify policy readiness and request normal cert-manager renewal.

## Managed API keys

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
the bridge before starting Dify. The selected Base must provide a generic bridge
chart and a published compatible bridge image without owning Dify resources;
Tooling must manage the verifier credentials. Do not select the add-on until
these prerequisites and the other installation requirements above are met.

## Keycloak access

The access stage requires Base's `charts/keycloak/addon-access` and the existing
`auth-keycloak-secret` with `adminPassword` before its Job runs. It waits for
`keycloak-realm-roles`; the Job's configuration label and network policy allow
DNS and access to the Keycloak service after the server is ready. The selected
client must supply namespace-local `auth-keycloak/dify-keycloak-access-values`
(`values.yaml`) for access settings. The add-on HelmRelease pins verified Tooling
`0.7.3` with `KC_REALM_ROLE_COMPOSITE_OWNERSHIP` support; Base's realm-role Job
must use the same image. Do not use an older image or select this stage before
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
