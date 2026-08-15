# a1-lan-werkzeuge

Werkzeugkasten für den **Bambu Lab A1** über das lokale Netz — ohne Cloud,
ohne Bildschirmabgriff, ohne Zusatzhardware. Slicen, hochladen, starten,
überwachen, einzelne Objekte überspringen, abgebrochene Drucke fortsetzen.

> **Inoffiziell.** Kein Bezug zu Bambu Lab. Wer das einsetzt, steuert damit
> eine Maschine, die heiß wird und sich bewegt — auf eigene Verantwortung.
> Die Abnahmeprüfungen in diesem Repo sind aus Schäden entstanden, nicht aus
> Vorsicht; sie ersetzen kein Hinsehen.

## Was hier drin ist

| Werkzeug | Aufgabe |
|---|---|
| `a1_drucker.py` | Kamera und Status lesen. **Enthält keinen einzigen ändernden Befehl.** |
| `a1_befehle.py` | alles Schreibende: hochladen, starten, überspringen, pausieren |
| `pruefe_gcode.py` | Abnahme **vor** dem Hochladen — sperrt bei Befund |
| `pruefe_repo.py` | Abnahme vor dem Veröffentlichen (pre-push-Hook) |
| `konfig_bauen.py` | Filament-Vektoren einer Konfiguration konsistent zuschneiden |
| `spaghetti_wache.py` | Fehldruck-Erkennung per Kamera und Sprachmodell |
| `fortsetzung_bauen.py` | G-Code ab einer Schicht neu aufsetzen |
| `druck_journal.py` | schreibt laufend mit, wo der Druck steht |
| `stoerungs_quittierer.py` | quittiert bekannte Störcodes begrenzt automatisch |
| `standby_sperre.py` | hält den Rechner wach, solange gedruckt wird |
| `melde_helfer.py` | Desktop-Meldungen auf festen Kanälen |

Ausführlich: [SLICEN.md](SLICEN.md) (headless slicen, alle geprüften
Sackgassen) und [PLAN-GEWISSHEIT.md](PLAN-GEWISSHEIT.md) (warum die Wache so
gebaut ist).

## Einrichtung

```bash
python3 -m venv .venv
./.venv/bin/pip install anthropic pillow paho-mqtt
```

Drei Angaben braucht jedes Werkzeug, als Umgebungsvariablen:

```bash
export BAMBU_A1_IP=192.168.x.y        # Drucker → Netzwerk
export BAMBU_A1_SERIAL=01S00Axxxxxxx  # Drucker → Geräteinfo
export BAMBU_A1_ACCESS_CODE=xxxxxxxx  # Drucker → Netzwerk → LAN-Zugangscode
export ANTHROPIC_API_KEY=sk-ant-...   # nur für die Kamerabeurteilung
```

Die mitgelieferten `*.sh`-Starter holen sie aus einem Passwortspeicher; wer
keinen hat, setzt sie direkt. **Nie in eine Datei schreiben, nie ausgeben** —
der Zugangscode erlaubt Vollzugriff auf den Drucker.

Am Drucker muss **LAN-Only** an sein. Für `skip_objects` zusätzlich der
**Entwicklermodus** — ohne ihn quittiert die Firmware jeden Befehl im
`print`-Namensraum mit `err_code 84033543`. Das ist geprüft, nicht vermutet.

## Die Kette

```bash
# 1. Abnahme — die Freigabeliste kommt aus dem Gerät, nicht aus dem Kopf
./.venv/bin/python -c "
import a1_drucker, a1_befehle
pla = a1_drucker.faecher_mit('PLA')          # liest die echte AMS-Belegung
a1_befehle.hochladen('fertig.gcode.3mf', ams=pla, material=('PLA',))"

# 2. Platte per Kamera freigeben und starten
./.venv/bin/python -c "
import a1_befehle
print(a1_befehle.platte_frei())
print(a1_befehle.druck_starten('fertig.gcode.3mf', ams_fach=[0,1]))"

# 3. Überwachen
systemctl --user start spaghetti-wache.timer

# 4. Bei Bedarf ein Objekt überspringen
./skip.sh --liste
./skip.sh --ueberspringen 105
```

`hochladen()` ruft `pruefe_gcode.py` selbst auf und **verweigert die
Übertragung bei Befund**. Das ist Absicht und kein Komfortverlust.

## Warum die Prüfungen so misstrauisch sind

Sie sind aus vier Fehlversuchen an einem Tag entstanden, die alle dasselbe
Muster hatten: **geprüft wurde, DASS etwas da ist, nicht WELCHEN WERT es hat.**
Betttemperatur 35 statt 65. Brim „vorhanden" mit Breite 0. Bauteile bei
z −1,5. Und schließlich ein verstopfter Extruder.

Die drei Fallen, die Material und Gerät gekostet haben:

**1. `support_interface_filament` fällt ohne Angabe auf den CLI-Vorgabewert 3**
— das ist Filament 3, also Index 2, also physisch AMS-Fach 2. Wer
`enable_support` setzt und den Rest vergisst, druckt die Trennschicht mit dem
Material aus Fach 2. Bei uns lag dort PETG; es kam bei 255 °C in einen auf PLA
eingestellten Extruder. Immer **beide** Schlüssel setzen:

```json
"enable_support": "1", "support_filament": "0", "support_interface_filament": "0"
```

**2. Die Existenz eines Filamentindex sagt nichts.** Deklariert der Slicer
sechs Filamente, dann „existiert" auch das PETG in Fach 2 — gezogen werden
darf es trotzdem nicht. Die belastbare Regel: *jeder angeforderte Index muss
auf ein freigegebenes Fach mit freigegebenem Material zeigen.*

**3. `G29` vermisst eine Fläche, keinen Punkt.** Bambu bildet das
Nivellierrechteck aus der Objekt-Bounding-Box. Bei einem Fortsetzungsdruck
liegt darunter immer Werkstück — die Düse tastet auf Kunststoff, `M500`
schreibt das falsche Mesh fest. Ein Test, der nur Antast*punkte* prüft, kann
das grundsätzlich nicht finden.

**Und: maßgeblich ist der `CONFIG_BLOCK` im G-Code**, nicht
`Metadata/project_settings.config`. Die beiden können völlig auseinanderlaufen
— gemessen an einer echten Datei beschrieb der Container einen einfarbigen
PLA-Druck bei 200 °C, gefahren wurde sechsfarbig mit PETG bei 255 °C.

## Die Spaghetti-Wache

Alle 10 Minuten: Druckerzustand per MQTT, drei Kamerabilder über ~10 s, **auf
die Druckplatte zugeschnitten** (der A1 ist ein Bettschubser — jede Bettstellung
zeigt das Werkstück aus einem anderen Winkel; der Zuschnitt wirft das Gehäuse
weg, das Werkstück erscheint rund 2,5-mal so groß und kostet dabei weniger
Token als das Vollbild). Beurteilung durch ein Sprachmodell **mit dem
Druckerzustand als Zusammenhang** — Schicht 12 von 483 heißt: flach ist normal.
Bei Befund eine Gegenprobe mit frischen Bildern und umgekehrter Fragestellung.

| Lage | Alarm | Bedeutung |
|---|---|---|
| `ok` | — | Druck läuft sichtbar normal |
| `verdacht` | **ja**, nach Gegenprobe | etwas stimmt womöglich nicht |
| `spaghetti` | **ja**, nach Gegenprobe | eindeutiger Fehldruck |
| `nichts_haftet` | **ja**, nach Gegenprobe | Bauteil hat sich von der Platte gelöst |
| `entkraeftet` | — | erste Beurteilung schlug an, Gegenprobe widersprach |
| `kein_druck` | — | Drucker druckt gerade nicht |
| `sicht_verdeckt` | Hinweis | Bett auf keinem Bild beurteilbar |
| `fehler` | ab 3× | Drucker oder API nicht erreichbar |

**Die Wache bricht standardmäßig nicht ab.** Sie meldet. Ein autonomer Abbruch
erfolgt nur, wenn beide Stufen übereinstimmen, mindestens eine „hoch" sicher
ist und Schicht ≥ 12 erreicht wurde — diese drei Bedingungen kamen dazu,
nachdem ein Fehlalarm bei Schicht 4 einen guten Druck zerstört hatte.

Kosten: rund 4 400 Eingabe- und 100 Ausgabetoken je Lauf. Läuft kein Druck,
bricht ein Torwächter vor dem API-Aufruf ab — dann kostet es nichts.

## Grenzen

- **Der Blickwinkel.** Die A1-Kamera sitzt vorn unten und sieht das Bett fast
  von der Seite. Zuschnitt und drei Bilder mildern das, der flache Winkel bleibt.
- **Der Ausschnitt ist eingemessen, nicht erkannt.** Wird die Kamera verstellt,
  muss `KONFIG["ausschnitt"]` nachgezogen werden — `./lauf.sh --zeigen` zeigt,
  was das Modell tatsächlich sieht.
- **Zehn Minuten Takt.** Ein Fehldruck fällt im Mittel nach 5 Minuten auf.
- **Die Trefferquote ist nicht gemessen.** Die Wache hat echte Fehldrucke
  erkannt und mindestens einen Fehlalarm produziert. Eine belastbare Quote
  gibt es nicht.
- **Fortsetzungsdrucke sind Neuland.** `fortsetzung_bauen.py` prüft auf
  Kollisionen und räumt die Nahtstelle auf, ist aber selten erprobt.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
