{{- define "keycloak-dify-agentgateway.effectiveRoles" -}}
{{- $catalogUpstreams := dict -}}
{{- $catalogNames := dict -}}
{{- range $entry := .Values.openrouterCatalog.models | default list -}}
{{- if hasKey $catalogUpstreams $entry.upstreamModel }}{{- fail (printf "OpenRouter upstream model %q is duplicated" $entry.upstreamModel) }}{{- end -}}
{{- if hasKey $catalogNames $entry.name }}{{- fail (printf "OpenRouter public model name %q is duplicated" $entry.name) }}{{- end -}}
{{- $_ := set $catalogUpstreams $entry.upstreamModel true -}}
{{- $_ := set $catalogNames $entry.name true -}}
{{- end -}}
{{- $excluded := .Values.openrouterCatalog.excludedModels | default list -}}
{{- if ne (len $excluded) (len ($excluded | uniq)) }}{{- fail "openrouterCatalog.excludedModels must not contain duplicates" }}{{- end -}}
{{- range $upstream := $excluded }}{{- if not (hasKey $catalogUpstreams $upstream) }}{{- fail (printf "openrouterCatalog.excludedModels contains unknown upstream model %q" $upstream) }}{{- end }}{{- end -}}
{{- $roles := .Values.authKeycloak.agentgatewayClientRoles | default list | uniq -}}
{{- if .Values.openrouterCatalog.enabled -}}
{{- range $entry := .Values.openrouterCatalog.models | default list -}}
{{- if not (has $entry.upstreamModel $excluded) }}{{- $roles = append $roles (printf "model:%s:invoke" $entry.name) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- $roles | uniq | toYaml -}}
{{- end -}}
