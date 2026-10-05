SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: check
check:
	uv run --frozen ruff check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts
	uv run --frozen ruff format --check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts
	shellcheck deploy.sh scripts/verify-sources.sh
	./scripts/verify-sources.sh
	@for chart in charts/dify/*; do \
		helm lint --strict "$$chart" --values tests/validation/helm-lint-values.yaml; \
		helm template test "$$chart" --values tests/validation/helm-lint-values.yaml | kubeconform -strict -summary -ignore-missing-schemas; \
	done
	@for package in releases/namespaces/dify releases/dify/app releases/dify/oidc releases/dify/managed-keys releases/dify/secret-sync; do \
		kustomize build --load-restrictor LoadRestrictionsNone "$$package" | kubeconform -strict -summary -ignore-missing-schemas; \
	done
