# neurwerk.base - Dify Add-on

The Dify add-on provides an optional Dify package for neurwerk.base, with API and web customizations including single-workspace enforcement. See the [neurwerk.base website](https://base.neurwerk.com/) for more information.

## Stack

| Repository | Purpose |
| --- | --- |
| [Base](https://github.com/neurwerk/k8s_stack_base) | Shared platform charts and release packages. |
| [Studio](https://github.com/neurwerk/k8s_stack_studio) | Platform dashboard and API. |
| [Tooling](https://github.com/neurwerk/k8s_stack_tooling) | Setup automation, operational tools, and operations image. |
| [PII Engine](https://github.com/neurwerk/k8s_stack_pii_engine) | Personal-information detection and safety policies. |
| [Gateway Processor](https://github.com/neurwerk/k8s_stack_agentgateway_extproc) | Connects gateway traffic to the PII Engine. |
| [API Key Bridge](https://github.com/neurwerk/k8s_stack_keycloak_api_key_bridge) | Issues and validates API keys using Keycloak permissions. |
| [Keycloak Theme](https://github.com/neurwerk/k8s_stack_keycloak_theme) | Keycloak login and email themes. |

## Add-ons

Optional products are packaged separately from Base. See each repository's documentation and release manifest for availability and prerequisites.

| Repository | Purpose |
| --- | --- |
| [Dify](https://github.com/neurwerk/k8s_stack_addon_dify) | Dify deployment and API/web customizations. **(this repository)** |
| [LibreChat](https://github.com/neurwerk/k8s_stack_addon_librechat) | LibreChat deployment and platform integration. |
| [Open WebUI](https://github.com/neurwerk/k8s_stack_addon_open_webui) | Open WebUI deployment and platform integration. |
| [Pipecat](https://github.com/neurwerk/k8s_stack_addon_pipecat) | Self-hosted voice bots and browser UI with platform identity and AI gateway integration. |

## Examples

| Repository | Purpose |
| --- | --- |
| [Example Client](https://github.com/neurwerk/k8s_stack_client_example_com) | Example client settings and Flux deployment setup. |

## Documentation

See [Repository documentation](docs/README.md).

## Contributing and support

- **Contributions:** Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) before proposing a change.
- **Bug reports and feature requests:** Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_addon_dify/issues) for reproducible bugs and clearly scoped feature requests.

## Security

Report vulnerabilities privately by following the instructions in [SECURITY.md](SECURITY.md).

## Licensing

Original build tools, tests, and documentation use the [MIT License](LICENSE).

Dify itself, including our changes to its code, uses the [Dify Open Source License](LICENSES/Dify-LICENSE). It is based on Apache 2.0, with extra rules about running multiple workspaces and changing Dify's logo or copyright notices in its web interface. Check Dify's license for the permissions you need.

The bundled OpenAI-compatible plugin uses the [Apache License 2.0](LICENSES/Apache-2.0.txt).

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party sources and licensing information.
