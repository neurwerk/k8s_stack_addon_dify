# neurwerk.base - Dify Add-on

The Dify add-on provides an optional Dify package for neurwerk.base, with API and web customizations including single-workspace enforcement. See the [neurwerk.base website](https://base.neurwerk.com/) for more information.

| Repository | Description |
| --- | --- |
| [Base chart](https://github.com/neurwerk/k8s_stack_base) | Shared platform charts and release packages that form the foundation of the stack. |
| [Studio](https://github.com/neurwerk/k8s_stack_studio) | Web dashboard and API for operating AI platform services. |
| [Tooling](https://github.com/neurwerk/k8s_stack_tooling) | One container image plus separate CLI tools for setup and operations. |
| [PII Engine](https://github.com/neurwerk/k8s_stack_pii_engine) | Service that uses Presidio to evaluate PII and apply safety policies. |
| [AgentGateway External Processor](https://github.com/neurwerk/k8s_stack_agentgateway_extproc) | Adapter that processes gateway requests and responses with the PII Engine. |
| [Keycloak API Key Bridge](https://github.com/neurwerk/k8s_stack_keycloak_api_key_bridge) | Separate service that issues and validates API keys using Keycloak permissions. |
| [Keycloak Theme](https://github.com/neurwerk/k8s_stack_keycloak_theme) | Customized Keycloak login pages and emails. |
|  |  |
| [Example client chart](https://github.com/neurwerk/k8s_stack_client_example_com) | Reference client configuration and Flux deployment setup to adapt for a new client. |
|  |  |
| [Dify Add-on](https://github.com/neurwerk/k8s_stack_addon_dify) | Optional Dify package with API and web customizations, including single-workspace enforcement (**this repo**). |

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
