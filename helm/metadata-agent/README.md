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
eingebunden; ob die Konfiguration arbeiten kann, meldet `/ready` – ohne
`WLO_GUEST_USERNAME`/`WLO_GUEST_PASSWORD` (etwa bei einem Tippfehler in
`secret.existingSecret`) bleibt der Pod nicht bereit. Ein Deployment, das nur
erschließt, setzt `env.METADATA_AGENT_REQUIRE_REPOSITORY: "false"` (bereit ohne
sie, `/upload` und `/nodes` antworten dann mit `503`). Prüfliste für die erste
Installation und Rollback: [DEPLOYMENT.md](../../DEPLOYMENT.md).

**Kein Ingress hinter der B-API.** Der B-API-Proxy (`forwardApiKey`) ruft den
Service im Cluster auf, z.B. `http://metadata-agent.<namespace>:8000`. Ein
Ingress machte den Dienst aus dem Internet erreichbar, und im Modus `auto`
könnte dann jeder Erschließungen auf `B_API_KEY` auslösen. `ingress.enabled`
nur für Variante B (ohne B-API) hinter einer Zugangsbeschränkung, oder mit
`env.METADATA_AGENT_B_API_AUTH_MODE=forward` und auf die Quelladressen der
B-API begrenzt. Ist der Ingress im Modus `auto` aktiv, warnt `helm install`.
Mit Ingress und NetworkPolicy (Standard) gehört der Namespace des
Ingress-Controllers (z.B. `ingress-nginx`) in `networkPolicy.ingressNamespaces`,
sonst blockiert die Policy den Controller (502/504); `helm install` warnt auch hier.

**Secret ändern:** Pods lesen ein vorhandenes Secret nur beim Start. Nach einer
Rotation `kubectl rollout restart deployment/metadata-agent` (oder einen
Reloader einsetzen). Das gilt auch für ein mit `secret.create` angelegtes
Secret: Die frühere Annotation `checksum/secret` zeigte einen ungesalzenen Hash
der Secret-Werte jedem, der Deployments lesen darf, und ist entfallen.

**AcademicCloud:** Das GWDG-Limit (~10 Aufrufe/min, 2 gleichzeitig) gilt für
den Schlüssel, die Drosselung des Agenten je Pod. `sharedLlmLimit.enabled=true`
teilt es durch `replicaCount` und setzt die beiden Durchsatz-Variablen je Pod;
`helm install` warnt, wenn AcademicCloud oder der Router ohne diese Aufteilung
mit mehreren Replicas läuft.

**IPv6-Cluster:** `env.METADATA_AGENT_INTERNAL_HOST: "::"`, damit die Probes den
internen Port über IPv6 erreichen.

## Was der Chart anlegt

| Ressource | Hinweis |
|---|---|
| Deployment | Probes auf dem internen Port (`/health`, `/ready`), 320 s Grace-Period für laufende `/generate` (bis 300 s), läuft als UID 1000 ohne Capabilities, ohne Service-Account-Token und mit schreibgeschütztem Root-Dateisystem (`/tmp`, `/home/appuser` als emptyDir mit `sizeLimit`, `scratchVolumes`); Replicas weich über Knoten verteilt (`topologySpread`, `ScheduleAnyway`); `METADATA_AGENT_INTERNAL_PORT` kommt aus `service.internalPort`, `FORWARDED_ALLOW_IPS` aus `forwardedAllowIps` (DEPLOYMENT.md, „Hinter TLS“) |
| Service | Port 8000 (API) und 9000 (intern, nur für Prometheus) |
| Ingress | nur mit `ingress.enabled` (hinter der B-API nicht, siehe oben); nur Port 8000; Annotationen für 320 s Timeout und 20 MB Body (ingress-nginx); ein B-API-Proxy oder edu-sharing davor braucht ebenso mindestens ~310 s |
| NetworkPolicy | Standard an: Port 8000 nur aus `networkPolicy.ingressNamespaces` (`b-api`), Port 9000 nur aus `prometheusNamespaces` (`monitoring`); ausgehend DNS nur zum Cluster-DNS (`networkPolicy.dns`), `egressNamespaces` nur auf `egressPorts` (Ports der Ziel-Pods) und Internet (IPv4) ohne private Netze. **Namespaces und Ports vor der Installation an den Cluster anpassen** (Details UEBERGABE §6; IPv6, LoadBalancer/DNAT und NodeLocal DNSCache: DEPLOYMENT.md, „Grenzen der NetworkPolicy“); braucht ein CNI mit NetworkPolicy-Unterstützung |
| PodDisruptionBudget | bei mehr als einer Replica: mindestens ein Pod läuft während einer Node-Wartung weiter |
| Secret | nur mit `secret.create` und `--set secret.values.<KEY>=…`; sonst ein vorhandenes Secret |
| ServiceMonitor, PrometheusRule | brauchen die CRDs des Prometheus Operators (z.B. kube-prometheus-stack); abschaltbar mit `serviceMonitor.enabled=false` / `prometheusRule.enabled=false`. `…labels` für den `serviceMonitorSelector`/`ruleSelector` |

## Alarmregeln

Die Regeln stehen als einfache Prometheus-Regeldatei in
[`files/alerts.yaml`](files/alerts.yaml); Übersicht in UEBERGABE §4.

## Prüfen

```bash
helm lint helm/metadata-agent --strict --set image.repository=ci/metadata-agent-api
helm lint helm/metadata-agent --strict --set image.repository=ci/metadata-agent-api --set ingress.enabled=true
helm template ci helm/metadata-agent --set image.repository=ci/metadata-agent-api | kubeconform -strict -summary -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -
promtool check rules helm/metadata-agent/files/alerts.yaml
promtool test rules helm/metadata-agent/tests/alerts.test.yaml
```

Ohne lokales promtool:
`docker run --rm -v "$PWD/helm/metadata-agent:/chart:ro" --entrypoint promtool prom/prometheus:v3.5.0 test rules /chart/tests/alerts.test.yaml`.
`tests/test_helm_chart.py` (pytest) gleicht Metriken, Label-Werte,
Env-Variablen und Version mit dem Code ab und rendert den Chart (auch
`NOTES.txt`), wo helm installiert ist; mit `REQUIRE_HELM=1` (beide CI) schlägt
ein fehlendes helm fehl, statt die Tests zu überspringen. Die CI führt alles
aus, kubeconform mit Default- und Ingress-Values.

Geändert: `version` in `Chart.yaml` erhöhen; `appVersion` und `image.tag`
folgen der Version in `pyproject.toml`.
