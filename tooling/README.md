# Dify add-on tooling

The `dify-tooling` distribution and image package `dify-validate`, `dify-model-sync project`, `dify-model-sync launch`, and `dify-stage-payloads`.
Neutral catalog/metadata/values, Job submission, bounded observations and resource staging come from the exact shared Tooling dependency.

Staging copies package resources to `/work/payloads` without importing native code; Dify's pinned API image executes the payload with native dependencies and scoped credentials.
Chart adoption requires published dependencies and anonymous verification of the exact public tooling image digest. A staged manifest is not installable.

From this directory, use `uv sync --locked --dev`, `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .`, `uv run --frozen ty check`, `uv run --frozen pytest`, and `uv build`.
The separately approved `tooling-vX.Y.Z` tag publishes `ghcr.io/neurwerk/k8s-stack-addon-dify-tooling:X.Y.Z`; it never builds Dify API or Web images.
