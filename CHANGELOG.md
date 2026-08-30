# Changelog

## [0.1.0] - 2026-08-30

### Added
- M2: Entitäten — `event` je Tür (`card_accepted` / `card_rejected` /
  `card_unknown`, mit Geräte-Zeitstempel `event_time` für exakt getaktete
  Automationen), `sensor` „Letzte Freigabe" je Person (`device_class:
  timestamp`, neue Personen erscheinen automatisch), open-only `lock` für
  den Türöffner und ein `button` je Tür-Relais. Karten- und Personalnummern
  sind in Attributen standardmäßig maskiert (`****3721`), abschaltbar über
  den Options-Flow.
- M1: Integrations-Gerüst mit Polling-Transport (Digest-Auth-Helper nach
  RFC 7616, ISAPI-API-Schicht mit Semaphore-Deckel, Coordinator mit
  Fenster-Überlappung und Deduplizierung, Config Flow mit Re-Auth und
  Options-Flow), Übersetzungen de/en, CI (ruff, pytest, hassfest).
- M0-Ergebnis dokumentiert: `AccessControllerEvent` erscheint auf der
  Referenz-Firmware (V3.7.1 build 251112) nicht im `alertStream` —
  Transport ist deshalb Polling.
