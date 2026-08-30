# Changelog

## [Unreleased]

### Added
- M1: Integrations-Gerüst mit Polling-Transport (Digest-Auth-Helper nach
  RFC 7616, ISAPI-API-Schicht mit Semaphore-Deckel, Coordinator mit
  Fenster-Überlappung und Deduplizierung, Config Flow mit Re-Auth und
  Options-Flow), Übersetzungen de/en, CI (ruff, pytest, hassfest).
- M0-Ergebnis dokumentiert: `AccessControllerEvent` erscheint auf der
  Referenz-Firmware (V3.7.1 build 251112) nicht im `alertStream` —
  Transport ist deshalb Polling.
