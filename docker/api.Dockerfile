# check=skip=InvalidDefaultArgInFrom
# Base image arguments intentionally require digest-pinned values from deploy.sh.
# =============================================================================
# SPDX-License-Identifier: MIT
# k8s-stack-addon-dify - Dify CE API + Keycloak OIDC auth
#
# This image extends the langgenius/dify-api image by adding a Keycloak OAuth
# provider that follows the same pattern as GitHub/Google OAuth.
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG BUILDER_REVISION
ARG DIFY_API_IMAGE_DIGEST
ARG DIFY_VERSION
ARG IMAGE_VERSION

LABEL org.opencontainers.image.title="Neurwerk Dify CE API overlay" \
  org.opencontainers.image.description="Dify CE API with the Neurwerk overlay" \
  org.opencontainers.image.source="https://github.com/neurwerk/k8s_stack_addon_dify" \
  org.opencontainers.image.url="https://github.com/neurwerk/k8s_stack_addon_dify" \
  org.opencontainers.image.version="${IMAGE_VERSION}" \
  org.opencontainers.image.revision="${BUILDER_REVISION}" \
  org.opencontainers.image.base.name="${BASE_IMAGE}" \
  com.neurwerk.dify.version="${DIFY_VERSION}" \
  com.neurwerk.dify.api-base.digest="${DIFY_API_IMAGE_DIGEST}"

USER root

# The upstream API image already contains PyJWT 2.13.0 and cryptography.
# Do not downgrade either dependency in the overlay.

# Patch the pinned upstream service instead of replacing its changed modules.
COPY customizations/api/neurwerk_sso.py /app/api/neurwerk_sso.py
COPY customizations/api/neurwerk_settings.py /app/api/neurwerk_settings.py
COPY customizations/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py /app/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py
COPY customizations/scripts/patch_dify.py /tmp/patch_dify.py
RUN python /tmp/patch_dify.py api /app/api && rm /tmp/patch_dify.py
COPY customizations/scripts/ /app/api/scripts/
COPY customizations/plugins/ /app/api/plugins-offline/
COPY LICENSE NOTICE-CHANGES.md THIRD_PARTY_NOTICES.md /licenses/neurwerk-k8s-stack-addon-dify/
COPY LICENSES/ /licenses/neurwerk-k8s-stack-addon-dify/LICENSES/

# Fix ownership
RUN chown -R dify:dify \
  /app/api/neurwerk_sso.py \
  /app/api/neurwerk_settings.py \
  /app/api/configs/app_config.py \
  /app/api/extensions/ext_application_services.py \
  /app/api/controllers/console/auth/oauth.py \
  /app/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py \
  /app/api/scripts/ \
  /app/api/plugins-offline/

# Create home directory for uv runtime cache ($HOME/.cache/uv)
RUN mkdir -p /home/dify && chown dify:dify /home/dify

# Switch back to non-root user
USER dify

EXPOSE 5001
ENTRYPOINT ["/entrypoint.sh"]
