#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prototyp-Begleitung: ems14a zweifarbig, mit Skip bei bestaetigtem Spaghetti
===========================================================================

Startet den Druck (Kamera-Freigabe eingeschlossen) und begleitet ihn bis zum
Ende. Die Erkennung faehrt zweistufig wie die Spaghetti-Wache: Beurteilung
plus Gegenprobe mit frischen Bildern. NUR wenn beide Stufen "spaghetti"
sagen (nicht bloss "verdacht"), wird versucht, das betroffene Teil zu
finden und zu ueberspringen — ausdrueckliche Vorab-Freigabe von Timo fuer
diesen Druck (14.08.2026: "wenn Du Spaghetti bekommst, dann musst Du
natuerlich den Skip machen").

Sicherungen:
  - Skip nur bei zweifach bestaetigtem "spaghetti", nie bei "verdacht".
  - Das Ziel muss sich eindeutig einem Objekt zuordnen lassen (die Zuordnung
    kennt Position und Namen aller 10 Teile); ist die Zuordnung unsicher,
    gibt es nur Alarm und KEINEN Skip.
  - Nach jedem Skip: Kontrolle ueber s_obj.
  - Hoechstens SKIP_MAX Skips je Druck — mehr deutet auf ein Grundproblem,
    dann ist der Mensch dran.
  - Der Druck wird niemals pausiert oder gestoppt.
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import a1_drucker as drucker
import a1_befehle as befehle

DATEI = "ems14a_zweifarbig.gcode.3mf"
AMS = [0, 1]                      # Filament 1 -> Fach 0 (weiss), 2 -> Fach 1 (schwarz)
PRUEF_TAKT = 600                  # Sekunden zwischen Bildbeurteilungen
STATUS_TAKT = 45                  # Sekunden zwischen Statusabfragen
SKIP_MAX = 3
ORDNER = os.path.dirname(os.path.abspath(__file__))
BILDER = os.path.join(ORDNER, "verlauf_proto")
AUSSCHNITT = (0.08, 0.00, 1.00, 0.46)
MODELL = "claude-opus-5"
TON = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"

OBJEKTE = json.load(open(os.path.join(ORDNER, "objekt_ids.json")))
ANORDNUNG = json.load(open(os.path.join(ORDNER, "anordnung.json")))

LAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "lage": {"type": "string",
                 "enum": ["ok", "verdacht", "spaghetti", "sicht_verdeckt"]},
        "sicherheit": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
        "begruendung": {"type": "string"},
    },
    "required": ["lage", "sicherheit", "begruendung"],
    "additionalProperties": False,
}

ZIEL_SCHEMA = {
    "type": "object",
    "properties": {
        "objekt": {"type": "string",
                   "enum": sorted(OBJEKTE) + ["unklar"]},
        "sicherheit": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
        "begruendung": {"type": "string"},
    },
    "required": ["objekt", "sicherheit", "begruendung"],
    "additionalProperties": False,
}

KAMERA = """\
Zur Kamera: Beim Bambu Lab A1 sitzt sie vorn unten und blickt flach ueber das
Druckbett; das helle Gehaeuse am unteren Bildrand ist der Drucker selbst. Das
Bett faehrt in der Tiefe, die Bilder zeigen daher wechselnde Ausschnitte.
Bewegungsunschaerfe und oranger Lichtschein sind normal.

Auf der Platte stehen 10 Teile eines Elektronikgehaeuses (weiss, PLA):
%s
Der gemauerte Turm links hinten ist der Wischturm — der gehoert dahin.
""" % "\n".join("  - %-20s Mitte bei x=%d, y=%d" % (n, *ANORDNUNG[n])
                for n in sorted(OBJEKTE))

LAGE_PROMPT = """\
Du beurteilst Standbilder eines laufenden FDM-Drucks.

%s
Lagen: "ok" (du siehst die Teile und sie wirken in Ordnung), "verdacht"
(etwas koennte nicht stimmen), "spaghetti" (eindeutiger Fehldruck:
Fadenwirrwarr, losgerissenes Teil, Kopf zieht Material durch die Luft),
"sicht_verdeckt" (auf keinem Bild beurteilbar).
Melde "spaghetti" nur, wenn du es wirklich siehst. Bei niedriger Schichtzahl
sind die Teile flach — das ist normal. Begruendung: ein kurzer deutscher Satz.
""" % KAMERA

GEGEN_PROMPT = """\
Eine erste Beurteilung meldet einen moeglichen Fehldruck. Pruefe skeptisch an
FRISCHEN Bildern nach — suche aktiv die harmlose Erklaerung (Wischturm,
Stringing, Spiegelung, Unschaerfe, Gehaeuse im Vordergrund).

%s
Antworte mit der Lage auf DIESEN Bildern: "ok", "verdacht", "spaghetti",
"sicht_verdeckt". Im Zweifel "ok". Begruendung: ein kurzer deutscher Satz.
""" % KAMERA

ZIEL_PROMPT = """\
Auf diesem Druck ist ein Fehldruck (Spaghetti) bestaetigt. Bestimme, WELCHES
Teil betroffen ist.

%s
Die Kamera blickt von vorn (y=0) ueber das Bett: kleine y sind vorn/nah,
grosse y hinten/fern; x waechst nach rechts. Nutze die Positionsliste oben.
Wenn du es nicht sicher einem Teil zuordnen kannst, antworte "unklar" —
ein falscher Skip verwirft ein gutes Teil. Begruendung: ein kurzer Satz.
""" % KAMERA


def sag(text):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def alarm(titel, text, ton=True):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "ems14a-Proto", "-u", "critical",
                        "-i", "dialog-warning", titel, text], check=False)
    if ton and shutil.which("paplay") and os.path.exists(TON):
        for _ in range(3):
            subprocess.run(["paplay", TON], check=False)


def bilder(n=4, abstand=6):
    roh = drucker.bilder_holen(n, abstand)
    aus = []
    for b in roh:
        i = Image.open(io.BytesIO(b)).convert("RGB")
        w, h = i.size
        l, o, r, u = AUSSCHNITT
        aus.append(i.crop((int(w*l), int(h*o), int(w*r), int(h*u))))
    return aus


def fragen(bs, system, text, schema):
    import anthropic
    inhalt = []
    for b in bs:
        p = io.BytesIO(); b.save(p, format="JPEG", quality=88)
        inhalt.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(p.getvalue()).decode()}})
    inhalt.append({"type": "text", "text": text})
    a = anthropic.Anthropic().messages.create(
        model=MODELL, max_tokens=2000, system=system,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": inhalt}])
    return json.loads(next(b.text for b in a.content if b.type == "text"))


def ablegen(bs, marke):
    os.makedirs(BILDER, exist_ok=True)
    st = datetime.now().strftime("%H%M%S")
    for i, b in enumerate(bs):
        b.save(os.path.join(BILDER, "%s_%s_%d.jpg" % (st, marke, i+1)),
               quality=85)


def main():
    sag("=== ems14a-Prototyp: Start + Begleitung ===")
    os.makedirs(BILDER, exist_ok=True)

    # --- Start (mit Kamera-Freigabe) -------------------------------------
    ende = time.monotonic() + 15 * 60
    gestartet = False
    try:
        if drucker.druckt(drucker.status(timeout=8)):
            sag("Druck laeuft bereits."); gestartet = True
    except Exception:
        pass
    while not gestartet and time.monotonic() < ende:
        ok, meldung, befund = befehle.druck_starten(DATEI, ams_fach=AMS,
                                                    bilder_nach=BILDER)
        sag(meldung if ok else "Start verweigert: %s" % meldung)
        gestartet = ok
        if not ok:
            time.sleep(45)
    if not gestartet:
        alarm("ems14a-Proto nicht gestartet", "Keine Freigabe in 15 Minuten.")
        return 1

    # --- Begleitung ------------------------------------------------------
    letzte_pruefung = 0.0
    letzte_schicht = None
    skips = []
    hms_bekannt = set()
    fehler_bekannt = 0

    while True:
        try:
            d = drucker.status(timeout=10)
        except Exception:
            time.sleep(STATUS_TAKT); continue
        zustand = d.get("gcode_state")
        schicht = d.get("layer_num")
        if schicht != letzte_schicht:
            letzte_schicht = schicht
            sag("%s Schicht %s/%s %s%%  s_obj=%s" %
                (zustand, schicht, d.get("total_layer_num"),
                 d.get("mc_percent"), d.get("s_obj")))

        neue_hms = set(d.get("hms_codes") or []) - hms_bekannt
        fehler = d.get("print_error") or 0
        if neue_hms or (fehler and fehler != fehler_bekannt):
            alarm("ems14a-Proto: Druckermeldung",
                  "HMS %s / print_error %s" % (sorted(neue_hms), fehler))
            hms_bekannt |= neue_hms
        fehler_bekannt = fehler

        if zustand in ("FINISH", "FAILED", "IDLE"):
            sag(">>> Ende: %s bei Schicht %s" % (zustand, schicht))
            try:
                ablegen(bilder(2, 4), "ende")
            except Exception:
                pass
            break

        # --- Bildbeurteilung im Takt -------------------------------------
        if zustand == "RUNNING" and time.monotonic() - letzte_pruefung > PRUEF_TAKT:
            letzte_pruefung = time.monotonic()
            try:
                bs = bilder()
                lage = fragen(bs, LAGE_PROMPT,
                              "Schicht %s von %s. Beurteile diese %d Bilder."
                              % (schicht, d.get("total_layer_num"), len(bs)),
                              LAGE_SCHEMA)
                sag("Beurteilung: %s (%s) %s" % (lage["lage"],
                    lage["sicherheit"], lage["begruendung"][:90]))
                ablegen(bs[:1], lage["lage"])
                if lage["lage"] not in ("spaghetti", "verdacht"):
                    continue

                gegen = fragen(bilder(), GEGEN_PROMPT,
                               "Erstbefund: %s — %s. Schicht %s. Pruefe nach."
                               % (lage["lage"], lage["begruendung"], schicht),
                               LAGE_SCHEMA)
                sag("Gegenprobe:  %s (%s) %s" % (gegen["lage"],
                    gegen["sicherheit"], gegen["begruendung"][:90]))
                if not (lage["lage"] == "spaghetti" and
                        gegen["lage"] == "spaghetti"):
                    if gegen["lage"] in ("spaghetti", "verdacht"):
                        alarm("ems14a-Proto: Verdacht",
                              "%s\nKein Skip (nicht zweifach bestaetigt) — "
                              "bitte selbst nachsehen." % gegen["begruendung"])
                    continue

                # --- bestaetigtes Spaghetti: Ziel bestimmen --------------
                zb = bilder()
                ablegen(zb, "spaghetti")
                ziel = fragen(zb, ZIEL_PROMPT,
                              "Schicht %s. Welches Teil ist betroffen?" % schicht,
                              ZIEL_SCHEMA)
                sag("Zielbestimmung: %s (%s) %s" % (ziel["objekt"],
                    ziel["sicherheit"], ziel["begruendung"][:90]))

                if ziel["objekt"] == "unklar" or ziel["sicherheit"] == "niedrig":
                    alarm("SPAGHETTI — Teil unklar",
                          "Fehldruck bestaetigt, aber keinem Teil sicher "
                          "zuzuordnen. KEIN Skip. Objekte: %s"
                          % ", ".join("%s=%d" % kv for kv in OBJEKTE.items()))
                    continue
                if len(skips) >= SKIP_MAX:
                    alarm("SPAGHETTI — Skip-Grenze erreicht",
                          "Schon %d Teile uebersprungen; das deutet auf ein "
                          "Grundproblem. Bitte selbst entscheiden." % len(skips))
                    continue
                if ziel["objekt"] in skips:
                    alarm("SPAGHETTI — Ziel schon uebersprungen",
                          "%s ist bereits uebersprungen; Reste bewegen sich "
                          "womoeglich noch. Bitte nachsehen." % ziel["objekt"])
                    continue

                oid = OBJEKTE[ziel["objekt"]]
                sag(">>> SKIP %s (ID %d)" % (ziel["objekt"], oid))
                ok, antwort, s_obj = befehle.ueberspringen([oid])
                sag("    Quittung %s | err %s | s_obj %s"
                    % (antwort.get("result"), antwort.get("err_code"), s_obj))
                if ok:
                    skips.append(ziel["objekt"])
                    alarm("SKIP ausgefuehrt: %s" % ziel["objekt"],
                          "Spaghetti zweifach bestaetigt.\n%s\n"
                          "s_obj=%s — Druck laeuft weiter."
                          % (ziel["begruendung"], s_obj))
                else:
                    alarm("SKIP FEHLGESCHLAGEN: %s" % ziel["objekt"],
                          "err_code %s — bitte am Geraet handeln."
                          % antwort.get("err_code"))
            except Exception as f:
                sag("Pruefung fehlgeschlagen: %s" % str(f)[:120])

        time.sleep(STATUS_TAKT)

    d = {}
    try:
        d = drucker.status()
    except Exception:
        pass
    sag("=== Schluss === %s | s_obj=%s | Skips=%s"
        % (drucker.kurzfassung(d), d.get("s_obj"), skips or "keine"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
