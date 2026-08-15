# Testplan: Objekte überspringen am A1

> **ERGEBNIS (14.08.2026, 20:37): FUNKTIONIERT.** Voraussetzung am A1
> (Firmware 01.08.01.00): Nur-LAN-Modus **und** Entwicklermodus. Testlauf 2
> lief vollautonom — Kamera-Freigabe der leeren Platte, Start per
> `druck_starten()` (20:09), `skip_objects` für Würfel 3 (ID 105) bei
> Schicht 6 (20:23:45, Quittung `success`), ab Schicht 8 durchgehend
> `s_obj = [105]`, Druck sauber beendet (FINISH 25/25). Auf der Platte:
> drei volle Würfel, Würfel 3 als flaches Pad von ~6 Schichten mit Brim.
> Alle drei Nachweis-Signale (Quittung, `s_obj`, Kamerabild) positiv;
> keine HMS-Meldung, kein `print_error`, H2/H3 nicht nötig.
> Belege: `belege2/`, Protokoll: `testlauf2.log`.

## Warum überhaupt ein Test

Am 2026-08-14 ist beim Druck `Front_Blende` die um 45° gekippte Frontblende
abgerissen. Die anderen neun Teile liefen sauber weiter — genau der Fall, für
den es das Überspringen gibt. Der Befehl wurde vom Drucker aber **abgelehnt**
(`err_code 0x05024007`), und zwar mit demselben Fehler für beide möglichen
ID-Nummernkreise. Gleicher Fehler bei verschiedenen IDs heißt: es lag nicht an
einer falschen Nummer.

Offen sind danach drei Erklärungen:

1. **Fehlendes Feld.** Die Bambu-Referenz führt ein `timestamp` im Befehl, das
   beim ersten Versuch fehlte.
2. **Mehrfarbendruck.** Der Auftrag lief mit zwei Filamenten und Wischturm.
   Das Wiki weist Mehrfarbe als Sonderfall aus.
3. **A1 kann es nicht.** Das Wiki nennt für die Studio-Oberfläche ausdrücklich
   „A1 series and P1 series: Currently not supported" — die Firmware-Tabelle
   listet den A1 dagegen ab 01.01.00.00 als unterstützt, und für P1/A1 wird
   auf den Bambu Farm Manager verwiesen. Widersprüchlich.

Der Test trennt diese drei sauber: **eine Farbe, kein Wischturm, `timestamp`
gesetzt.** Schlägt es dann immer noch fehl, ist es Erklärung 3.

## Der Prüfkörper

Vier Würfel 14 × 14 × 5 mm nebeneinander auf y = 128, Abstand 28 mm — weit
genug auseinander, dass die Druckerkamera sie einzeln sieht und eine Lücke
sofort auffällt.

| Würfel | Objekt-ID | Bettposition x |
|---|---|---|
| Wuerfel_1 | 83 | 100 |
| Wuerfel_2 | 94 | 128 |
| **Wuerfel_3** | **105** | **156** ← Ziel |
| Wuerfel_4 | 116 | 184 |

25 Schichten à 0,2 mm, PLA, rund 22 Minuten. Erzeugt mit der Bambu-Studio-CLI
(`erzeuge_testplatte.sh`), liegt als `/Skiptest_final.gcode.3mf` auf der
SD-Karte des Druckers.

Die CLI kann headless keine Vorschaubilder rendern (kein OpenGL). Die wurden
deshalb nachgebaut — inklusive `pick_1.png`, in dem Bambu die Objekt-ID als
Farbe kodiert (`id = r + g·256 + b·65536`, 2 px/mm, Y gespiegelt; abgeleitet
und geprüft an der echten `Front_Blende.gcode.3mf`). Damit ist die Datei
strukturgleich zu einer aus der grafischen Oberfläche, und fehlende
Vorschaubilder scheiden als Fehlerquelle aus.

## Fehlversuch 1 (18:30) — Betttemperatur

Der erste Anlauf haftete nicht: `FAILED` bei Schicht 0. Ursache gefunden und
behoben.

**Die Bambu-Studio-CLI fällt ohne Angabe auf `curr_bed_type = "Cool Plate"`
zurück.** Das ergibt für PLA `M190 S35` — 35 statt 65 °C. Bei 30 K zu kaltem
Bett haftet die erste Schicht schlicht nicht. In der grafischen Oberfläche
fällt das nie auf, weil dort der zuletzt gewählte Plattentyp hängt; über die
CLI muss er ausdrücklich gesetzt werden.

Gültige Werte sind nur `"Cool Plate"` und `"Textured PEI Plate"`. Timos Platte
ist die texturierte PEI (am Kamerabild überprüft: dunkel, körnig, mit
eingeprägter Kontur).

Gleich mit korrigiert:

| Wert | vorher | jetzt | warum |
|---|---|---|---|
| `curr_bed_type` | Cool Plate | Textured PEI Plate | 35 → **65 °C** |
| `brim_type` | auto_brim | `outer_only` | `auto_brim` gab den kleinen Würfeln keinen Brim |
| `brim_width` | 0 | 3 mm | 14 mm Kantenlänge ist wenig Haftfläche |
| `nozzle_temperature_initial_layer` | 200 | 215 °C | etwas mehr Reserve für die erste Schicht |

Achtung beim Nachbauen: `brim_type` heißt `outer_only`, **nicht**
`outer_brim_only` — ein ungültiger Wert wird stillschweigend auf `auto_brim`
zurückgesetzt. Immer am erzeugten G-Code gegenprüfen, ob `; FEATURE: Brim`
überhaupt vorkommt.

Die Profile stehen als `prozess_skiptest.json` und `filament_skiptest.json`
im Projekt. Die Objekt-IDs haben sich durch die Neuslicung **nicht** geändert
(83/94/105/116).

## Fehlversuch 2 (19:00) — der halbe Start-G-Code

Bett und Brim stimmten jetzt, aber es kam **überhaupt kein Material** heraus.
Timos Frage („machst Du denn die Flusskalibrierung und den Z-Abgleich?")
traf den Nagel: nein, tat die Datei nicht.

**Bambu legt die langen G-Code-Blöcke nicht ins Maschinenprofil, sondern in
eigene Template-Presets** (`… template machine_start_gcode.json` und vier
weitere). Die grafische Oberfläche führt sie beim Slicen zusammen; gibt man
der CLI nur `Bambu Lab A1 0.4 nozzle.json`, fehlen sie ersatzlos — ohne
Fehlermeldung.

Der Vergleich mit dem echten, funktionierenden Druck zeigte es sofort:

| Marke | vorher | echt | wofür |
|---|---|---|---|
| `G29` | 0 | 10 | Bettnivellierung / Z-Abgleich |
| `M900` | 0 | 6 | Druckvorschub-Kalibrierung |
| `extrude_cali` | 0 | 8 | Flusskalibrierung |
| `M620` / `M621` / `M623` | 0 | 19 / 4 / 18 | **AMS-Filament laden** |
| `G1 E` | 0 | 68 | Purge-Linie |

Die fehlenden `M620`-Befehle sind die eigentliche Ursache: ohne sie zieht der
Drucker nie Filament aus der AMS lite, `tray_now` bleibt auf 255, und die
Düse fährt trocken über die Platte.

**Behebung:** `maschine_skiptest.json` führt die fünf Templates ins
Maschinenprofil zusammen. Danach stimmen alle Marken exakt mit dem echten
Druck überein (10/10, 6/6, 19/19, 4/4, 18/18, 8/8, 68/68).

Zwei Stolpersteine dabei:

- Das Maschinenprofil darf **nicht umbenannt** werden — das Prozessprofil
  prüft `compatible_printers` gegen den Namen (`process not compatible with
  printer`).
- Das Feld `"from": "system"` muss erhalten bleiben, sonst: `file's from
  unsupported`.

**Merksatz für jedes CLI-Slicen:** nie der Konfiguration glauben, sondern den
erzeugten G-Code gegen eine nachweislich funktionierende Datei zählen. Genau
das hat hier beide Fehler gefunden.

## Ablauf

1. Druckplatte frei räumen, PLA weiß (AMS-Fach 0) bereit.
2. Druck am Druckerbildschirm starten: `Skiptest_final.gcode.3mf`.
   *(Bewusst von Hand — das Starten eines Drucks bleibt eine körperliche
   Handlung am Gerät, nicht etwas, das ein Skript nebenbei auslöst.)*
3. Warten bis etwa Schicht 6. Alle vier Würfel wachsen gleich hoch.
4. `./skip.sh --skip Wuerfel_3`
5. Beobachten: `s_obj`, Schichtzähler, Kamerabild.

## Auflösung (14.08., 20:15) — es war der Entwicklermodus

Der erste Testdruck lief durch, der Skip bei Schicht 6 wurde mit
`err_code 84033543 (0x05024007)` abgelehnt — **aber keine der drei
Erklärungen oben stimmte.** Die Ursache lag eine Ebene tiefer:

**Der gesamte `print`-Namensraum war gesperrt, weil am Drucker der
Entwicklermodus aus war.** Beweiskette:

1. Auch ein folgenloses `print.print_speed` (Stufe 2 → 2) und ein harmloses
   `gcode_line M400` gaben denselben Fehler — es lag also nie am
   Skip-Befehl selbst.
2. `system.ledctrl` (Kammerlicht) wurde gleichzeitig mit `result: success`
   ausgeführt — nur die Druck-Ebene war zu, nicht die Befehlsannahme.
3. Nach dem Einschalten des Entwicklermodus (Nur-LAN-Modus war schon an),
   ohne jede weitere Änderung: `print.print_speed` → **success**,
   `skip_objects` im Leerlauf → **success**. Der Zugangscode blieb gleich.

Die Wiki-Seite zum Überspringen erwähnt den Entwicklermodus mit keinem
Wort — ihre Zeile „A1 series: Currently not supported" betrifft nur den
Bedienweg über die Studio-Oberfläche, nicht die Firmware. Für Fremdsoftware
gilt am A1 (Firmware 01.08.01.00): **Nur-LAN-Modus + Entwicklermodus,
sonst ist jede Drucksteuerung per MQTT gesperrt.** Lesen (Status, Kamera,
FTPS) ging auch ohne.

**Noch offen ist einzig der Wirk-Nachweis:** dass ein übersprungenes Objekt
auf einem *laufenden* Druck tatsächlich stehen bleibt. Dafür den Testdruck
wiederholen (Platte freiräumen, `Skiptest_final.gcode.3mf` liegt auf der
SD-Karte) — diesmal kann auch der Start selbst über `druck_starten()`
erfolgen, denn der hängt an derselben Freischaltung.

## Unvoreingenommene Gegenprüfung (14.08., 20:30) — mit offiziellen Quellen

Berechtigter Einwand von Timo: Die Skip-Wiki-Seite erwähnt keinen
Entwicklermodus — also Vorsicht vor der eigenen Hypothese. Die Gegenprüfung
ergab: Die Sperre ist offiziell dokumentiert, nur auf einer **anderen**
Wiki-Seite (`software/third-party-integration`):

- Bambu führt eine „printer control authorization" ein, „first on X-series,
  followed by P-series and A-series in future firmware updates". Betroffen
  sind u. a. „starting a print job from other software" und „controlling
  printer functions such as movement, temperature, fans".
- Ausdrücklich **nicht** betroffen: „sending printer status updates (MQTT
  status pushes for tools like Home Assistant)" — deckt sich exakt mit der
  Messung (Lesen ging immer, Steuern nie).
- Developer Mode: „does not require authorization verification … third-party
  software will continue to work without any modifications", nur unter
  LAN-Modus möglich.
- Die HMS-Meldung `0500_0500_0001_0007`, die seit 07.08. auf dem Drucker
  steht, heißt offiziell: **„MQTT command verification failed, please update
  Studio or Handy."** Dieselbe Fehlerfamilie, Tage vor unseren Versuchen.

Damit tragen drei unabhängige Belege dieselbe Erklärung: das A/B-Experiment
am Gerät, die offizielle Doku, und die vorbestehende HMS-Meldung.

## Gefahrenanalyse — kann der Skip-Test den Drucker beschädigen?

Oberstes Gut: der Drucker. Deshalb vor dem Test die Risiken einzeln:

| Vektor | Bewertung |
|---|---|
| `skip_objects` selbst | Bambus eigene Funktion, von X1-Bildschirm und Farm Manager genutzt. Mechanik laut Wiki: die Firmware lässt die G-Code-Blöcke des Objekts aus, Leerfahrten bleiben. Das übersprungene Objekt ist danach **niedriger** als die aktuelle Schicht — der Abstand zur Düse wächst, ein Aufprall wird unwahrscheinlicher, nicht wahrscheinlicher. |
| Temperaturen / Achsgrenzen | Werden von `skip_objects` nicht berührt; alle Firmware-Schutzmechanismen (Thermoüberwachung, Motorstall-Erkennung) bleiben aktiv. |
| Roher G-Code während des Drucks (`gcode_line` mit G0/G1/G28/M211/M624) | **Das einzige echte Risiko** — eine Bewegungsanweisung mitten im Druckstrom könnte die Düse ins Werkstück oder Bett fahren. **Wird kategorisch nicht gemacht.** |
| Schlimmster realistischer Ausgang | Ein ruinierter 30-Minuten-Opferdruck (vier Würfel) oder ein pausierter Auftrag. Genau dafür ist es ein Opferdruck. |
| Letzte Instanz | Timo am Druckerbildschirm (Stopp-Taste); die Überwachung schlägt bei HMS/`print_error` sofort Alarm, stoppt aber nie selbst. |

## Hypothesenleiter für den Testdruck (nach Risiko geordnet)

- **H1 (primär):** Offizielles `skip_objects` für Würfel 3 (ID 105) bei
  Schicht 6. Nachweis über drei unabhängige Signale: Quittung, `s_obj` im
  Statusbericht, Kamerabilder bei Schicht 8/12/16/20/25 (Würfel 3 muss flach
  bleiben, 1/2/4 wachsen weiter).
- **H2 (falls quittiert, aber wirkungslos):** Denselben Befehl **einmal**
  bei ~Schicht 12 wiederholen — mehr nicht.
- **H3 (falls weiter wirkungslos):** Als Ergebnis dokumentieren („Firmware
  quittiert, setzt nicht um"), keine weiteren Experimente am laufenden
  Druck. Rückfallstrategie für echte Drucke ist dann das **Neu-Slicen ohne
  das defekte Teil** über die CLI-Kette — heute belegt, dauert Minuten und
  ist risikofrei.
- **Ausdrücklich unterlassen:** jede `gcode_line`-Injektion während des
  Drucks, jede Pause/Stopp-Automatik, jeder Temperatur- oder Achsbefehl.

## Was welches Ergebnis bedeutet

| Beobachtung | Schluss |
|---|---|
| Befehl angenommen, `s_obj = [105]`, Würfel 3 bleibt flach | **Funktioniert.** Der erste Fehlschlag lag am `timestamp` oder an der Mehrfarbigkeit. |
| Befehl angenommen, `s_obj = [105]`, Würfel 3 wächst weiter | Firmware quittiert, setzt aber nicht um. Sackgasse über MQTT. |
| Wieder `err_code 0x05024007` | Der A1 nimmt den Befehl grundsätzlich nicht an. Dann bleibt nur der Bambu Farm Manager — der aber über die Cloud läuft, und Aufträge von SD-Karte haben dort kein Vorschaubild. Realistisch heißt das: am A1 nicht verfügbar. |

Das Ergebnis gehört in dieses Dokument und in `PLAN-GEWISSHEIT.md`.

## Wenn es funktioniert: Einbau

Die Bedienteile stehen schon:

```bash
./skip.sh --liste                # was liegt auf der Platte, was ist schon raus
./skip.sh --skip Front_Blende    # nach Name, Teilstück genügt
./skip.sh --skip 105             # nach ID
```

`a1_befehle.py` holt dafür die laufende 3MF vom Drucker, liest die
Objektklammern aus dem G-Code (`; start printing object, unique label id: N`)
und ordnet sie über die Extrusionskoordinaten den Bauteilnamen zu — an der
echten Datei auf 0,8–3 mm genau.

Als Nächstes wäre dann sinnvoll:

- **Alarmtext der Wache erweitert** um die Objektliste des laufenden Auftrags
  plus den fertigen `./skip.sh --skip …`-Befehl. Dann steht in der Meldung
  nachts um drei direkt, was zu tun ist.
- **Welches Objekt betroffen ist**, kann die Wache noch nicht sagen — die
  Kamera schaut flach von vorn, eine Zuordnung Bildstelle → Bauteil ist damit
  unsicher. Ehrlicher ist, die Liste anzubieten und den Menschen wählen zu
  lassen.

Was bewusst **nicht** gebaut wird: automatisches Überspringen durch die Wache.
Ein Fehlurteil würde sonst ein gutes Teil verwerfen, und das ist schlimmer als
ein verpasster Alarm. Die Wache meldet, entschieden wird von Hand — dieselbe
Linie wie beim Nie-den-Druck-stoppen.

## Trennung der Zuständigkeiten

`a1_drucker.py` bleibt **nur lesend** und enthält keinen einzigen ändernden
Befehl; die Spaghetti-Wache importiert ausschließlich dieses Modul. Alles
Schreibende steht in `a1_befehle.py`, das die Wache nicht anfasst. Auch dort
fehlen Starten, Pausieren und Abbrechen bewusst — für diese Eingriffe gibt es
den Druckerbildschirm.
