# Vacuum Planner

Putzplan, tägliche Warteschlange und ein mitdenkender Ablauf für Saugroboter mit Raumreinigung in Home Assistant. Jeder Raum hat einen Plan (Modus und Intervall), fällige Räume stehen **jeden Tag automatisch in der Warteschlange**, und die Warteschlange wird in Etappen abgearbeitet, solange niemand zuhause ist. Dabei achtet die Integration auf Ruhezeiten und auf die Zeit bis zur erwarteten Rückkehr.

Entstanden aus den Automationen „Saugplan & Warteschlange“ und jetzt als eigene Integration umgesetzt: testbar, neustartfest und ohne String-Parsing in `input_text`.

## Ablauf

1. **Fällig → Warteschlange.** Ein Raum ist fällig, wenn `zuletzt + Intervall ≤ heute`. Fällige Räume landen sofort (und nach Mitternacht) mit dem Plan-Modus in der Warteschlange. Von Hand eingereihte Räume kommen dazu und haben Vorrang. Pro Raum gibt es nur einen Eintrag; Modi werden zusammengeführt (Saugen + Wischen → Saugen & Wischen).
   - Entfernst du einen fälligen Raum, wird er **für heute ausgelassen** und morgen wieder eingereiht.
   - Räume lassen sich für einige Tage **aussetzen**.
2. **Wann gestartet wird.** Die Wohnung muss seit einigen Minuten leer sein: Niemand ist zuhause, die nächste Person ist weiter als die Mindestentfernung weg und niemand ist innerhalb des Heimweg-Radius auf dem Rückweg. Außerdem darf kein Gastmodus aktiv sein und Ruhezeiten müssen eingehalten werden. Vor dem Start kommt eine **Vorwarnung** mit „Heute nicht“ / „Jetzt“.
3. **Ruhezeiten** (einstellbar, deutscher Standard): geputzt wird nur zwischen 8 und 20 Uhr, nicht während der Mittagsruhe (13–15 Uhr) und nicht an Sonn- und Feiertagen. Feiertage erkennt die Integration über einen Workday-Sensor.
4. **Zeitbudget.** Geplant wird nur, was bis zur nächsten Ruhezeit und bis kurz vor der **erwarteten Rückkehr** fertig wird:
   - Die Rückkehr wird aus euren bisherigen Abwesenheiten gelernt, nach Werktag oder Wochenende und nach Uhrzeit des Weggehens. Verwendet wird das untere Quartil, also eher vorsichtig. Beim ersten Start werden die Abwesenheiten aus dem Recorder-Verlauf übernommen.
   - Läuft gerade ein Kalendertermin, gilt sein Ende plus Heimweg.
   - Die Dauer je Raum und Modus wird aus den gemessenen Auftragsdauern gelernt.
5. **Etappen.** Eine Etappe ist ein Roboterauftrag mit allen Räumen desselben Modus. Die Reihenfolge ist trocken vor nass: Saugen, dann Saugen & Wischen, dann Erst saugen, dann wischen, zuletzt nur Wischen. Innerhalb einer Etappe fährt er die Räume in Routen-Reihenfolge ab. Passt nicht alles ins Budget, kommen zuerst die manuellen und dann die am längsten überfälligen Räume dran. Vor jeder Etappe werden Akku, Frischwasser und Station (wäscht/saugt ab) geprüft.
6. **„Bitte nicht jetzt“ je Raum.** Für jeden Raum lassen sich Entitäten hinterlegen, die ihn sperren, zum Beispiel ein Mediaplayer, der gerade abspielt. Der Raum wird dann übersprungen und frühestens 5 Minuten nach dem Ende nachgeholt.
7. **Erledigt** ist ein Raum erst, wenn der Auftrag mit `Completed` endet, nicht unterbrochen wurde und der gefahrene Modus den geplanten abdeckt.
8. **Heimkommen.** Nähert sich jemand, während er putzt, bekommt diese Person eine Push-Nachricht mit „Zur Station“ / „Weitermachen“. Ohne Antwort fährt er nach 3 Minuten zurück; die Räume bleiben dann in der Warteschlange.
9. **Fehler.** Bei einem Fehler gibt es eine Push-Nachricht an alle. Die Räume des Auftrags laufen beim nächsten Mal **einzeln**, damit klar wird, welcher Raum das Problem ist. Scheitert ein Raum öfter als „Wiederholungen nach Fehler“ erlaubt, wird er bis morgen ausgelassen.
10. **Plan B.** Ist ein Raum seit N Tagen überfällig (weil ihr nicht weg wart), fragt die Integration zur eingestellten Uhrzeit nach, ob jetzt geputzt werden soll.

## Installation (HACS)

HACS → ⋮ → *Benutzerdefinierte Repositories* → dieses Repo, Kategorie **Integration** → *Vacuum Planner* herunterladen und Home Assistant neu starten. Danach *Einstellungen → Geräte & Dienste → Integration hinzufügen → Vacuum Planner*.

Voraussetzungen:
- Ein Roboter, der `vacuum.send_command` mit `room_clean` und `params.rooms: [{id, clean_mode}]` versteht, zum Beispiel eufy über die Integration robovac_mqtt. Die Räume werden aus dem Attribut `rooms` gelesen.
- Eine [Proximity](https://www.home-assistant.io/integrations/proximity/)-Instanz für die Zuhause-Zone. Daraus kommen die Personen, ihre Entfernungen und Bewegungsrichtungen.
- Für Push-Nachrichten die Companion-App. Je Person wird ein Gerät gewählt.

## Entitäten

| Entität | Zweck |
|---|---|
| `sensor.…_status` | `idle`, `waiting`, `prewarn`, `starting`, `cleaning`, `between_stages`, `error`. Das Attribut `reason` sagt warum, z. B. `someone_home`, `quiet_midday`, `no_time`, `rooms_blocked`, `charging`. |
| `sensor.…_queue` | Anzahl der Räume. Die Attribute `items`, `stages`, `total_minutes`, `finish`, `next_stage` und `rooms` (Plan je Raum) bilden die Datenquelle für die Dashboard-Karte. |
| `sensor.…_expected_return` | Erwartete Rückkehr, mit Quelle (`learned`, `calendar`, `default`). |
| je Raum: `select.…_mode`, `number.…_interval`, `sensor.…_next_due`, `sensor.…_last_cleaned` | Plan. |
| `switch.…_automatic_cleaning`, `…_midday_rest`, `…_quiet_on_sundays_and_holidays` | Automatik und Ruhezeiten. |
| `time.…_cleaning_from/until`, `…_midday_rest_from/until`, `…_ask_at` | Zeitfenster. |
| `number.…_minimum_distance`, `…_way_home_radius`, `…_minimum_battery`, `…_pre_warning`, `…_ask_when_overdue_for`, `…_retries_after_an_error` | Einstellungen. |
| `button.…_start_now`, `…_stop`, `…_queue_due_rooms` | Bedienung. |

## Dienste

`vacuum_planner.enqueue`, `dequeue` (mit `skip_today`), `move`, `clear_queue`, `enqueue_due`, `start` (`force`: Anwesenheit und Zeit ignorieren), `stop`, `clean_now` (sofort, wird im Plan verbucht), `pause_room`, `resume_room`, `set_plan`, `set_last_cleaned`. Räume können als IDs oder Namen angegeben werden, bei `clean_now` auch als `all`.

Nach jedem erledigten Auftrag feuert das Event `vacuum_planner_job_finished` mit Räumen, Modus und Dauer.

## Entwicklung

```sh
python3 scripts/run_pure_tests.py     # Logik ohne Home Assistant
pip install -r requirements_test.txt && pytest
```
