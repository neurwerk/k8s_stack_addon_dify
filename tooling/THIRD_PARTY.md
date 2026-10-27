# Third-party components

This MIT-licensed operational package uses shared `k8s-stack-tooling` (MIT), including its neutral catalog helpers, and its dependencies such as Kubernetes Python client (Apache-2.0), Pydantic (MIT), and PyYAML (MIT).
The image also includes CPython (PSF license), Debian components, and uv (MIT OR Apache-2.0); installed distributions and system packages retain their license notices.
Exact Python dependencies and source archives are recorded in `uv.lock`.

Dify and its provider SDK are not bundled in this image: the exported standalone payload runs in the separately maintained native API image.
