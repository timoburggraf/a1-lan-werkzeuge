# Plan: die Gewissheit der Alarme verbessern

Ein Wächter hat zwei Arten, falsch zu liegen. Er kann Alarm schlagen, wo
nichts ist — dann glaubt man ihm bald nicht mehr. Und er kann einen echten
Fehldruck übersehen — dann war er umsonst. Beide Fehler haben verschiedene
Ursachen und brauchen verschiedene Gegenmittel.

Dieser Plan ordnet die Gegenmittel nach Aufwand. Stufe 0 steht, Stufe 1 läuft
seit heute mit, 2 bis 6 sind beschrieben und noch nicht gebaut.

---

## Die ehrliche Ausgangslage

**Wie gut die Wache urteilt, ist derzeit unbekannt.** Sie hat noch nie einen
echten Fehldruck gesehen. Alles unten ist begründete Konstruktion, keine
gemessene Trefferquote — und das bleibt so, bis Belege vorliegen. Genau
deshalb ist Stufe 1 die wichtigste: sie ist die Voraussetzung dafür, dass sich
über die Qualität überhaupt etwas *wissen* lässt statt nur behaupten.

Und eine Grenze löst keine Software: **die A1-Kamera sitzt vorn unten und
sieht das Bett fast von der Seite.** Mehrere Bilder je Prüfung und der
Zuschnitt mildern das stark, aber der flache Blickwinkel bleibt. Wer diese
Grenze wirklich aufheben will, braucht eine zweite Kamera über dem Bett —
bewusst ausgeklammert.

### Gemessen: der Zuschnitt (2026-08-14, Schicht 16/483)

Dieselben drei Bilder, derselbe Prompt, derselbe Druckerzustand — nur einmal
als Vollbild und einmal zugeschnitten:

| | Urteil | Bildtoken |
|---|---|---|
| Vollbild 1024 × 720 | `sicht_verdeckt`, Sicherheit hoch | 4538 |
| Ausschnitt 1414 × 496 | `ok`, mit zutreffender Beschreibung | **4406** |

Im Vollbild sind gut 55 % der Fläche Druckergehäuse und der linke Rand
Regalhintergrund. Der Zuschnitt wirft beides weg. Das Werkstück erscheint rund
2,5-mal so groß — **und kostet dabei weniger Token als vorher.** Ein Zuschnitt
ist damit in jeder Hinsicht besser als das Vollbild; es gibt keinen Grund,
jemals wieder das ganze Bild zu schicken.

Die Grenzen des Rahmens sind bewusst asymmetrisch: nach oben bis zum Bildrand,
weil das Werkstück im Bild nach oben wächst (beim A1 fährt der Kopf in Z, das
Bett nur in Y). Nach unten bis 46 %, damit auch herunterhängendes Material
noch zu sehen ist. Ob der Rahmen für ein 96 mm hohes Teil noch trägt, prüfen
die Nachschau-Runden am laufenden Druck.

---

## Stufe 0 — umgesetzt

Was heute schon läuft, und warum es hilft:

| Maßnahme | Gegen welchen Fehler |
|---|---|
| **Torwächter Druckerstatus.** Läuft laut MQTT kein Druck, wird gar nicht erst geurteilt. | Fehlalarm. Der größte Einzelposten: die meisten Fehlurteile entstanden an leeren oder fertigen Betten. Kostet zusätzlich nichts, spart sogar API-Aufrufe. |
| **Kontext mitgeben.** Schicht 12 von 483, 14 %, Solltemperaturen. | Beides. Ein flaches Etwas auf dem Bett ist bei Schicht 12 normal und bei Schicht 400 ein abgerissenes Teil. Ohne diese Angabe kann das kein Modell auseinanderhalten. |
| **Drei Bilder statt einem**, fünf Sekunden auseinander. | Übersehen. Der A1 schiebt das Bett in Y; jede Stellung gibt einen anderen Blick. Was auf einem Bild verdeckt ist, liegt auf dem nächsten frei. |
| **Zuschnitt auf die Druckplatte** (Idee von Timo), `KONFIG["ausschnitt"]`. | Beides — und zwar deutlich. Siehe Messung unten. |
| **Kamerageometrie im Prompt erklärt.** | Fehlalarm. Ohne den Absatz hielt das Modell das eigene Druckergehäuse für ein Hindernis und meldete verlässlich „Sicht verdeckt" — der Befund, der die erste Fassung wertlos machte. |
| **Gegenprobe.** Bei Verdacht werden *frische* Bilder geholt und mit umgekehrter Fragestellung neu beurteilt: suche die harmlose Erklärung, im Zweifel „ok". Nur was das übersteht, alarmiert. | Fehlalarm. Der eigentliche Gewinn an Gewissheit. Kostet nur dann etwas, wenn tatsächlich ein Verdacht besteht — also selten. |
| **Zweimal entkräftet zählt trotzdem.** Schlägt die erste Beurteilung zweimal hintereinander an und wird jeweils entkräftet, wird gemeldet. | Übersehen. Verhindert, dass die Gegenprobe einen echten, aber schwer sichtbaren Fehler dauerhaft wegbügelt. |
| **Schichtzähler.** Steht er trotz „RUNNING" drei Läufe (30 min) still, geht das als Hinweis in die Beurteilung. | Übersehen. Fängt Fehler, bei denen sich nichts mehr bewegt — die sieht man einem Standbild nicht an. |
| **HMS und `print_error` durchreichen.** | Übersehen. Der Drucker diagnostiziert sich selbst mit; das ist ein völlig unabhängiges Signal, das nichts kostet. |
| **Standby-Sperre**, solange gedruckt wird. | Übersehen. Ein schlafender Laptop prüft nichts. |

---

## Stufe 1 — Belege sammeln *(läuft seit heute)*

Jeder Lauf legt ein verkleinertes Bild in `verlauf/` ab, mit Datum, Urteil und
Schichtnummer im Dateinamen, und wirft nach 14 Tagen weg. Rund 18 kB je Lauf,
also etwa 4 MB pro Drucktag.

Das ist die Grundlage für alles Weitere. Irgendwann geht ein Druck schief —
dann liegen erstmals Bilder eines echten Fehldrucks vor, samt der Bilder aus
den Läufen davor. Damit lässt sich rückblickend prüfen: *Wann hätte man es
sehen können? Hat die Wache es gesehen? Beim wievielten Lauf?*

**Zu tun, wenn es soweit ist:** die Bilder aus `verlauf/` und `befunde/` in
einen Ordner `belege/` sortieren, je Bild ein Urteil danebenlegen
(`ok` / `verdacht` / `spaghetti`). Zwanzig bis dreißig Bilder genügen für
einen ersten aussagekräftigen Durchgang.

---

## Stufe 2 — Testreihe

Ein Skript `pruefe_wache.py`, das die gesammelten Belege durch dieselbe
Beurteilung schickt wie der Ernstfall und auszählt, wie oft sie richtig lag —
getrennt nach „Fehlalarm" und „übersehen".

Damit werden aus Meinungen Messwerte, und drei Fragen endlich beantwortbar:

- Reicht **Haiku** statt Opus? Das ist eine Kostenfrage von Faktor fünf, und
  im Moment ist sie schlicht nicht beantwortbar. Mit einer Testreihe wird sie
  es.
- Bringen drei Bilder wirklich mehr als eines, oder zahlt man die Token umsonst?
- Hilft eine Formulierung im Prompt, oder fühlt sie sich nur besser an?

Ohne diese Stufe ist jede weitere Prompt-Änderung Raten.

---

## Stufe 3 — Zeitvergleich

**Vermutlich der stärkste verbleibende Hebel.** Ein Fehldruck ist eine
*Veränderung*: gestern Abend war da ein sauberer Turm, jetzt ein Knäuel. Ein
Einzelbild zwingt das Modell, aus dem Aussehen allein zu schließen — und
genau da entstehen die Fehlurteile.

Da Stufe 1 die Bilder ohnehin aufhebt, kostet der Umbau fast nichts: dem
Aufruf zusätzlich das Bild aus dem letzten Lauf und dessen Schichtnummer
mitgeben, mit der Frage „was hat sich in diesen zehn Minuten verändert, und
ist die Veränderung plausibel?". Bei ~30 Schichten Fortschritt muss das Objekt
sichtbar gewachsen sein; ist es plötzlich kleiner oder unförmiger, ist das ein
harter Befund statt einer Geschmacksfrage.

---

## Stufe 4 — Mehrheitsentscheid statt Zweikampf

Heute urteilen zwei Instanzen: Beurteilung und Gegenprobe. Bei Uneinigkeit
gewinnt die Gegenprobe. Sauberer wäre bei Verdacht ein Dreiergremium mit
verschiedenen Blickwinkeln — einer prüft auf Fadenwirrwarr, einer auf Haftung
am Bett, einer auf Formtreue gegen die Schichtzahl — und zwei von dreien
entscheiden.

Kostet nur im Verdachtsfall, also selten. Sinnvoll erst nach Stufe 2: ohne
Messung weiß man nicht, ob der Zweikampf überhaupt das schwache Glied ist.

---

## Stufe 5 — die eigene Erkennung des Druckers mitlesen

Der A1 bringt eine Erstschichtprüfung mit, neuere Firmware zusätzlich eine
Spaghetti-Erkennung. Der Zustand steht im MQTT-Feld `xcam`; derzeit meldet der
Drucker dort nur `buildplate_marker_detector: true` — die weitergehende
Erkennung ist also aus oder auf dieser Firmware nicht vorhanden.

**Zu prüfen:** ob sie sich in Bambu Studio unter den Geräteeinstellungen
einschalten lässt. Wenn ja, ist ihr Urteil ein zweiter, völlig unabhängiger
Sensor — anderer Hersteller, andere Methode, gleiche Frage. Zwei unabhängige
Quellen, die sich einig sind, sind erheblich mehr wert als eine sehr gute.

---

## Stufe 6 — Takt an die Gefahr koppeln

Zehn Minuten, immer gleich, ist eine grobe Näherung. Fehldrucke verteilen sich
aber nicht gleichmäßig: die meisten entstehen in den ersten Schichten
(Haftung) oder direkt nach einem Filamentwechsel. Die langen Stunden in der
Mitte eines gut laufenden Drucks sind vergleichsweise harmlos.

Die Schichtnummer liegt vor, also ließe sich der Takt danach richten: alle
drei Minuten während der ersten zehn Schichten, sonst alle fünfzehn. Gleiche
Kosten, bessere Abdeckung dort, wo tatsächlich etwas passiert.

---

## Reihenfolge

1. **Warten**, bis `verlauf/` echte Fehldrucke enthält — passiert von allein.
2. Dann **Stufe 2** (Testreihe), damit alles Weitere messbar wird.
3. Dann **Stufe 3** (Zeitvergleich) — größter Effekt fürs Geld.
4. **Stufe 5** (Bambu-Erkennung) kann jederzeit dazwischen, ist nur ein Blick
   in die Studio-Einstellungen.
5. Stufe 4 und 6 nur, wenn die Messung sagt, dass es sich lohnt.
