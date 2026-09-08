# Changelog

## [0.2.0] - 2026-09-08

### Added
- **Gegensprech-Wächter (Two-way audio guard):** Die Referenz-Firmware
  schaltet ihren ISAPI-Gegensprechkanal von selbst ab (dreimal seit
  02.09.2026: nach einem Firmware-Update, nach abgerissenen ISAPI-Sitzungen,
  nach einem go2rtc-Umzug) — ohne Fehlermeldung, Gegensprechen und Ansagen
  über den go2rtc-`isapi://`-Rückkanal sind dann einfach tot. Der
  Coordinator prüft den Kanal jetzt gedrosselt im normalen Poll mit (Option
  `two_way_audio_check_interval`, Standard 60 s, 10–3600 s) und schaltet
  ihn bei aktivem Wächter (Option `two_way_audio_guard`, Standard an) sofort
  wieder ein: `PUT` mit dem vom Gerät gemeldeten `audioCompressionType`,
  `GET` zur Bestätigung, Info-Log, Zähler und Home-Assistant-Ereignis
  `hikvision_access_two_way_audio_restored` (`entry_id`, `device`,
  `channel`, `timestamp`, `repairs`). Nach drei fehlgeschlagenen
  Reparaturen in Folge erscheint eine Reparatur-Meldung
  (`two_way_audio_disabled`, Warnung), die automatisch verschwindet, sobald
  der Kanal wieder an ist. Ersetzt das manuelle Behelfsskript
  `vdm10_fix_twowayaudio.py`.
- `switch` **Gegensprechen** (Kategorie Konfiguration, `mdi:account-voice`):
  Kanalzustand aus dem Coordinator; Ein-/Ausschalten sendet das `PUT` und
  liest nur diesen Wert neu (kein voller Ereignis-Poll). Attribute
  `audio_compression`, `last_checked`, `repairs_count`, `guard_enabled`.
- `switch` **Gegensprech-Wächter** (Kategorie Konfiguration): spiegelt die
  Option und aktualisiert sie beim Umschalten **ohne Neuladen** des
  Eintrags — der Coordinator liest beide Wächter-Optionen live. Andere
  Optionsänderungen laden den Eintrag weiterhin neu.
- Beide Optionen im Options-Flow und im Reconfigure-Flow; Übersetzungen
  de/en inklusive Reparatur-Text.
- **Diagnose-Download** (`diagnostics.py`): Eintrag mit maskierten
  Zugangsdaten, Geräteinfo ohne Seriennummer/MAC, Personenzahl mit
  maskierten Personalnummern, letztes Ereignis maskiert, kompletter
  Gegensprech-Block (Zustand, Codec, letzte Prüfung, Intervall, Wächter,
  Zähler, Fehlversuche, Meldung offen).
- API: `async_get_two_way_audio()` / `async_set_two_way_audio()` mit
  XML-Parsern für Einzelkanal, Kanalliste und `ResponseStatus`.

### Notes
- Ein Fehlschlag der Kanalprüfung ist vollständig vom Ereignis-Poll
  isoliert (eigener `try`, nur Debug-Log, Zustand „unbekannt") — auch bei
  einem Fehler im Wächter selbst. Der Ereignis-Poll bleibt unverändert bei
  2 s.
- 94 Tests (vorher 72): Parser gegen anonymisierte Fixtures, Drosselung
  (kein Check vor Ablauf des Intervalls), Reparatur + Ereignis, Wächter aus,
  Check-Fehler ohne Auswirkung auf den Poll, Schalter, Reparatur-Meldung
  nach drei Fehlversuchen und deren Auflösung, keine Ereignis-Wiederholung
  beim Out-of-band-Publish, Options-/Reconfigure-Flow, Diagnose.

## [0.1.4] - 2026-09-06

### Added
- **Neustart-Taster** (`button`, Geräteklasse „Neustart", Kategorie
  Konfiguration): sendet `PUT /ISAPI/System/reboot`. Gedacht für
  Wartungs-Automationen, die das Gerät nachts neu starten — ersetzt den
  entsprechenden Dienst von `hikvision_next`. Das Gerät ist danach ein bis
  zwei Minuten nicht erreichbar; der Coordinator fängt die Fehl-Polls ab.
- Am Produktivgerät (VDM10, build 251112) verifiziert: 15 s nach dem
  Tastendruck war die Station vom Netz, nach rund 55 s antwortete sie
  wieder und lieferte dann etwa 80 s lang nur 401 (Boot-Phase). Der
  Coordinator hat das ohne Re-Auth-Aufforderung überstanden.

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
