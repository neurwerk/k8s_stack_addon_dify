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
        fsGroup: 65532
        seccompProfile: {type: RuntimeDefault}
      affinity:
        podAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app.kubernetes.io/name: api
                  app.kubernetes.io/instance: frontend-dify-api
              topologyKey: kubernetes.io/hostname
      initContainers:
        - name: export-model-sync
          image: {{ .Values.frontendDify.modelSync.toolingImage | quote }}
          command: [dify-stage-payloads]
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            runAsGroup: 65532
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
            seccompProfile: {type: RuntimeDefault}
          resources:
            requests: {cpu: 10m, memory: 32Mi}
            limits: {cpu: 100m, memory: 128Mi}
          volumeMounts:
            - {name: payload, mountPath: /work/payloads}
      containers:
        - name: model-sync
          image: {{ .Values.frontendDify.apiImage | quote }}
          imagePullPolicy: {{ .Values.frontendDify.pullPolicy }}
          workingDir: /app/api
          command: [python, -B, /work/payloads/sync_models.py]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          resources:
            {{- toYaml .Values.frontendDify.modelSync.resources | nindent 12 }}
          env:
            # Launcher must explicitly replace all three values in its snapshot.
            - {name: DIFY_MODEL_SYNC_MODE, value: ""}
            - {name: EXPECTED_PROJECTION_REVISION, value: ""}
            - {name: MAX_GLOBAL_MODELS, value: ""}
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
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: DB_PASSWORD}
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: REDIS_PASSWORD}
            - name: SECRET_KEY
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: SECRET_KEY}
            - name: PLUGIN_DAEMON_KEY
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: PLUGIN_DAEMON_KEY}
            - name: INNER_API_KEY_FOR_PLUGIN
              valueFrom:
                secretKeyRef: {name: frontend-dify-secret, key: PLUGIN_DAEMON_KEY}
            - name: LLM_PROXY_API_KEY
              valueFrom:
                secretKeyRef: {name: frontend-dify-openbao-secret, key: AGENTGATEWAY_API_KEY}
          volumeMounts:
            - {name: payload, mountPath: /work/payloads, readOnly: true}
            - {name: support, mountPath: /etc/dify-model-sync, readOnly: true}
            - {name: storage, mountPath: /app/api/storage, subPath: storage, readOnly: true}
      volumes:
        - name: payload
          emptyDir: {}
        - name: support
          configMap: {name: frontend-dify-model-sync}
        - name: storage
          persistentVolumeClaim: {claimName: frontend-dify-api-pvc, readOnly: true}
{{- end -}}
