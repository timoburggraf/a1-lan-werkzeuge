# Slicen ohne GUI — aber mit Bambus eigener Maschinerie

Vorgabe Timo (14.08.2026): AMS-Handhabung, Slicing und Stützstrukturen sollen
von Bambu Lab kommen und **nicht von uns maschinell nachgebaut** werden. Der
Grundsatz ist richtig, und der Weg dahin ist inzwischen belegt.

## Der richtige Weg: Projektdatei als Vorlage

**Bambu Studio legt in einem Projekt nur die fertig zusammengeführte
Konfiguration ab** (`Metadata/project_settings.config`) — getypte Presets
gibt es dort nicht. Die CLI wiederum frisst genau diese zusammengeführte
Konfiguration *nicht* (`unknown config type`). Der einzige Weg, bei dem
Bambu die Zusammenführung selbst macht, ist deshalb:

```bash
flatpak run --filesystem=home --command=bambu-studio com.bambulab.BambuStudio \
  --slice 0 --export-3mf "fertig.gcode.3mf" --outputdir "$PWD" \
  vorlage.3mf  Teil_1.stl Teil_2.stl ...
```

Die Projektdatei bringt Drucker, Filament, Prozess, Plattentyp und vor allem
den vollständigen Start-G-Code mit; die STL-Dateien werden als zusätzliche
Objekte eingefügt.

**Gemessen** (Projektdatei `Front_Blende.3mf` über die CLI geslicet gegen
dieselbe Platte aus der grafischen Oberfläche):

| | CLI aus Projekt | GUI |
|---|---|---|
| Start-G-Code | 58 939 Zeichen | 58 902 |
| `G29` Nivellierung | 10 | 10 |
| `M900` Druckvorschub | 6 | 6 |
| `extrude_cali` Fluss | 8 | 8 |
| `M620` / `M621` / `M623` AMS | 19 / 4 / 18 | 19 / 4 / 18 |
| `G1 E` Purge | 68 | 68 |

Der Unterschied von 37 Zeichen ist der Datumskommentar im Kopf. Damit ist die
CLI aus einer Projektvorlage **gleichwertig zur grafischen Oberfläche**.

## Arbeitsteilung

| Wer | Was |
|---|---|
| **Bambu Studio (GUI), einmalig** | Vorlagenprojekt: Drucker, Filament, Prozess, Plattentyp, Stützstruktur-Grundeinstellung. Als `.3mf` speichern. |
| **Wir automatisch** | Geometrie erzeugen, gegen die Vorlage slicen, hochladen, starten, überwachen, Objekte überspringen. |

Eine Vorlage je Materialklasse genügt (z. B. `A1_PLA_020.3mf`,
`A1_PETG_020.3mf`). Ändert sich etwas Grundsätzliches, wird die Vorlage in
der Oberfläche angepasst — nicht der Code.

## Die vollständige Kette — nachgewiesen

Ziel (Timo, 14.08.2026): von der in Blender erzeugten Geometrie bis zum
laufenden Drucker ohne Handgriff dazwischen. Alle Glieder in einem Durchlauf
geprüft:

```bash
flatpak run --filesystem=home --command=bambu-studio com.bambulab.BambuStudio \
  --load-settings "prozess_override.json" \
  --arrange 1 --allow-rotations --ensure-on-bed \
  --slice 0 --export-3mf "fertig.gcode.3mf" --outputdir "$PWD" \
  vorlage.3mf  Pilz.stl
```

| Glied | Nachweis |
|---|---|
| Vorlage bringt Bambus Maschinerie mit | Start-G-Code 58 768 Zeichen, `G29` 10×, `M620` 19×, `extrude_cali` 8× |
| Eigene Geometrie wird importiert | `Pilz.stl` erscheint als Objekt |
| Automatisch angeordnet | absichtlich auf (40,40) gelegt → verschoben auf Mitte (51, 143) |
| Stützstruktur vorgebbar | Override `tree(auto)` / `tree_strong` / 25° landet in der Ausgabe |
| Stützen tatsächlich erzeugt | 831 `Support`, 95 `Support transition`, 33 `Support interface` |

**Die Trennlinie**, die dabei einzuhalten ist: Ein *Prozess-Override* setzt
benannte Parameter (Stützart, Schichthöhe, Brim) auf einem echten Bambu-Preset
auf — das ist dasselbe, was die Oberfläche beim Anklicken einer Checkbox tut,
und völlig in Ordnung. Etwas anderes ist es, G-Code-Blöcke oder AMS-Sequenzen
selbst zusammenzusetzen — das bleibt Bambus Sache und kommt über die Vorlage.

Nützliche Schalter fürs Anordnen: `--arrange 1`, `--orient 1` (dreht das Teil
in die günstigste Drucklage), `--allow-rotations`, `--ensure-on-bed`,
`--repetitions`, `--clone-objects`.

## Fallstricke

**Objekte werden stillschweigend verworfen, wenn die Platte voll ist.**
Beim Versuch, 4 Würfel zu einer Vorlage mit 10 Objekten zu legen, kamen nur
9 Objekte durch — ohne Fehlermeldung. Die Vorlage sollte deshalb möglichst
leer sein, und **nach jedem Slicen muss die Objektzahl geprüft werden.**

**Nie der Konfiguration glauben, immer den G-Code zählen.** Zwei Fehlversuche
an einem Abend, beide nur durch Nachzählen gefunden:

1. Betttemperatur 35 statt 65 °C — die CLI fällt ohne Angabe auf
   `curr_bed_type = "Cool Plate"` zurück.
2. Halber Start-G-Code — die langen Blöcke stehen in eigenen
   Template-Presets, die nur die grafische Oberfläche zusammenführt. Ohne
   `M620` zieht der Drucker nie Filament aus dem AMS: die Düse fährt trocken
   über die Platte.

Prüfliste nach jedem CLI-Slice, gegen eine nachweislich funktionierende
Datei.

**Wichtig: nur im Start-Block zählen, nicht über die ganze Datei.** Ein
naives `grep -c` über den kompletten G-Code liefert falsche Zahlen — 6 statt
10 `G29` und 274 statt 19 `M620`, weil `M620` bei jedem Filamentwechsel
wiederkehrt und der Konfigurationsblock am Dateiende eine einzige Riesenzeile
ist. Und die Objektzahl ergibt sich aus den **verschiedenen** IDs, nicht aus
der Zeilenzahl (4 Würfel erzeugen 125 Zeilen mit `unique label id`).

```python
import zipfile, re, collections
g = zipfile.ZipFile("fertig.gcode.3mf").read("Metadata/plate_1.gcode").decode("utf-8","replace")
kopf = g.split("; CHANGE_LAYER")[0]          # nur der Start-Block!

for m in ("G29","M900","M620","M621","M623","extrude_cali","M975","G1 E"):
    print(m, kopf.count(m))                  # erwartet: 10 6 19 4 18 8 10 68
print(re.findall(r"^M190[^\n]*", g, re.M)[:1])          # Betttemperatur
print(re.findall(r"^M109[^\n]*", g, re.M)[:1])          # Duesentemperatur
print("Brim:", "Brim" in collections.Counter(re.findall(r"; FEATURE: ([^\n]+)", g)))
print("Objekte:", len(set(re.findall(r"unique label id:\s*(\d+)", g))))
```

Die Sollwerte 10/6/19/4/18/8/10/68 gelten für den A1 mit 0.4-Düse und sind an
zwei unabhängigen Dateien gegengeprüft (Testplatte mit 4 Objekten und ein
echter Auftrag mit 483 Schichten).

## Mehrfarbdruck über die CLI (gelöst 14.08.2026, Quellcode-verifiziert)

Symptom: Zuweisungen auf Extruder 2 kommen in der Ausgabe an, der G-Code hat
trotzdem null Werkzeugwechsel. **Wurzel (BambuStudio 02.07.01.57):** Die CLI
löst bei `--load-filaments` die `inherits`-Kette der Filamentprofile nicht
auf → `filament_diameter` bleibt einelementig → und **die Filamentanzahl beim
Slicen ist `filament_diameter.size()`**, nicht die Zahl der geladenen
Profile. Extruder > Anzahl wird still auf 1 geklemmt
(`PrintObject.cpp:3203`, „Clamp invalid extruders to the default extruder").

Regeln, alle am Binary nachgemessen:

- **Extruder-Zuweisung auf `<object>`-Ebene** in model_settings.config.
  `<part>`-Zuweisungen werden bei **einteiligen** Objekten gelöscht
  (`bbs_3mf.cpp:2286`); bei mehrteiligen Objekten (Träger + Inlay) bleiben
  sie erhalten — so läuft unser Prototyp.
- **`--load-filament-ids` funktioniert**, aber: Anzahl = Zahl der
  Eingabedateien, nur zusammen mit `--load-filaments`, und die erste
  Eingabedatei darf **keine 3MF** sein (harter Abbruch). Vorlagen-3MF und
  `--load-filament-ids` schließen sich also aus.
- STL-Route braucht zusätzlich `--filament-colour "#FFFFFF;#000000"` und
  **flachgezogene** Filamentprofile (inherits-Kette manuell aufgelöst),
  sonst greift die Diameter-Falle.
- **Unser Weg (läuft):** Platte als eigene 3MF mit
  `<metadata name="BambuStudio:3mfVersion">1</metadata>` im Modell — erst
  dieser Marker macht die Datei zum „Projekt", dessen eingebettete
  `project_settings.config` gelesen wird. Als Settings die **voll
  aufgelöste** Konfiguration aus einem echten GUI-Projekt einbetten (alle
  `filament_*`-Vektoren konsistent lang!). Slicen **ohne** `--export-3mf`
  (der Projekt-Export crasht headless), `plate_1.gcode` fällt trotzdem ins
  Ausgabeverzeichnis; Container selbst packen (Chassis eines früheren
  Exports, G-Code + MD5 + plate_1.json tauschen).
- Die Segfaults (Exit 134/139) kommen von **inkonsistenten
  Filamentvektoren** (`filament_settings_id` / `filament_diameter` /
  `filament_colour` / `filament_map` verschieden lang), nicht vom Export an
  sich. Vor dem Slicen die Längen prüfen.
- `Failed to create GLFW window` steht in **jedem** headless-Lauf, auch in
  erfolgreichen — keine Fehlerursache, nur fehlende Vorschaubilder (OSMesa
  fehlt im Flatpak).

## Sackgassen (geprüft, funktionieren nicht)

- `--load-settings <project_settings.config>` → `unknown config type`.
  Die zusammengeführte Projektkonfiguration hat kein `type`-Feld.
- Getypte System-Presets einzeln laden → Start-G-Code fehlt, weil er in
  separaten `… template …`-Dateien liegt. Führt man die von Hand zusammen,
  funktioniert es zwar (nachgemessen), ist aber genau der Nachbau, den wir
  nicht wollen. Die Dateien `maschine_skiptest.json` /
  `prozess_skiptest.json` / `filament_skiptest.json` in `skiptest/` sind
  dieser Zwischenstand und nur noch Beleg.
- Maschinenprofil umbenennen → `process not compatible with printer`
  (das Prozessprofil prüft `compatible_printers`).
- `"from"` entfernen oder auf `project` lassen → `from … unsupported`.

## Fortsetzen eines abgebrochenen Drucks (15.08.2026)

`druck_journal.py` (Dienst) schreibt laufend mit, wo der Druck steht;
`fortsetzung_bauen.py` baut daraus die Fortsetzung. Feiner als **auf die
Schicht genau** geht es nicht — MQTT meldet `layer_num`, aber nicht die
Position innerhalb einer Schicht. Das ist die Grenze der Schnittstelle.

Drei Fallen, alle am misslungenen ersten Versuch gelernt:

1. **Nicht auf `; CHANGE_LAYER` schneiden.** Der Block beginnt mit dem
   ABSCHLUSS der Vorschicht: WIPE mit rund 2 mm Rueckzug, ein
   `; stop printing object` und ein `M625`, dessen oeffnendes `M624` im
   abgeschnittenen Teil steht. Ergebnis: Duese leer, keine Foerderung.
   Das Werkzeug raeumt diese Zeilen jetzt selbst weg.
2. **Duese laden.** Weil der abgeschnittene Teil mit Rueckzuegen endete,
   muss die Ueberleitung ausdruecklich Filament nachfoerdern (`G1 E3`).
3. **Farbwechsel an der Schnittstelle vermeiden.** Der Wechselablauf setzt
   voraus, dass der Drucker weiss, welches Filament geladen ist — diese
   Vorgeschichte liegt im abgeschnittenen Teil. Das Werkzeug erkennt das
   und schlaegt die naechste Schicht ohne Wechsel vor.

**Sicherheitspruefung vor jedem Bau:** Es liest aus dem G-Code, wo das
Startritual das Bett beruehrt (`G28 Z`-Antastpunkte und jede Materialablage,
beides auf den Druckbereich 0..256 begrenzt) und prueft **punktgenau**, ob
eine davon im Werkstueckbereich liegt. Ein Bounding-Box-Vergleich schlaegt
hier falsch an. Beim A1 liegen Antastpunkte und Purge bei y 254/261 — also
ausserhalb — deshalb darf das komplette Ritual mitlaufen und der Drucker
homet regulaer, statt mit einer geratenen Z-Referenz zu arbeiten.

## Watchdog: Stillstand erkennen

Symptom des misslungenen Fortsetzungsversuchs war `Schicht 0` bei 54 %
Fortschritt — der Zaehler stand, der Drucker meldete RUNNING. Die Begleitung
alarmiert deshalb, wenn sich `layer_num` 8 Minuten nicht bewegt.

## Abbruch-Schutz gegen Fehlalarme

Nach dem ersten Fehlalarm (intakter Druck bei Schicht 4 abgebrochen, weil
0.5 mm flache Teile im flachen Kamerawinkel "verschwanden") verlangt ein
eigenmaechtiger Abbruch drei Bedingungen: beide Stufen einig, mindestens
eine **"hoch"** sicher, und **ab Schicht 12**. Sonst nur Alarm mit Begruendung,
warum nicht abgebrochen wurde.
