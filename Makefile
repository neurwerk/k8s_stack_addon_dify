SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
ADDON_TOOLING ?= $(WORKSPACE_ROOT)/tooling

.PHONY: check
check:
	uv run --frozen --project "$(ADDON_TOOLING)" addon-check "$(CURDIR)"
	uv run --frozen ruff check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts tests/charts charts/dify/model-sync/files
	uv run --frozen ruff format --check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts tests/charts charts/dify/model-sync/files
	shellcheck deploy.sh scripts/verify-sources.sh
	./scripts/verify-sources.sh
	uv run --frozen python -m unittest discover -s tests/charts -p 'test_*.py'
	@for chart in charts/dify/*; do \
		helm lint --strict "$$chart" --values tests/validation/helm-lint-values.yaml; \
		helm template test "$$chart" --values tests/validation/helm-lint-values.yaml | kubeconform -strict -summary -ignore-missing-schemas; \
	done
	@for package in releases/namespaces/dify releases/dify/secret-sync releases/dify/database releases/dify/access releases/dify/oidc releases/dify/managed-keys releases/dify/certificate-approval releases/dify/app; do \
		kustomize build --load-restrictor LoadRestrictionsNone "$$package" | kubeconform -strict -summary -ignore-missing-schemas; \
	done
