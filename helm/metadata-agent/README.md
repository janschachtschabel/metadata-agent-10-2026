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
  --set secret.existingSecret=metadata-agent \
  --set ingress.enabled=true --set ingress.host=<host>
```

`B_API_KEY` entfällt mit `METADATA_AGENT_B_API_AUTH_MODE=forward`. Alle
Secret-Schlüssel sind optional eingebunden; ob die Konfiguration arbeiten
kann, meldet `/ready`.

## Was der Chart anlegt

| Ressource | Hinweis |
|---|---|
| Deployment | Probes auf dem internen Port (`/health`, `/ready`), 120 s Grace-Period für laufende `/generate`, läuft als UID 1000 ohne Capabilities |
| Service | Port 8000 (API) und 9000 (intern, nur für Prometheus) |
| Ingress | nur mit `ingress.enabled`; nur Port 8000; Annotationen für 120 s Timeout und 20 MB Body (ingress-nginx) |
| Secret | nur mit `secret.create` und `--set secret.values.<KEY>=…`; sonst ein vorhandenes Secret |
| ServiceMonitor, PrometheusRule | brauchen die CRDs des Prometheus Operators (z.B. kube-prometheus-stack); abschaltbar mit `serviceMonitor.enabled=false` / `prometheusRule.enabled=false`. `…labels` für den `serviceMonitorSelector`/`ruleSelector` |

## Alarmregeln

Die Regeln stehen als einfache Prometheus-Regeldatei in
[`files/alerts.yaml`](files/alerts.yaml); Übersicht in UEBERGABE §4.

## Prüfen

```bash
helm lint helm/metadata-agent --strict
promtool check rules helm/metadata-agent/files/alerts.yaml
promtool test rules helm/metadata-agent/tests/alerts.test.yaml
```

Ohne lokales promtool:
`docker run --rm -v "$PWD/helm/metadata-agent:/chart:ro" --entrypoint promtool prom/prometheus:v3.5.0 test rules /chart/tests/alerts.test.yaml`.
`tests/test_helm_chart.py` (pytest) gleicht Metriken, Label-Werte,
Env-Variablen und Version mit dem Code ab. Die CI führt alle drei aus.

Geändert: `version` in `Chart.yaml` erhöhen; `appVersion` und `image.tag`
folgen der Version in `pyproject.toml`.
