#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testlauf 2: die ganze Kette — Start bis Skip-Wirknachweis
=========================================================

Erste vollautonome Fahrt: Kamera-Freigabe der leeren Platte, Druckstart per
`druck_starten()`, Begleitung mit Bildern, `skip_objects` fuer Wuerfel_3 bei
Schicht 6, Wirknachweis ueber drei unabhaengige Signale (Quittung, s_obj,
Kamerabilder). Hypothesenleiter aus TESTPLAN.md:

  H1  Skip bei Schicht 6, dann beobachten.
  H2  Falls quittiert aber wirkungslos (s_obj leer): EINE Wiederholung bei
      ~Schicht 12. Mehr nicht.
  H3  Weiter wirkungslos -> nur dokumentieren, keine Experimente mehr.

Sicherheitsregeln (fest einprogrammiert):
  - Kein Start ohne Kamera-Freigabe der leeren Platte.
  - Ausschliesslich der offizielle skip_objects-Befehl; niemals gcode_line
    oder andere Injektionen waehrend des Drucks.
  - Taucht nach dem Skip eine HMS-Meldung oder print_error auf: sofort
    Desktop-Alarm mit Ton. Der Druck wird NIE von hier aus gestoppt —
    das entscheidet der Mensch am Geraet.
"""

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

DATEI = "Skiptest_final.gcode.3mf"
ZIEL_ID = 105                    # Wuerfel_3
SKIP_BEI = 6
WIEDERHOLUNG_BEI = 12            # H2, nur falls s_obj nach H1 leer bleibt
BILD_BEI = (2, 5, 8, 12, 16, 20, 25)
AUSSCHNITT = (0.08, 0.00, 1.00, 0.46)
ORDNER = os.path.dirname(os.path.abspath(__file__))
BILDER = os.path.join(ORDNER, "belege2")
PLATTE_WARTEN_MIN = 15           # so lange auf eine freie Platte warten
TON = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def sag(text):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def alarm(titel, text):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Skip-Test", "-u", "critical",
                        "-i", "dialog-warning", titel, text], check=False)
    if shutil.which("paplay") and os.path.exists(TON):
        for _ in range(3):
            subprocess.run(["paplay", TON], check=False)


def bild(marke):
    try:
        roh = drucker.bilder_holen(1, 0)
        b = Image.open(io.BytesIO(roh[0])).convert("RGB")
        w, h = b.size
        l, o, r, u = AUSSCHNITT
        b = b.crop((int(w * l), int(h * o), int(w * r), int(h * u)))
        os.makedirs(BILDER, exist_ok=True)
        p = os.path.join(BILDER, "%s.jpg" % marke)
        b.save(p, quality=90)
        return p
    except Exception as f:
        sag("  Bild %s fehlgeschlagen: %s" % (marke, str(f)[:80]))
        return None


def main():
    sag("=== Testlauf 2: Kette + Skip-Wirknachweis ===")
    os.makedirs(BILDER, exist_ok=True)

    # --- 1. Auf freie Platte warten und starten --------------------------
    ende = time.monotonic() + PLATTE_WARTEN_MIN * 60
    gestartet = False
    # Laeuft (nach einem Neustart dieses Skripts) schon ein Druck, direkt
    # in die Begleitung gehen statt einen zweiten Start zu versuchen.
    try:
        if drucker.druckt(drucker.status(timeout=8)):
            sag("Druck laeuft bereits — gehe direkt in die Begleitung.")
            gestartet = True
    except Exception:
        pass
    while time.monotonic() < ende and not gestartet:
        ok, meldung, befund = befehle.druck_starten(DATEI, ams_fach=0,
                                                    bilder_nach=BILDER)
        sag(meldung if ok else "Start verweigert: %s" % meldung)
        if ok:
            gestartet = True
            break
        # Platte noch belegt oder Drucker beschaeftigt: kurz warten
        time.sleep(45)
    if not gestartet:
        alarm("Skip-Test nicht gestartet",
              "Innerhalb von %d Minuten keine Freigabe." % PLATTE_WARTEN_MIN)
        return 1

    # --- 2. Begleiten, Skip, Wirknachweis --------------------------------
    gemacht = set()
    skip_gesendet = False
    wiederholt = False
    s_obj_je = {}
    hms_vorher = None

    while True:
        try:
            d = drucker.status(timeout=10)
        except Exception:
            time.sleep(10)
            continue
        schicht = d.get("layer_num") or 0
        zustand = d.get("gcode_state")

        if hms_vorher is None:
            hms_vorher = set(d.get("hms_codes") or [])

        if schicht in BILD_BEI and schicht not in gemacht:
            gemacht.add(schicht)
            p = bild("schicht_%02d%s" % (schicht,
                                         "_nach_skip" if skip_gesendet else ""))
            sag("Schicht %2d  s_obj=%s  Bild: %s"
                % (schicht, d.get("s_obj"), os.path.basename(p or "-")))

        # H1: der eigentliche Skip
        if not skip_gesendet and zustand == "RUNNING" and schicht >= SKIP_BEI:
            bild("vor_skip_schicht_%02d" % schicht)
            sag(">>> H1: skip_objects fuer Wuerfel_3 (ID %d) bei Schicht %d"
                % (ZIEL_ID, schicht))
            ok, antwort, s_obj = befehle.ueberspringen([ZIEL_ID])
            sag("    Quittung: %s | err_code: %s | s_obj: %s"
                % (antwort.get("result", "?"), antwort.get("err_code"), s_obj))
            skip_gesendet = True
            s_obj_je[schicht] = s_obj

        # H2: einmalige Wiederholung, falls s_obj leer blieb
        if skip_gesendet and not wiederholt and zustand == "RUNNING" \
                and schicht >= WIEDERHOLUNG_BEI \
                and not (d.get("s_obj") or []):
            sag(">>> H2: s_obj weiter leer — einmalige Wiederholung bei "
                "Schicht %d" % schicht)
            ok, antwort, s_obj = befehle.ueberspringen([ZIEL_ID])
            sag("    Quittung: %s | err_code: %s | s_obj: %s"
                % (antwort.get("result", "?"), antwort.get("err_code"), s_obj))
            wiederholt = True

        # Sicherheitsnetz: neue HMS oder print_error nach dem Skip
        if skip_gesendet:
            neue = set(d.get("hms_codes") or []) - hms_vorher
            if neue or d.get("print_error"):
                alarm("Skip-Test: Drucker meldet Stoerung",
                      "HMS %s / print_error %s — bitte am Geraet nachsehen. "
                      "Es wird NICHT automatisch gestoppt."
                      % (sorted(neue), d.get("print_error")))
                sag("!!! Stoerung: HMS %s print_error %s"
                    % (sorted(neue), d.get("print_error")))
                hms_vorher |= neue

        if zustand in ("FINISH", "FAILED", "IDLE"):
            sag(">>> Druck beendet (%s) bei Schicht %s" % (zustand, schicht))
            time.sleep(20)
            bild("ende_%s" % zustand.lower())
            break
        time.sleep(12)

    # --- 3. Schlussbefund -------------------------------------------------
    try:
        d = drucker.status()
    except Exception:
        d = {}
    sag("=== Ende ===")
    sag("Zustand: %s" % drucker.kurzfassung(d))
    sag("s_obj am Ende: %s" % d.get("s_obj"))
    sag("Bilder: %s" % BILDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
