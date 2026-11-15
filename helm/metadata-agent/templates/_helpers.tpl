{{- /* "metadata-agent", not "metadata-agent-metadata-agent", for a release of that name. */}}
{{- define "metadata-agent.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- /* The Service of the internal port; cut so the suffix fits into 63 characters. */}}
{{- define "metadata-agent.internalName" -}}
{{- printf "%s-internal" (include "metadata-agent.fullname" . | trunc 54 | trimSuffix "-") -}}
{{- end -}}

{{- define "metadata-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "metadata-agent.labels" -}}
{{ include "metadata-agent.selectorLabels" . }}
{{- /* The version the chart deploys, not image.tag: a tag need not be a
       valid label value, and with image.digest it names nothing. */}}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "metadata-agent.secretName" -}}
{{- .Values.secret.existingSecret | default (include "metadata-agent.fullname" .) -}}
{{- end -}}

{{- /* An env value as text. helm reads a YAML number as float64: 41943040
       without quotes was rendered as "4.194304e+07" and the service did not
       start. Whole numbers are written as integers. */ -}}
{{- define "metadata-agent.envValue" -}}
{{- if and (kindIs "float64" .) (eq (floor .) .) -}}
{{- int64 . -}}
{{- else -}}
{{- . -}}
{{- end -}}
{{- end -}}

{{- /* repository:tag, or repository@digest where image.digest pins the image. */ -}}
{{- define "metadata-agent.image" -}}
{{- $repository := required "image.repository setzen (Registry des Images)" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" (toString .Values.image.digest)) -}}
{{- fail (printf "image.digest %q: expected sha256: and 64 hex digits" (toString .Values.image.digest)) -}}
{{- end -}}
{{- printf "%s@%s" $repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" $repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{- /* Values that render, and then fail in the cluster or mean something else
       there. Included once by the Deployment; prints nothing. */ -}}
{{- define "metadata-agent.checkValues" -}}
{{- $port := int .Values.service.internalPort -}}
{{- if or (lt $port 1) (gt $port 65535) -}}
{{- fail (printf "service.internalPort %v: a port from 1 to 65535 - the probes, the ServiceMonitor and the agent's internal server use it" .Values.service.internalPort) -}}
{{- end -}}
{{- /* The chart names production. Empty, the agent would take the default of
       its code, which is staging - and start. */ -}}
{{- range $name := list "METADATA_AGENT_B_API_BASE_URL" "METADATA_AGENT_REPOSITORY_URL" -}}
{{- if not (index $.Values.env $name) -}}
{{- fail (printf "env.%s is empty: the agent would fall back to the staging address in its code - name the address of this environment" $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* The largest request body as the ingress annotation must name it: whole
       megabytes as "<n>m", anything else in bytes. */ -}}
{{- define "metadata-agent.bodySize" -}}
{{- $bytes := int64 (include "metadata-agent.envValue" (index .Values.env "METADATA_AGENT_MAX_REQUEST_BYTES" | default 20971520)) -}}
{{- if eq (mod $bytes 1048576) 0 -}}
{{- printf "%dm" (div $bytes 1048576) -}}
{{- else -}}
{{- $bytes -}}
{{- end -}}
{{- end -}}
