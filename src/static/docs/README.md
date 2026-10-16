# Swagger UI (selbst ausgeliefert)

Die API-Doku unter `/docs` lädt diese Dateien vom Agenten selbst, nicht von
einem CDN. So geht beim Öffnen der Doku keine Anfrage an Dritt-Server.

| | |
|---|---|
| Paket | `swagger-ui-dist` 5.33.1 (npm, veröffentlicht 2026-10-01) |
| Integrität | `sha512-H872wWkA53bFIsGgi7OWgmq+CRWw3nFQGdJWRqOB9wNwTm6e5ol34+qPDkV4AJlK+gglM2EsJiOOzsGsEGbluA==` |
| Lizenz | Apache-2.0, siehe `LICENSE` und `NOTICE` |
| Dateien | `swagger-ui-bundle.js`, `swagger-ui.css`, `favicon-32x32.png` unverändert aus dem Paket |

Aktualisieren:

```bash
npm pack swagger-ui-dist@<version>
npm view swagger-ui-dist@<version> dist.integrity
```

Die Prüfsumme des Archivs mit der aus der Registry vergleichen, die drei Dateien
sowie `LICENSE` und `NOTICE` hierher kopieren und Version und Prüfsumme oben
anpassen. `tests/test_docs_self_hosted.py` prüft, dass `/docs` danach weiter nur
vom Agenten lädt.
