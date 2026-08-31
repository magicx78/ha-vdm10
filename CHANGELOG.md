# Changelog

## [0.1.3] - 2026-09-01

### Fixed
- **Verbindungsflut am Gerät behoben:** Die Referenz-Firmware antwortet auf
  jede Anfrage mit `Connection: close`, solange der Client Keep-Alive nicht
  ausdrücklich anfordert — obwohl HTTP/1.1 es zum Standard macht. Gemessen:
  6 Anfragen erzeugten 6 TCP-Verbindungen ohne den Header und genau 1 mit
  ihm. Bei 2 s Abfrageintervall hinterließ das rund 30 Sockets pro Minute im
  Wartezustand und erzwang jedes Mal einen neuen Digest-Handshake. Am
  Produktivgerät verifiziert: 43 → 23 Sockets insgesamt, davon erstmals eine
  dauerhaft aktive Verbindung.
- `RuntimeError("Session is closed")` beim Herunterfahren von Home Assistant
  wird als Verbindungsfehler behandelt statt als unerwarteter Fehler.

### Added
- **Reconfigure-Flow**: Zieht das Gerät auf eine neue IP-Adresse um, lässt
  sich das jetzt in der Oberfläche ändern, statt den Eintrag löschen zu
  müssen. Die Seriennummer wird dabei geprüft.

## [0.1.1] - 2026-08-30

### Fixed
- Ständige „Neu anmelden"-Aufforderungen im Parallelbetrieb: Die Firmware
  verweigert unter Last sporadisch Anfragen mit einem 401 **ohne**
  Digest-Challenge (~1×/Minute beobachtet). Das gilt jetzt als transienter
  Gerätefehler statt als Zugangsdaten-Problem. Re-Auth startet nur noch
  nach ≥5 aufeinanderfolgenden Fehlschlägen über ≥60 s; beim Setup wird
  stattdessen automatisch neu versucht (ConfigEntryNotReady).
- Entitäten flackern nicht mehr bei einzelnen Fehl-Polls: bis zu 4
  aufeinanderfolgende transiente Fehler behalten still den letzten
  Datenstand (ohne Events nachzufeuern).
- Deprecation-Warnung zum Options-Update-Listener behoben
  (`async_schedule_reload`).

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
