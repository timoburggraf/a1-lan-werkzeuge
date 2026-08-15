#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spaghetti-Wache — Druckueberwachung direkt am Bambu Lab A1
==========================================================

Holt in festem Takt Kamerabilder und Zustand direkt vom Drucker (LAN, keine
Cloud, kein Bildschirmabgriff), laesst die Bilder von Claude beurteilen und
schlaegt bei Druckfehlern Alarm.

**Diese Wache greift nicht in den Druck ein.** Sie meldet nur — ausdrueckliche
Vorgabe Timo: bei Spaghetti erst einmal nur Alarm, kein Abbruch, weil es auch
eine Kleinigkeit sein kann. Das Modul a1_drucker.py kennt keinen Befehl, der
etwas am Drucker aendert.

Ablauf je Lauf:
  1. Druckerzustand per MQTT holen
  2. Torwaechter: laeuft ueberhaupt ein Druck? Wenn nein, keine Beurteilung,
     kein Alarm, keine API-Kosten
  3. Meldet der Drucker selbst einen Fehler (HMS/print_error), wird der
     durchgereicht
  4. Schichtzaehler gegen den letzten Lauf pruefen — steht er trotz "RUNNING",
     haengt etwas
  5. Drei Kamerabilder ueber ein paar Sekunden holen (das Bett faehrt, jede
     Stellung zeigt etwas anderes)
  6. Beurteilung durch Claude, mit dem Druckerzustand als Zusammenhang
  7. Bei Befund: GEGENPROBE mit frischen Bildern und skeptischer Fragestellung.
     Nur was die Gegenprobe bestaetigt, loest Alarm aus.
  8. Alarm: Desktop-Meldung + Ton, Bilder zur Nachschau abgelegt

Aufruf:
    python3 spaghetti_wache.py            # ein Lauf
    python3 spaghetti_wache.py --status   # nur Druckerzustand zeigen
    python3 spaghetti_wache.py --zeigen   # nur Bilder holen und ablegen
    python3 spaghetti_wache.py --testton  # Alarm einmal vorfuehren
"""

import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from PIL import Image

import a1_drucker
import melde_helfer

# ---------------------------------------------------------------------------
#  Konfiguration
# ---------------------------------------------------------------------------
KONFIG = {
    # Modell. Eine Zeile — wer die laufenden Kosten druecken will, traegt hier
    # "claude-haiku-4-5" ein; die Aufgabe ist einfach genug dafuer.
    "modell": "claude-opus-5",

    # Bilder je Beurteilung und Pause dazwischen in Sekunden. Mehr Bilder
    # heisst mehr Blickwinkel aufs Werkstueck, weil das Bett zwischendurch
    # faehrt — und deutlich weniger Fehlurteile als bei einem Einzelbild.
    "bilder": 4,
    "bild_abstand": 7,

    # Ausschnitt als Anteil des Vollbilds (links, oben, rechts, unten).
    # Die Kamera des A1 sitzt vorn unten; die unteren ~55 % des Bildes zeigen
    # nur das Druckergehaeuse, links steht der Hintergrund des Regals. Uebrig
    # bleibt das Band mit Druckplatte und Werkstueck. Nach unten grosszuegig
    # geschnitten, damit auch ein hohes Objekt noch ganz drin ist — es waechst
    # im Bild nach OBEN, weil beim A1 der Kopf in Z faehrt und das Bett nur
    # in Y. Auf None setzen schaltet den Zuschnitt ab.
    "ausschnitt": (0.08, 0.00, 1.00, 0.46),

    # Laengste Bildkante NACH dem Zuschnitt (Kamera liefert 1536x1080).
    # Der Ausschnitt ist von sich aus schon klein genug — nicht kuenstlich
    # verkleinern, sonst ist die gewonnene Schaerfe wieder weg.
    "bild_kante": 1440,

    # Schichtzaehler steht trotz "RUNNING" seit N Laeufen -> Hinweis.
    "schicht_steht_ab": 3,

    # Belegsammlung: je Lauf ein verkleinertes Bild behalten, aelteres
    # wegwerfen. Ohne solche Belege laesst sich nie messen, wie gut die Wache
    # wirklich urteilt — siehe PLAN-GEWISSHEIT.md, Stufe 1.
    "verlauf_tage": 14,
    "verlauf_kante": 700,

    # Alarm
    "ton": "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "ton_anzahl": 3,              # Wiederholungen je Alarm
    "ton_pause_minuten": 20,      # bei Dauerbefund hoechstens alle N Minuten
}

BASIS = os.path.dirname(os.path.abspath(__file__))
ZUSTAND = os.path.join(BASIS, "zustand.json")
PROTOKOLL = os.path.join(BASIS, "protokoll.jsonl")
BEFUNDE = os.path.join(BASIS, "befunde")
VERLAUF = os.path.join(BASIS, "verlauf")

ALARM_LAGEN = ("spaghetti", "verdacht", "nichts_haftet")

# Nur DIESE Lagen rechtfertigen einen eigenmaechtigen Abbruch — und nur, wenn
# beide Stufen (Beurteilung UND Gegenprobe) sie bestaetigen. Ein blosser
# "verdacht" wird nie abgebrochen: Vorgabe Timo, das kann eine Kleinigkeit
# sein. Regelaenderung vom 15.08.2026, Begruendung in a1_befehle.druck_stoppen.
ABBRUCH_LAGEN = ("spaghetti", "nichts_haftet")

# Schutz gegen Fehlabbrueche (15.08.2026, nach dem ersten Fehlalarm: ein
# intakter Druck wurde bei Schicht 4 abgebrochen, weil 0.5 mm flache
# Plaettchen im flachen Kamerawinkel "verschwanden"). Ein Abbruch verlangt
# deshalb ZUSAETZLICH:
#   - mindestens eine der beiden Stufen muss sich "hoch" sicher sein
#   - das Werkstueck muss hoch genug sein, um ueberhaupt sicher erkennbar
#     zu sein; darunter wird nur alarmiert
ABBRUCH_MIN_SCHICHT = 12
ABBRUCH_BRAUCHT_HOCH = True

# ---------------------------------------------------------------------------
#  Prompts
# ---------------------------------------------------------------------------
#  Der Absatz zur Kamerageometrie ist nicht schmueckendes Beiwerk: ohne ihn
#  haelt das Modell das eigene Druckergehaeuse im Vordergrund fuer ein
#  Hindernis und meldet dauernd "Sicht verdeckt".
KAMERA_ERKLAERUNG = """\
Zur Kamera: Beim Bambu Lab A1 sitzt sie vorn unten und blickt flach ueber das
Druckbett. Du siehst also nicht von oben auf das Werkstueck, sondern fast von
der Seite. Die Bilder sind bereits auf den Bereich zugeschnitten, in dem
Druckplatte und Werkstueck liegen; unten ragt teilweise noch das helle,
gewoelbte Druckergehaeuse ins Bild — das ist der Drucker selbst, voellig
normal und KEIN Hindernis. Die dunkle, koernige Flaeche ist die Druckplatte.

Der A1 ist ein Bettschubser: das Bett faehrt in der Tiefe vor und zurueck,
deshalb zeigen die Bilder das Werkstueck aus etwas unterschiedlichen Winkeln
und Abstaenden — auf einem Bild kann verdeckt sein, was auf dem naechsten frei
liegt. Bewegungsunschaerfe und der orange Schein der Kammerbeleuchtung sind
normal.
"""

SYSTEMPROMPT = """\
Du beurteilst Standbilder aus der eingebauten Kamera eines FDM-3D-Druckers
(Bambu Lab A1). Du bekommst mehrere Bilder desselben laufenden Drucks, wenige
Sekunden auseinander, dazu den vom Drucker gemeldeten Zustand.

Deine einzige Aufgabe: erkennen, ob der Druck sichtbar fehlgeschlagen ist.

%s
Lagen:
  "ok"              Du SIEHST das Werkstueck und es ist in Ordnung.
                    "ok" braucht einen positiven Befund. Wenn du das
                    Werkstueck auf keinem Bild wirklich beurteilen kannst,
                    ist das NICHT "ok", sondern "sicht_verdeckt".
  "verdacht"        Etwas stimmt moeglicherweise nicht, ist aber nicht
                    eindeutig: lose Faeden, sich hebende Ecke (Warping),
                    auffaellige Verformung, Materialklumpen am Duesenkopf.
  "spaghetti"       Eindeutiger Fehldruck: Fadenwirrwarr ueber dem Bett, das
                    Teil hat sich geloest und wird herumgeschoben, das Modell
                    ist erkennbar zerstoert, oder der Kopf zieht Material
                    frei durch die Luft.
  "nichts_haftet"   Der SCHWERSTE Fall: auf dem Bett liegt praktisch nichts
                    (mehr), obwohl der Drucker laut Zustand mitten im Druck
                    ist — alles hat sich geloest, oder die Duese faehrt leer
                    ueber eine blanke Platte. Auch dann, wenn nur noch lose
                    Fetzen herumliegen, wo ein Werkstueck stehen muesste.
  "sicht_verdeckt"  Das Druckbett ist auf KEINEM der Bilder zu beurteilen:
                    voellig dunkel, voellig unscharf, oder ein fremder
                    Gegenstand (nicht der Drucker) steht davor.

Wichtig:
- Nutze den gemeldeten Zustand. Bei niedriger Schichtzahl ist das Werkstueck
  noch flach und unscheinbar — das ist dann kein Fehler, sondern der normale
  Anfang. Bei hoher Schichtzahl muss ein entsprechend hohes Objekt zu sehen
  sein.
- Melde "spaghetti" nur, wenn du es wirklich siehst. Stringing-Faeden, eine
  raue Oberflaeche oder das Stuetzmaterial sind kein Fehldruck.
- Genuegt EIN Bild zur Beurteilung, reicht das — die anderen muessen nicht
  auch etwas zeigen.
- Das Druckergehaeuse im Vordergrund allein ist kein verdecktes Bild. Faehrt
  das Bett aber so weit nach vorn, dass das Werkstueck auf allen Bildern
  hinter dem Gehaeuse verschwindet, dann IST die Sicht verdeckt — sag das,
  statt auf gut Glueck "ok" zu melden. Ein falsches "ok" ist der teuerste
  Fehler, den du machen kannst: dann laeuft ein Fehldruck stundenlang weiter.
- Die Begruendung ist ein einziger kurzer deutscher Satz.
""" % KAMERA_ERKLAERUNG

# Gegenprobe: bewusst gegen den ersten Befund gebuerstet. Ein Alarm soll nur
# ueberleben, wenn er auch dieser Fragestellung standhaelt.
GEGENPROBE_PROMPT = """\
Du pruefst einen Verdacht auf Druckfehler an einem Bambu Lab A1 nach. Eine
erste Beurteilung hat angeschlagen; du bekommst FRISCHE Bilder desselben
Drucks und sollst pruefen, ob der Verdacht standhaelt.

%s
Deine Haltung ist skeptisch: Suche aktiv nach der harmlosen Erklaerung.
Haeufige Fehlalarme sind Stuetzmaterial, der Purge-/Wischturm neben dem Teil,
abgestreiftes Material am Bettrand, Bewegungsunschaerfe, Spiegelungen auf
glaenzendem Filament, Schatten und das Druckergehaeuse im Vordergrund.

Antworte mit der Lage, die du auf DIESEN Bildern siehst:
  "ok"              Der Verdacht ist entkraeftet, der Druck laeuft normal.
  "verdacht"        Etwas stimmt moeglicherweise nicht, aber nicht eindeutig.
  "spaghetti"       Eindeutiger Fehldruck, klar sichtbar.
  "nichts_haftet"   Auf dem Bett liegt praktisch nichts mehr, obwohl gedruckt
                    wird — alles abgeloest.
  "sicht_verdeckt"  Auf diesen Bildern ist das Bett nicht zu beurteilen.

Im Zweifel "ok". Ein Fehlalarm um drei Uhr nachts kostet Vertrauen; einen
echten Fehldruck faengt der naechste Lauf in zehn Minuten ohnehin wieder ein.
Die Begruendung ist ein einziger kurzer deutscher Satz.
""" % KAMERA_ERKLAERUNG

SCHEMA = {
    "type": "object",
    "properties": {
        "lage": {
            "type": "string",
            "enum": ["ok", "verdacht", "spaghetti", "nichts_haftet",
                     "sicht_verdeckt"],
        },
        "sicherheit": {
            "type": "string",
            "enum": ["niedrig", "mittel", "hoch"],
        },
        "begruendung": {"type": "string"},
    },
    "required": ["lage", "sicherheit", "begruendung"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
#  Bilder
# ---------------------------------------------------------------------------
def zuschneiden(bild, anteile):
    """Auf den Bereich zuschneiden, in dem Druckplatte und Werkstueck liegen.

    Bringt doppelt: das Modell sieht das Werkstueck rund 2,5-mal so gross,
    und der Ausschnitt kostet trotzdem weniger Bildtoken als das Vollbild.
    """
    if not anteile:
        return bild
    b, h = bild.size
    l, o, r, u = anteile
    kasten = (int(b * l), int(h * o), int(b * r), int(h * u))
    if kasten[2] - kasten[0] < 32 or kasten[3] - kasten[1] < 32:
        return bild            # unsinnig parametriert, lieber alles behalten
    return bild.crop(kasten)


def aufbereiten(jpeg_bytes):
    """JPEG-Bytes -> zugeschnittenes, ggf. verkleinertes PIL-Bild."""
    bild = zuschneiden(Image.open(io.BytesIO(jpeg_bytes)).convert("RGB"),
                       KONFIG["ausschnitt"])
    b, h = bild.size
    kante = KONFIG["bild_kante"]
    if kante and max(b, h) > kante:
        f = kante / float(max(b, h))
        bild = bild.resize((int(b * f), int(h * f)), Image.LANCZOS)
    return bild


def bilder_holen():
    roh = a1_drucker.bilder_holen(KONFIG["bilder"], KONFIG["bild_abstand"])
    return [aufbereiten(b) for b in roh]


def _bildblock(bild):
    puffer = io.BytesIO()
    bild.save(puffer, format="JPEG", quality=88)
    daten = base64.standard_b64encode(puffer.getvalue()).decode("ascii")
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": daten}}


# ---------------------------------------------------------------------------
#  Beurteilung
# ---------------------------------------------------------------------------
def zustand_text(z, hinweise):
    zeilen = ["Vom Drucker gemeldeter Zustand:",
              "  Status:  %s" % z.get("gcode_state", "?"),
              "  Auftrag: %s" % z.get("subtask_name", "?"),
              "  Schicht: %s von %s" % (z.get("layer_num", "?"),
                                        z.get("total_layer_num", "?")),
              "  Fortschritt: %s%%" % z.get("mc_percent", "?"),
              "  Duese %.0f/%.0f C, Bett %.0f/%.0f C" % (
                  z.get("nozzle_temper", 0), z.get("nozzle_target_temper", 0),
                  z.get("bed_temper", 0), z.get("bed_target_temper", 0))]
    if z.get("hms_codes"):
        zeilen.append("  Drucker meldet HMS: %s" % ", ".join(z["hms_codes"]))
    for h in hinweise:
        zeilen.append("  Hinweis: %s" % h)
    return "\n".join(zeilen)


def fragen(bilder, systemprompt, text, modell):
    """Bilder + Text an Claude schicken. Rueckgabe: dict nach SCHEMA."""
    import anthropic

    inhalt = [_bildblock(b) for b in bilder]
    inhalt.append({"type": "text", "text": text})

    antwort = anthropic.Anthropic().messages.create(
        model=modell,
        max_tokens=2000,
        system=systemprompt,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": inhalt}],
    )
    if antwort.stop_reason == "refusal":
        raise RuntimeError("Anfrage abgelehnt (stop_reason=refusal)")
    ergebnis = json.loads(next(b.text for b in antwort.content
                               if b.type == "text"))
    ergebnis["_tokens"] = {"ein": antwort.usage.input_tokens,
                           "aus": antwort.usage.output_tokens}
    return ergebnis


def beurteilen(bilder, z, hinweise):
    return fragen(bilder, SYSTEMPROMPT,
                  "%s\n\nBeurteile diese %d Kamerabilder."
                  % (zustand_text(z, hinweise), len(bilder)),
                  KONFIG["modell"])


def gegenpruefen(bilder, z, erster_befund):
    return fragen(bilder, GEGENPROBE_PROMPT,
                  "%s\n\nDie erste Beurteilung lautete \"%s\" mit der "
                  "Begruendung: %s\n\nPruefe das an diesen %d frischen "
                  "Bildern nach."
                  % (zustand_text(z, []), erster_befund["lage"],
                     erster_befund["begruendung"], len(bilder)),
                  KONFIG["modell"])


# ---------------------------------------------------------------------------
#  Alarm
# ---------------------------------------------------------------------------
def melden(titel, text, dringend=True):
    """Alarme in den ALARM-Kanal (bleiben stehen, ersetzen einander),
    Hinweise in den HINWEIS-Kanal (laufen von selbst ab)."""
    melde_helfer.melden(melde_helfer.ALARM if dringend else melde_helfer.HINWEIS,
                        titel, text, dringend=dringend)


def piepsen(anzahl=None):
    ton = KONFIG["ton"]
    anzahl = anzahl if anzahl is not None else KONFIG["ton_anzahl"]
    if not (shutil.which("paplay") and os.path.exists(ton)):
        sys.stdout.write("\a" * max(1, anzahl))   # Rueckfall: Terminalglocke
        sys.stdout.flush()
        return
    for i in range(anzahl):
        subprocess.run(["paplay", ton], check=False)
        if i + 1 < anzahl:
            time.sleep(0.35)


def verlauf_ablegen(bilder, lage, d):
    """Ein verkleinertes Bild je Lauf behalten, samt Urteil im Dateinamen.

    Das ist die Belegsammlung: erst wenn hier Bilder echter Fehldrucke liegen,
    laesst sich pruefen, ob die Wache richtig urteilt. Kostet nichts ausser
    ein paar Megabyte.
    """
    os.makedirs(VERLAUF, exist_ok=True)
    kante = KONFIG["verlauf_kante"]
    stempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, bild in enumerate(bilder):
        b, h = bild.size
        if max(b, h) > kante:
            f = kante / float(max(b, h))
            bild = bild.resize((int(b * f), int(h * f)), Image.LANCZOS)
        name = "%s_%s_s%s_%d.jpg" % (stempel, lage, d.get("layer_num", "x"),
                                     i + 1)
        bild.save(os.path.join(VERLAUF, name), quality=80)

    grenze = time.time() - KONFIG["verlauf_tage"] * 86400
    for alt in os.listdir(VERLAUF):
        pfad = os.path.join(VERLAUF, alt)
        try:
            if os.path.getmtime(pfad) < grenze:
                os.remove(pfad)
        except OSError:
            pass


def bilder_ablegen(bilder, marke):
    stempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    pfade = []
    for i, b in enumerate(bilder):
        p = os.path.join(BEFUNDE, "%s_%s_%d.jpg" % (stempel, marke, i + 1))
        b.save(p, quality=90)
        pfade.append(p)
    return pfade


# ---------------------------------------------------------------------------
#  Zustand und Protokoll
# ---------------------------------------------------------------------------
LEERZUSTAND = {"letzte_lage": None, "letzter_ton": 0, "befund_seit": None,
               "fehler_serie": 0, "letzte_schicht": None, "schicht_steht": 0,
               "entkraeftet_serie": 0, "letzter_auftrag": None}


def zustand_lesen():
    z = dict(LEERZUSTAND)
    if os.path.exists(ZUSTAND):
        try:
            with open(ZUSTAND, encoding="utf-8") as f:
                z.update(json.load(f))
        except Exception:
            pass
    return z


def zustand_schreiben(z):
    with open(ZUSTAND, "w", encoding="utf-8") as f:
        json.dump(z, f, ensure_ascii=False, indent=1)


def protokollieren(eintrag):
    eintrag["zeit"] = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds")
    with open(PROTOKOLL, "a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    print(json.dumps(eintrag, ensure_ascii=False))
    return eintrag


def merken_fehler(z, text, eintrag):
    z["fehler_serie"] = z.get("fehler_serie", 0) + 1
    eintrag["serie"] = z["fehler_serie"]
    zustand_schreiben(z)
    protokollieren(eintrag)
    # Erst nach drei Fehlschlaegen stoeren — eine einzelne Netzstoerung ist
    # keinen Alarm wert.
    if z["fehler_serie"] == 3:
        melden("Spaghetti-Wache gestoert",
               "Drei Pruefungen in Folge fehlgeschlagen:\n%s" % text[:200],
               dringend=False)
    return 0


# ---------------------------------------------------------------------------
#  Hauptlauf
# ---------------------------------------------------------------------------
def lauf():
    os.makedirs(BEFUNDE, exist_ok=True)
    z = zustand_lesen()

    # --- 1. Druckerzustand -------------------------------------------------
    try:
        d = a1_drucker.status()
    except Exception as f:
        return merken_fehler(z, str(f), {"lage": "fehler", "quelle": "mqtt",
                                         "meldung": str(f)[:400]})

    kurz = a1_drucker.kurzfassung(d)
    auftrag = d.get("subtask_name")

    # Neuer Auftrag: alte Befunde und Zaehler gelten nicht mehr.
    if auftrag != z.get("letzter_auftrag"):
        z.update({"letzte_lage": None, "befund_seit": None,
                  "letzte_schicht": None, "schicht_steht": 0,
                  "entkraeftet_serie": 0, "letzter_auftrag": auftrag})

    # --- 2. Torwaechter ----------------------------------------------------
    # Der mit Abstand groesste Fehlalarmvermeider: laeuft kein Druck, gibt es
    # auch keinen Fehldruck. Kostet nichts und braucht keine Bildbeurteilung.
    if not a1_drucker.druckt(d):
        if z.get("letzte_lage") in ALARM_LAGEN:
            # Druck ist vorbei — der stehende Alarm ist gegenstandslos.
            melde_helfer.schliessen(melde_helfer.ALARM)
            melde_helfer.melden(
                melde_helfer.ABSCHLUSS, "Druck beendet",
                "Letzter Befund der Wache war \"%s\" (seit %s). Der Druck "
                "ist jetzt zu Ende — bitte das Ergebnis selbst begutachten."
                % (z.get("letzte_lage"), z.get("befund_seit") or "?"),
                dauer_ms=60000)
        z["fehler_serie"] = 0
        z["letzte_lage"] = "kein_druck"
        z["befund_seit"] = None
        zustand_schreiben(z)
        protokollieren({"lage": "kein_druck", "drucker": kurz})
        return 0

    # --- 3. Der Drucker meldet selbst etwas --------------------------------
    hinweise = []
    if d.get("print_error"):
        hinweise.append("Drucker meldet Fehlercode %s" % d["print_error"])
    if d.get("hms_codes"):
        hinweise.append("HMS-Meldung(en) aktiv: %s" % ", ".join(d["hms_codes"]))
    if d.get("gcode_state") == "PAUSE":
        hinweise.append("Druck ist pausiert")

    # --- 4. Schichtzaehler --------------------------------------------------
    schicht = d.get("layer_num")
    if d.get("gcode_state") == "RUNNING" and schicht is not None:
        if schicht == z.get("letzte_schicht"):
            z["schicht_steht"] = z.get("schicht_steht", 0) + 1
        else:
            z["schicht_steht"] = 0
        z["letzte_schicht"] = schicht
    if z.get("schicht_steht", 0) >= KONFIG["schicht_steht_ab"]:
        hinweise.append("Schichtzaehler steht seit %d Laeufen auf %s"
                        % (z["schicht_steht"], schicht))

    # --- 5. Bilder ---------------------------------------------------------
    try:
        bilder = bilder_holen()
        if not bilder:
            raise a1_drucker.DruckerFehler("Keine Bilder erhalten")
    except Exception as f:
        return merken_fehler(z, str(f), {"lage": "fehler", "quelle": "kamera",
                                         "drucker": kurz,
                                         "meldung": str(f)[:400]})

    # --- 6. Beurteilung ----------------------------------------------------
    try:
        erst = beurteilen(bilder, d, hinweise)
        z["fehler_serie"] = 0
    except Exception as f:
        return merken_fehler(z, str(f), {"lage": "fehler", "quelle": "api",
                                         "drucker": kurz,
                                         "meldung": str(f)[:400]})

    eintrag = {"lage": erst["lage"], "sicherheit": erst["sicherheit"],
               "begruendung": erst["begruendung"], "drucker": kurz,
               "hinweise": hinweise, "tokens": erst.get("_tokens")}

    # --- 7. Gegenprobe -----------------------------------------------------
    # Nur was frische Bilder und eine skeptische Fragestellung ueberlebt,
    # loest Alarm aus. Das ist der eigentliche Gewinn an Gewissheit.
    alarm = False
    if erst["lage"] in ALARM_LAGEN:
        try:
            zweite = bilder_holen()
            gegen = gegenpruefen(zweite, d, erst)
        except Exception as f:
            # Gegenprobe nicht moeglich: im Zweifel fuer den Alarm. Lieber
            # einmal umsonst geweckt als einen Fehldruck verschlafen.
            gegen = {"lage": erst["lage"], "sicherheit": "niedrig",
                     "begruendung": "Gegenprobe nicht moeglich (%s)"
                                    % str(f)[:120]}
            zweite = []
        eintrag["gegenprobe"] = {"lage": gegen["lage"],
                                 "sicherheit": gegen["sicherheit"],
                                 "begruendung": gegen["begruendung"]}
        if gegen.get("_tokens"):
            eintrag["tokens_gegenprobe"] = gegen["_tokens"]

        if gegen["lage"] in ALARM_LAGEN:
            alarm = True
            z["entkraeftet_serie"] = 0
            # Die schwerere der beiden Einschaetzungen zaehlt.
            lage = ("spaghetti"
                    if "spaghetti" in (erst["lage"], gegen["lage"])
                    else "verdacht")
            begruendung = gegen["begruendung"]
            sicherheit = gegen["sicherheit"]
        else:
            # Entkraeftet. Wiederholt sich das, ist trotzdem etwas im Busch:
            # zweimal hintereinander Rauch ohne Feuer wird gemeldet.
            z["entkraeftet_serie"] = z.get("entkraeftet_serie", 0) + 1
            eintrag["lage"] = "entkraeftet"
            if z["entkraeftet_serie"] >= 2:
                alarm = True
                lage = "verdacht"
                sicherheit = "niedrig"
                begruendung = ("Zweimal hintereinander Verdacht, den die "
                               "Gegenprobe jeweils nicht bestaetigt hat: %s"
                               % erst["begruendung"])
    else:
        z["entkraeftet_serie"] = 0

    # --- 8. Alarm ----------------------------------------------------------
    if alarm:
        eintrag["lage"] = lage
        eintrag["alarm"] = True

        # Massiver, zweifach bestaetigter Fehler -> Druck abbrechen.
        schicht_ok = (d.get("layer_num") or 0) >= ABBRUCH_MIN_SCHICHT
        sicher_genug = (not ABBRUCH_BRAUCHT_HOCH) or "hoch" in (
            erst.get("sicherheit"), gegen.get("sicherheit"))
        darf_abbrechen = (lage in ABBRUCH_LAGEN
                          and gegen.get("lage") in ABBRUCH_LAGEN
                          and schicht_ok and sicher_genug)
        if not darf_abbrechen and lage in ABBRUCH_LAGEN \
                and gegen.get("lage") in ABBRUCH_LAGEN:
            eintrag["abbruch_unterdrueckt"] = {
                "schicht": d.get("layer_num"), "schicht_ok": schicht_ok,
                "sicherheit": [erst.get("sicherheit"), gegen.get("sicherheit")]}
            melden("Moeglicher Fehldruck — NICHT abgebrochen",
                   "%s\n%s\n\nKein Abbruch: %s. Bitte selbst nachsehen."
                   % (gegen["begruendung"], kurz,
                      "zu fruehe Schicht (%s < %d)" % (d.get("layer_num"),
                                                       ABBRUCH_MIN_SCHICHT)
                      if not schicht_ok else "beide Stufen nur unsicher"))
        if darf_abbrechen:
            import a1_befehle
            grund = "%s (Beurteilung: %s | Gegenprobe: %s) bei %s" % (
                lage, erst["begruendung"], gegen["begruendung"], kurz)
            ok_stop, antwort = a1_befehle.druck_stoppen(grund)
            eintrag["abbruch"] = {"ausgefuehrt": ok_stop,
                                  "antwort": antwort.get("result"),
                                  "err_code": antwort.get("err_code")}
            melden("DRUCK ABGEBROCHEN — %s" % lage,
                   "%s\n%s\n\nAbbruch %s. Bitte Platte pruefen."
                   % (gegen["begruendung"], kurz,
                      "ausgefuehrt" if ok_stop else "FEHLGESCHLAGEN"))
            piepsen()
            z["letzte_lage"] = lage
            zustand_schreiben(z)
            protokollieren(eintrag)
            return 0
        neu = z.get("letzte_lage") not in ALARM_LAGEN
        if neu:
            z["befund_seit"] = datetime.now().astimezone().isoformat(
                timespec="seconds")
        eintrag["bilder"] = bilder_ablegen(bilder + zweite, lage)

        melden("SPAGHETTI erkannt" if lage == "spaghetti"
               else "Druck: Verdacht auf Fehler",
               "%s\n%s\n(Sicherheit: %s, seit %s)\n"
               "Der Druck laeuft weiter — bitte selbst nachsehen."
               % (begruendung, kurz, sicherheit, z.get("befund_seit") or "jetzt"))

        # Ton beim ersten Auftreten immer, danach hoechstens alle N Minuten
        jetzt = time.time()
        if neu or jetzt - z.get("letzter_ton", 0) > KONFIG["ton_pause_minuten"] * 60:
            piepsen()
            z["letzter_ton"] = jetzt
        z["letzte_lage"] = lage

    else:
        fruehe_schicht = (d.get("layer_num") or 0) <= 3
        if erst["lage"] == "sicht_verdeckt" and not fruehe_schicht \
                and z.get("letzte_lage") != "sicht_verdeckt":
            melden("Spaghetti-Wache: Bett nicht beurteilbar",
                   "%s\nDer Druck laeuft, aber es kann kein Fehler erkannt "
                   "werden." % erst["begruendung"], dringend=False)
        # Meldet der Drucker selbst einen Fehler, gehoert das gesagt — auch
        # wenn auf den Bildern nichts zu sehen ist.
        if d.get("hms_codes") and z.get("letzte_lage") != "hms":
            melden("Drucker meldet einen Fehler",
                   "HMS: %s\n%s" % (", ".join(d["hms_codes"]), kurz),
                   dringend=False)
            z["letzte_lage"] = "hms"
        else:
            z["letzte_lage"] = eintrag["lage"]
        z["befund_seit"] = None

    try:
        verlauf_ablegen(bilder, eintrag["lage"], d)
    except OSError:
        pass          # Belegsammlung ist nice-to-have, nie ein Grund zu scheitern

    zustand_schreiben(z)
    protokollieren(eintrag)
    return 0


def main():
    p = argparse.ArgumentParser(description="Spaghetti-Wache")
    p.add_argument("--testton", action="store_true",
                   help="Alarm einmal vorfuehren und beenden")
    p.add_argument("--zeigen", action="store_true",
                   help="nur Kamerabilder holen und als probe_N.jpg ablegen")
    p.add_argument("--status", action="store_true",
                   help="nur den Druckerzustand zeigen")
    args = p.parse_args()

    if args.testton:
        melden("Spaghetti-Wache — Probe",
               "So sieht ein Alarm aus. Der Druck wird nie gestoppt.")
        piepsen()
        return 0
    if args.status:
        print(json.dumps(a1_drucker.status(), ensure_ascii=False, indent=2))
        return 0
    if args.zeigen:
        for i, b in enumerate(bilder_holen()):
            pfad = os.path.join(BASIS, "probe_%d.jpg" % (i + 1))
            b.save(pfad, quality=90)
            print("%s  (%dx%d)" % (pfad, b.size[0], b.size[1]))
        return 0
    return lauf()


if __name__ == "__main__":
    sys.exit(main())
