{{- define "dify-model-sync.job" -}}
apiVersion: batch/v1
kind: Job
metadata:
  generateName: frontend-dify-model-sync-
  namespace: {{ .Release.Namespace }}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 7200
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        app.kubernetes.io/name: model-sync
        app.kubernetes.io/instance: frontend-dify-model-sync
    spec:
      serviceAccountName: frontend-dify-model-sync
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        runAsGroup: 1001
        seccompProfile: {type: RuntimeDefault}
      affinity:
        podAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app.kubernetes.io/name: api
                  app.kubernetes.io/instance: frontend-dify-api
              topologyKey: kubernetes.io/hostname
      containers:
        - name: model-sync
          image: {{ .Values.frontendDify.apiImage | quote }}
          imagePullPolicy: {{ .Values.frontendDify.pullPolicy }}
          workingDir: /app/api
          command: [python, -B, /etc/dify-model-sync/sync_models.py]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          resources:
            {{- toYaml .Values.frontendDify.modelSync.resources | nindent 12 }}
          env:
            # Launcher must explicitly replace all three values in its snapshot.
            - {name: DIFY_MODEL_SYNC_MODE, value: ""}
            - {name: EXPECTED_PROJECTION_REVISION, value: ""}
            - {name: MAX_COMPANY_MODELS, value: ""}
            - {name: PYTHONPATH, value: /app/api}
            - {name: MIGRATION_ENABLED, value: "false"}
            - {name: DB_HOST, value: {{ .Values.frontendDify.postgres.host | quote }}}
            - {name: DB_PORT, value: {{ .Values.frontendDify.postgres.servicePort | quote }}}
            - {name: DB_USERNAME, value: {{ .Values.frontendDify.postgres.user | quote }}}
            - {name: DB_DATABASE, value: {{ .Values.frontendDify.postgres.database | quote }}}
            - {name: PGSSLMODE, value: disable}
            - {name: PGCONNECT_TIMEOUT, value: "10"}
            - {name: REDIS_HOST, value: frontend-dify-redis-service}
            - {name: REDIS_PORT, value: "6379"}
            - {name: STORAGE_TYPE, value: {{ .Values.frontendDify.config.STORAGE_TYPE | quote }}}
            - {name: STORAGE_LOCAL_PATH, value: {{ .Values.frontendDify.config.STORAGE_LOCAL_PATH | quote }}}
            - {name: PLUGIN_DAEMON_URL, value: {{ .Values.frontendDify.config.PLUGIN_DAEMON_URL | quote }}}
            - {name: AGENT_BACKEND_BASE_URL, value: "http://frontend-dify-agent-backend-service:5050"}
            - name: INNER_API_KEY_FOR_PLUGIN
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: PLUGIN_DAEMON_KEY}
            - name: AGENT_BACKEND_API_TOKEN
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: DIFY_AGENT_API_TOKEN}
            - name: KEYCLOAK_OIDC_CLIENT_SECRET
              valueFrom:
                secretKeyRef: {name: frontend-dify-openbao-secret, key: KEYCLOAK_OIDC_CLIENT_SECRET}
            - name: LLM_PROXY_API_KEY
              valueFrom:
                secretKeyRef: {name: frontend-dify-openbao-secret, key: AGENTGATEWAY_API_KEY}
          envFrom:
            - secretRef: {name: frontend-dify-secret}
          volumeMounts:
            - {name: support, mountPath: /etc/dify-model-sync, readOnly: true}
            - {name: storage, mountPath: /app/api/storage, subPath: storage, readOnly: true}
      volumes:
        - name: support
          configMap: {name: frontend-dify-model-sync}
        - name: storage
          persistentVolumeClaim: {claimName: frontend-dify-api-pvc, readOnly: true}
{{- end -}}
