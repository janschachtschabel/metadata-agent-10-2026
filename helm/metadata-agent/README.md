# Helm-Chart: Metadata Agent API

Installiert die API (mit der ausgelieferten Webkomponente) als Deployment mit
Service, optionalem Ingress, `ServiceMonitor` und Alarmregeln
(`PrometheusRule`). Betriebsdetails: [UEBERGABE.md](../../UEBERGABE.md),
alle Einstellungen: [ENV-PARAMETER.md](../../ENV-PARAMETER.md).

## Installieren

```bash
kubectl create secret generic metadata-agent \
  --from-literal=B_API_KEY='<B_API_KEY>' \
  --from-literal=WLO_GUEST_USERNAME='<WLO_GUEST_USERNAME>' \
  --from-literal=WLO_GUEST_PASSWORD='<WLO_GUEST_PASSWORD>'

helm install metadata-agent helm/metadata-agent \
  --set image.repository=$DOCKER_REGISTRY/projects/wlo/meta-services/metadata-agent-api \
  --set secret.existingSecret=metadata-agent
```

`image.repository` ist Pflicht. `B_API_KEY` entfällt mit
`METADATA_AGENT_B_API_AUTH_MODE=forward`. Alle Secret-Schlüssel sind optional
eingebunden; ob die Konfiguration arbeiten kann, meldet `/ready`.

**Kein Ingress hinter der B-API.** Der B-API-Proxy (`forwardApiKey`) ruft den
Service im Cluster auf, z.B. `http://metadata-agent.<namespace>:8000`. Ein
Ingress machte den Dienst aus dem Internet erreichbar, und im Modus `auto`
könnte dann jeder Erschließungen auf `B_API_KEY` auslösen. `ingress.enabled`
nur für Variante B (ohne B-API) hinter einer Zugangsbeschränkung, oder mit
`env.METADATA_AGENT_B_API_AUTH_MODE=forward` und auf die Quelladressen der
B-API begrenzt. Ist der Ingress im Modus `auto` aktiv, warnt `helm install`.

**Secret ändern:** Pods lesen ein vorhandenes Secret nur beim Start. Nach einer
Rotation `kubectl rollout restart deployment/metadata-agent` (oder einen
Reloader einsetzen). Ein mit `secret.create` angelegtes Secret startet die Pods
selbst neu.

**IPv6-Cluster:** `env.METADATA_AGENT_INTERNAL_HOST: "::"`, damit die Probes den
internen Port über IPv6 erreichen.

## Was der Chart anlegt

| Ressource | Hinweis |
|---|---|
| Deployment | Probes auf dem internen Port (`/health`, `/ready`), 120 s Grace-Period für laufende `/generate`, läuft als UID 1000 ohne Capabilities |
| Service | Port 8000 (API) und 9000 (intern, nur für Prometheus) |
| Ingress | nur mit `ingress.enabled` (hinter der B-API nicht, siehe oben); nur Port 8000; Annotationen für 120 s Timeout und 20 MB Body (ingress-nginx) |
| PodDisruptionBudget | bei mehr als einer Replica: mindestens ein Pod läuft während einer Node-Wartung weiter |
| Secret | nur mit `secret.create` und `--set secret.values.<KEY>=…`; sonst ein vorhandenes Secret |
| ServiceMonitor, PrometheusRule | brauchen die CRDs des Prometheus Operators (z.B. kube-prometheus-stack); abschaltbar mit `serviceMonitor.enabled=false` / `prometheusRule.enabled=false`. `…labels` für den `serviceMonitorSelector`/`ruleSelector` |

## Alarmregeln

Die Regeln stehen als einfache Prometheus-Regeldatei in
[`files/alerts.yaml`](files/alerts.yaml); Übersicht in UEBERGABE §4.

## Prüfen

```bash
helm lint helm/metadata-agent --strict --set image.repository=ci/metadata-agent-api
promtool check rules helm/metadata-agent/files/alerts.yaml
promtool test rules helm/metadata-agent/tests/alerts.test.yaml
```

Ohne lokales promtool:
`docker run --rm -v "$PWD/helm/metadata-agent:/chart:ro" --entrypoint promtool prom/prometheus:v3.5.0 test rules /chart/tests/alerts.test.yaml`.
`tests/test_helm_chart.py` (pytest) gleicht Metriken, Label-Werte,
Env-Variablen und Version mit dem Code ab. Die CI führt alle drei aus.

Geändert: `version` in `Chart.yaml` erhöhen; `appVersion` und `image.tag`
folgen der Version in `pyproject.toml`.
