# Dify

This repository packages optional Dify deployments for Neurwerk Base. It owns Helm charts, staged Flux releases, and API/Web overlays on pinned upstream Dify sources. It is not a full Dify source checkout.

- [Architecture](architecture/README.md): components, browser identity, managed model access, and persistent state.
- [Development](development/README.md): source pins, customization entry points, and local checks.
- [Operations](operations/README.md): configuration, installation order, verification, and recovery boundaries.
- [Release manifest](../release/manifest.yaml): authoritative prerequisites, package dependencies, and retention.

Base supplies shared identity, gateway, database, storage, and secret-delivery infrastructure. Client repositories select the add-on source, supply values, and own the Flux dependency graph. [Tooling](https://github.com/neurwerk/k8s_stack_tooling) owns setup and credential automation; this repository invokes its packaged commands. See the [shared add-on conventions](https://github.com/neurwerk/docs/blob/main/dev/conventions/addons.md) for these boundaries.
