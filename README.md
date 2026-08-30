# Hikvision Access Control für Home Assistant

> **Status: in Entwicklung (pre-release).** Kern-Transport und Config Flow stehen,
> Entitäten folgen. Noch nicht für den produktiven Einsatz gedacht.

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

## Geplante Entitäten (M2)

- `event` je Tür: `card_accepted` / `card_rejected` / `card_unknown`
- `sensor` je Person: Zeitpunkt der letzten Freigabe (`device_class: timestamp`)
- `lock` (open-only) für den Türöffner, `button` je Relais

Kartennummern sind in Attributen standardmäßig maskiert (Options-Schalter für
Vollanzeige).

## Lizenz

[MIT](LICENSE)
