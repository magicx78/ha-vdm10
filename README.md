# Hikvision Access Control für Home Assistant

> **Status: Beta.** Läuft seit 2026-09 als einzige Anbindung der Referenz-
> Türstation produktiv; Verhalten anderer ISAPI-Geräte ist unverifiziert.

Bringt den **Zutritts-Teil** von Hikvision-ISAPI-Geräten nach Home Assistant —
das, was `hikvision_next` (Kamera-Ereignisse) nicht abdeckt: RFID-Kartenlesungen
mit Personenzuordnung, „letzte Freigabe" je Person und den Fern-Türöffner.

Referenzgerät: **Metzler VDM10** Türstation (Hikvision-OEM `VDM10-VM-2W-2.0`,
Firmware V3.7.1). Andere ISAPI-Zutrittsgeräte sollten funktionieren, sind aber
unverifiziert.

## Warum Polling?

Diese Firmware kann Zutrittsereignisse nachweislich **nicht pushen**: der
Ereignis-Stream (`/ISAPI/Event/notification/alertStream`) liefert nur
Kamera-Ereignisse, HTTP-Host-Benachrichtigungen werden für Zutritt nie
gesendet, und das Ereignis-Abo ist nicht beschreibbar (verifiziert am
30.08.2026). Die Integration pollt deshalb das Zutrittsprotokoll
(`AcsEvent`) mit konfigurierbarem Intervall (Standard 2 s) — mit
überlappenden Fenstern und Deduplizierung, damit keine Lesung verloren geht
und keine doppelt gemeldet wird.

## Entitäten

Alle Entitäten hängen an einem Gerät je Config-Eintrag.

- `event` je Tür: `card_accepted` / `card_rejected` / `card_unknown`, mit dem
  Geräte-Zeitstempel der Lesung als Attribut `event_time`
- `sensor` je Person: Zeitpunkt der letzten Freigabe (`device_class: timestamp`);
  neue Personen auf dem Gerät bekommen automatisch einen Sensor
- `lock` (open-only) für den Türöffner, `button` je Tür-Relais
- `button` „Neu starten" (`PUT /ISAPI/System/reboot`) für Wartungs-Automationen
- `switch` „Gegensprechen" und `switch` „Gegensprech-Wächter" (siehe unten)

Kartennummern sind in Attributen standardmäßig maskiert (Options-Schalter für
Vollanzeige). Ein Diagnose-Download (Geräteseite → „Diagnose herunterladen")
enthält keine Zugangsdaten und nur maskierte Nummern.

## Two-way audio guard

**Warum:** Die Referenz-Firmware schaltet ihren ISAPI-Gegensprechkanal
(`/ISAPI/System/TwoWayAudio/channels/1`) von selbst ab — beobachtet nach einem
Firmware-Update, nach hart abgerissenen ISAPI-Sitzungen und nach einem
go2rtc-Umzug, dreimal innerhalb einer Woche. Es gibt dafür keinerlei
Fehlermeldung: Gegensprechen und Ansagen über den go2rtc-Rückkanal
(`isapi://`) sind einfach still, bis jemand den Kanal in der Weboberfläche
wieder einschaltet.

**Was die Integration tut:** Der Coordinator prüft den Kanal im normalen
Poll mit, aber gedrosselt (Option „Prüfintervall Gegensprechen", Standard
60 s, 10–3600 s) — das 2-s-Ereignis-Polling bekommt also nicht in jedem
Zyklus eine zusätzliche Anfrage. Ist der Kanal aus und der Wächter an
(Option „Gegensprech-Wächter", Standard an), wird er sofort wieder
eingeschaltet (`PUT` mit dem vom Gerät gemeldeten `audioCompressionType`,
danach `GET` zur Bestätigung), gezählt und als Home-Assistant-Ereignis
`hikvision_access_two_way_audio_restored` gemeldet (`entry_id`, `device`,
`channel`, `timestamp`, `repairs`). Schlägt die Reparatur dreimal in Folge
fehl, erscheint eine Reparatur-Meldung; sie verschwindet von selbst, sobald
der Kanal wieder an ist.

Ein Fehlschlag der Prüfung berührt das Ereignis-Polling und die übrigen
Entitäten nie: Der Schalter zeigt dann „unbekannt", mehr nicht.

- `switch` **Gegensprechen** (Kategorie Konfiguration): Zustand des Kanals,
  Ein-/Ausschalten per `PUT` mit sofortiger Neuabfrage nur dieses Werts.
  Attribute `audio_compression`, `last_checked`, `repairs_count`,
  `guard_enabled`. Ausschalten bei aktivem Wächter hält nur bis zur
  nächsten Prüfung.
- `switch` **Gegensprech-Wächter** (Kategorie Konfiguration): spiegelt die
  Option und ändert sie ohne Neuladen des Eintrags.

Beispiel-Automation, die eine Reparatur meldet:

```yaml
triggers:
  - trigger: event
    event_type: hikvision_access_two_way_audio_restored
actions:
  - action: notify.notify
    data:
      message: >-
        Gegensprechen an {{ trigger.event.data.device }} wieder eingeschaltet
        ({{ trigger.event.data.repairs }}. Reparatur).
```

## Lizenz

[MIT](LICENSE)
