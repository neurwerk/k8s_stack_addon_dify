# Architecture

## Upstream and repository-owned parts

| Part | Responsibility and source |
| --- | --- |
| API, worker, beat | Run the customized API image. [api.Dockerfile](../../docker/api.Dockerfile) extends the pinned upstream API image, adds Keycloak adapters, bootstrap helpers, a migration overlay, and the bundled model plugin. |
| Web | [web.Dockerfile](../../docker/web.Dockerfile) downloads verified upstream source and compiles the sign-in customization before building the production bundles. |
| Plugin daemon and gateway adapter | The upstream plugin daemon executes providers. The add-on's [loopback Nginx adapter](../../charts/dify/plugin-daemon/templates/gateway-adapter.yaml) translates the provider's identity header for AgentGateway. |
| Agent and code execution | [agent](../../charts/dify/agent) packages upstream agent backend, local sandbox, and Squid proxy images; [sandbox](../../charts/dify/sandbox) packages the separate Dify code sandbox. |
| Redis and PostgreSQL | The add-on deploys Redis. Its [database release](../../releases/dify/database/postgres.yaml) uses Base's PostgreSQL add-on chart for `dify`, `dify_plugin`, and pgvector-enabled `dify_vector`. |
| Deployment | [charts/dify](../../charts/dify) owns templates and image pins; [releases/dify](../../releases/dify) owns HelmRelease packages. Clients compose those packages with Base stages. |

The [Web HTTPRoute](../../charts/dify/web/templates/http-route.yaml) sends `/console/api`, `/api`, `/v1`, and `/files` to the API, and `/` to Web. The Traefik Gateway and certificate use `dify.hostname`; the external route is conditional on `externalGateway.enabled`.

## Browser identity and the single workspace

The [OIDC Job](../../charts/dify/oidc/templates/job.yaml) invokes Tooling's `upsert-scoped-oidc-client` with the `dify-user` and `dify-admin` realm-role scope. The callback is `https://<dify.hostname>/console/api/oauth/authorize/keycloak`. The Web overlay redirects ordinary social-login sign-ins to Keycloak while preserving the login-form option and upstream invitation/error handling.

[neurwerk_sso.py](../../customizations/api/neurwerk_sso.py) adds Keycloak to upstream's OAuth service. It checks browser-bound, expiring state and validates the access token's signature, issuer, audience, authorized client, subject, and verified email. `dify-user` is required for admission; admitted users become editors, or administrators when they also hold `dify-admin`. Identity links use the Keycloak subject, not automatic matching to an existing email account. Keycloak invitations and SSO for the configured break-glass owner are rejected.

The API requires exactly one workspace and complete OIDC settings with `ENFORCE_SINGLE_WORKSPACE` enabled. Membership is synchronized at login. A login without `dify-user` removes that linked account's memberships and revokes its token pair; this is not continuous synchronization of existing sessions when directory roles change.

## Managed model access

Browser identity is separate from gateway identity. The [service-account OIDC Job](../../charts/dify/oidc-agentgateway/templates/job.yaml) assigns `authKeycloak.difyAgentgatewayClientRoles` to `authKeycloak.difyAgentgatewayClientId`. The [managed-key chart](../../charts/dify/managed-keys/templates/grants.yaml) publishes primary and secondary grant definitions for that principal to the API Key Bridge. It requires `llm:invoke` and checks that selected permissions exist in the effective gateway role catalog. Model invocation and company-provider use require their corresponding grants.

OpenBao and External Secrets deliver the application key as `frontend-dify-openbao-secret/AGENTGATEWAY_API_KEY` and deliver verifier hashes separately to the bridge. The API's `setup-model-provider` init container uses [setup_model_provider.py](../../customizations/scripts/setup_model_provider.py) to install the bundled OpenAI-compatible plugin and upsert the selected default model with encrypted provider credentials. `frontendDify.defaultModel.name` must have a matching model grant; a selected model also requires `contextSize`.

The provider calls `http://127.0.0.1:8080/v1` inside the plugin-daemon Pod. The adapter accepts only `POST /v1/chat/completions`, moves its bearer credential to `X-Neurwerk-Authorization`, clears `Authorization`, `Proxy-Authorization`, and cookies, and forwards to `/bindings/<frontendDify.agentGateway.bindingId>/v1/chat/completions` on AgentGateway. Streaming is unbuffered. These requests identify the Dify service account, not the signed-in browser user. Gateway controls apply to this routed model path; the adapter does not implement other OpenAI endpoints.

## Startup and persistent state

The API [pre-install/pre-upgrade hook](../../charts/dify/api/templates/migration-job.yaml) runs database migrations before the Deployment starts. The provider init container serializes bootstrap with a PostgreSQL advisory lock, creates the owner/workspace only for an uninitialized installation, requires one tenant, ensures its encryption key pair, waits for the plugin daemon, and provisions configured model credentials. Failure exits nonzero and prevents API startup.

The [shared chart](../../charts/dify/shared/templates/secret.yaml) combines the OpenBao-backed Helm input into `frontend-dify-secret`, including database, Redis, sandbox, plugin, and agent credentials plus derived OIDC settings. API storage contains files and tenant encryption material; plugin and agent storage have separate volume definitions. Database backups alone do not preserve all application state. The release manifest retains databases, volumes, and credentials when removing workloads.
