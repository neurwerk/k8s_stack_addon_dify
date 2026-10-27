# Operations

## Configuration and ownership

Use the selected revision's [release manifest](../../release/manifest.yaml) for exact Base, runtime Tooling, and standalone `stack-setup` prerequisites. It also lists the required ConfigMaps and their owners. Base supplies shared services and default identity settings; clients supply product selection, hostnames, roles, sizing, and the Flux graph. Tooling prepares the reviewed secret catalog and credentials through the [shared OpenBao procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/openbao.md).

Set `dify.enabled: true` and `dify.hostname` in client configuration, and select the corresponding stages. Other component settings remain under `frontendDify`. The [API HelmRelease](../../releases/dify/app/api.yaml) illustrates value precedence: `dify-defaults`, `frontend-dify-app-defaults`, `client-values`, then `dify-product-values`. Check each release's `valuesFrom` rather than assuming all stages consume the same inputs.

| Setting | Effect |
| --- | --- |
| `dify.hostname` | Shared public hostname for Web/API URLs, cookies, certificate approval, and OIDC callback. |
| `frontendDify.config` | Application defaults, including social login, registration, owner email, and workspace name. [app-defaults.yaml](../../releases/dify/secret-sync/app-defaults.yaml) enables SSO registration and disables ordinary registration, workspace creation, and email login. |
| `authKeycloak.difyClientId` | Browser OIDC client; the provisioning Job supplies the Dify audience and scoped realm roles. |
| `authKeycloak.difyAgentgatewayClientId` and `difyAgentgatewayClientRoles` | Separate model-call service principal and selected invocation/company-use grants. The manifest binds its settings to `auth-keycloak/keycloak-product-values` and trusted `dify-service-approval-values`. |
| `frontendDify.agentGateway.bindingId` | Existing AgentGateway binding using the default `/bindings/<id>` path; the adapter does not support a custom `apiPath`. |
| `frontendDify.defaultModel.name` and `contextSize` | Optional model provisioned by the API init container; its canonical name must match a granted model in the selected binding. |
| `frontendDify.api`, `webGui`, `worker`, `beat`, `redis`, `sandbox`, `pluginDaemon`, `agent` | Component resource, replica, or storage settings supported by the corresponding chart. |

Configure an intentional owner email before first bootstrap. The owner password is supplied from OpenBao, not client values. The login-form link does not enable password login: the packaged application defaults disable it, and the owner cannot sign in through Keycloak. Any recovery that enables a different login method needs an approved configuration change.

Clients supply browser scope approval in `dify-oidc-approval-values` and machine scope approval in `dify-service-approval-values`. The latter contains `serviceApproval` with `clientId`, `realmRoles`, `gatewayPermissions`, and `audiences`; model invocation permissions must cover the selected projection, and the audience is `agentgateway`. Approval is an upper bound, not permission to add unrelated roles. Both runtime provisioning and offline effective-image validation check the machine request against that approval.

## Installation and rollout

Clients reference the add-on as `dify-addon` and compose the packages below into their Flux graph. Paths are relative to this repository; dependencies are declared in the manifest.

| Package path | Required earlier add-on stages |
| --- | --- |
| `releases/namespaces/dify` | None |
| `releases/dify/secret-sync` | namespaces |
| `releases/dify/database` | secret-sync |
| `releases/dify/access` | secret-sync |
| `releases/dify/oidc` | access, secret-sync |
| `releases/dify/managed-keys` | oidc, secret-sync |
| `releases/dify/certificate-approval` | namespaces |
| `releases/dify/app` | database, oidc, managed-keys, certificate-approval |

Stage namespace, client values, and secret delivery before catalog reconciliation. Verify delivery before activating consumers. The database stage reuses Base's `charts/postgres/addon`; access reuses `charts/keycloak/addon-access`. OIDC configures both browser and model-call clients. Managed keys add the bridge grant ConfigMap and gateway ingress policy. Application HelmReleases add their own service dependencies, including PostgreSQL, Keycloak, AgentGateway, and extProc.

For an existing installation, follow [platform upgrade checks](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md) before adopting the selected source. The API migration hook runs on both install and upgrade. Preserve the database and API file/encryption state together; a chart revert does not undo migrations.

## Credential refresh and verification

For an approved refresh of existing credentials, run from the compatible Tooling checkout's `cli_tools/openbao_stack_setup/` directory with its dependencies and explicit target:

```bash
uv run --frozen stack-setup addon refresh dify \
  --context "<context>" --client "<client-id>" \
  --client-root "<client-checkout>" --source-cache "<verified-source-cache>" \
  --cluster-dir "<cluster-directory>"
```

Ensure the nonsecret shared template ConfigMap is staged before credential delivery. Verify all five ExternalSecrets are Ready; `frontend-dify-runtime-secret` now targets the final `frontend-dify-secret`, not a Helm-values Secret. Verify delivery by readiness and metadata rather than printing values. Preparation preserves valid credentials; refresh, restart and rotation are separate actions. The API init container consumes the managed key when provisioning model credentials.

For the ownership transition, use maintenance mode and stop consumers. Relinquish Helm ownership of `frontend-dify-secret` before ESO adopts it; recreate the runtime ExternalSecret for its changed target. Transfer the shared HelmRelease and defaults ConfigMaps from the app Flux inventory to secret-sync without old-inventory pruning. Canonical records must be prepared through the reviewed setup definition; no old-record copying or automatic cleanup is performed. Retire the old intermediate Secret only as an explicitly approved operator action.

After authorized deployment, perform the [shared Flux and event checks](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md#after-deployment), then check these Dify-specific boundaries:

- The migration Job completed, and `setup-model-provider` completed before API readiness. On init failure, inspect bounded logs for the failed database lock, tenant, encryption-key, plugin, or provider step.
- API `/health`, plugin daemon `/health/check`, and adapter loopback `/health` probes pass. Adapter health alone does not prove model invocation works.
- The public hostname reaches Web and the API callback; an admitted Keycloak user reaches the sole workspace with the intended editor/admin role. Existing unlinked email accounts are rejected rather than silently linked.
- An approved model request uses the selected binding and managed service-account permissions. Check application results and streaming, not only HelmRelease readiness.
- Existing application records, files, and required plugin state remain usable. Accept operator-confirmed feature tests rather than repeating them unnecessarily.

## Manual model synchronization

Use the Dify tooling package's `dify-model-sync project` command to derive the projection from reviewed catalog and metadata files. `dify-model-sync launch` requires an explicit Kubernetes context and the expected projection revision; preview is the default. Applying changes requires explicit `--mode apply` and a reviewed company-model cap. The command creates a one-shot native Job, never executes in application Pods, and reports uncertain API outcomes without retrying creation. Inspect the named Job before retrying an uncertain submission.

The native Job requires the existing sole workspace, matching tenant encryption material and installed provider plugin. It preserves unmanaged credentials and uses only authoritative model metadata. Staging and API images are independently pinned; the tooling stage receives no credentials or ServiceAccount token.

The release manifest declares both containers as optional `JobTemplate` consumers of `frontend-dify-model-sync/job-template.json`. When model sync is enabled, shared validation inspects the effective native and staging image references inside that template. Staged tooling tags without a published digest are not installable pins.

## Recovery and removal

A failed migration or inconsistent workspace needs an investigated repair or an approved restore plan. Bootstrap refuses to choose among multiple tenants; do not delete tenants or storage to get past initialization. Follow the [shared recovery boundaries](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md#recovery) for database/schema changes and the OpenBao guide for credential recovery.

The manifest declares retention for databases, volumes, and credentials. Disabling the product or removing workload stages is separate from deleting persistent data. Image replacement follows the [operator-only Dify publication procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/image-releases.md#dify), with API pins updated for API, worker, and beat, and Web pinned separately.
