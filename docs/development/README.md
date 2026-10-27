# Development

## Source layout

[DIFY_VERSION](../../DIFY_VERSION), [DIFY_SOURCE_REVISION](../../DIFY_SOURCE_REVISION), and [DIFY_SOURCE_SHA256](../../DIFY_SOURCE_SHA256) select upstream Web source. The root `*_IMAGE_DIGEST` files pin build inputs; deployed image references live in `charts/dify/*/values.yaml`. [NOTICE-CHANGES.md](../../NOTICE-CHANGES.md) and [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) describe the overlays and licensing.

[patch_dify.py](../../customizations/scripts/patch_dify.py) applies guarded replacements to upstream API configuration, OAuth construction/callback handling, and Web sign-in components. Each replacement requires exactly one source match, so an upstream change fails validation instead of silently omitting a hook. API behavior lives in [neurwerk_sso.py](../../customizations/api/neurwerk_sso.py) and [neurwerk_settings.py](../../customizations/api/neurwerk_settings.py); the Web component is [sso-redirect.tsx](../../customizations/web/app/signin/components/sso-redirect.tsx). Provider bootstrap runs inside the Dify API image, not as a standalone workstation application.

## Local checks

Use Python satisfying [pyproject.toml](../../pyproject.toml), Git, curl, tar, Make, and the tools selected by [.tool-versions](../../.tool-versions). Product tooling installs an exact shared Tooling release in one locked environment. Supply the exact selected Base checkout for composition checks:

```bash
mise exec -- make check BASE_REPO=/absolute/path/to/selected-base
```

The [Makefile](../../Makefile) runs add-on package validation, Ruff lint/format checks, ShellCheck, source verification, chart unit tests, strict Helm linting, and Helm/Kustomize rendering through kubeconform. Missing custom-resource schemas are explicitly ignored by kubeconform; rendering is not cluster admission testing.

[verify-sources.sh](../../scripts/verify-sources.sh) needs public network access to GitHub and Docker Hub. It checks the upstream tag/revision, downloads and verifies the source archive, exercises both patch sets in temporary source directories, checks base-image index digests, and verifies the bundled plugin checksum. It does not build images. The chart tests cover endpoint derivation, managed grants, adapter configuration, and network-policy alignment; this check target does not run upstream API integration tests or compile Web.

## Builds and publication

[deploy.sh](../../deploy.sh) is an interactive, operator-only API/Web image builder and publisher, despite its name. It requires a clean checkout at fetched canonical `origin/main`, pinned provenance, Docker/Buildx, and an immutable image version beginning with the selected upstream version. The Web Dockerfile builds upstream sources; there is no repository-local Web development server or full application dependency tree here.

API/Web builds and publication remain operator-only. The separate tooling image uses `make tooling-build VERSION=<version> REVISION=<commit>` with `tooling/` as its context. Follow the [shared image-release procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/image-releases.md#dify); publication, public package visibility, chart pins and client reconciliation are separate steps.

Release checks require the committed lockfile resolved against the published shared wheel and its hashes. Candidate checks may install a reviewed shared wheel into a temporary environment or the local test environment; they do not establish a release lock or installable image. Never commit local wheel paths in a lockfile.

The wheel sources are GitHub release `v0.14.0` for the runtime SDK and `stack-setup-v0.7.0` for setup integration tests. Their canonical URLs and archive hashes are recorded in `tooling/pyproject.toml` and `tooling/uv.lock`. The setup package is a development dependency, not part of the operational image. `make check BASE_REPO=...` checks browser and machine approvals and both images inside the enabled model-sync Job template against the selected Base.
