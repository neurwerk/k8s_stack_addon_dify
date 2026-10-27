SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
TOOLING := uv run --locked --project tooling
TOOLING_IMAGE := ghcr.io/neurwerk/k8s-stack-addon-dify-tooling

.PHONY: check tooling-check tooling-build
check: tooling-check
	@test -n "$(BASE_REPO)" || { echo 'Set BASE_REPO to the exact selected Base checkout'; exit 1; }
	$(TOOLING) addon-check "$(CURDIR)" --base "$(BASE_REPO)"
	uv run --frozen ruff check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts tests/charts
	uv run --frozen ruff format --check customizations/api/neurwerk_sso.py customizations/api/neurwerk_settings.py customizations/scripts scripts tests/charts
	shellcheck deploy.sh scripts/verify-sources.sh
	./scripts/verify-sources.sh
	uv run --frozen python -m unittest discover -s tests/charts -p 'test_*.py'
	@for chart in charts/dify/*; do \
		helm lint --strict "$$chart" --values tests/validation/helm-lint-values.yaml; \
		helm template test "$$chart" --values tests/validation/helm-lint-values.yaml | kubeconform -strict -summary -ignore-missing-schemas; \
	done
	@for package in $$($(TOOLING) dify-validate "$(CURDIR)" --list-packages); do \
		kustomize build --load-restrictor LoadRestrictionsNone "$$package" | kubeconform -strict -summary -ignore-missing-schemas; \
	done

tooling-check:
	$(TOOLING) ruff check --config tooling/pyproject.toml tooling
	$(TOOLING) ruff format --check --config tooling/pyproject.toml tooling
	$(TOOLING) ty check --project tooling
	BASE_REPO="$(BASE_REPO)" $(TOOLING) pytest tooling/tests

tooling-build:
	@test -n "$(VERSION)" -a -n "$(REVISION)" || { echo 'Set VERSION and REVISION'; exit 1; }
	docker build --build-arg VERSION="$(VERSION)" --build-arg REVISION="$(REVISION)" -t "$(TOOLING_IMAGE):$(VERSION)" tooling
