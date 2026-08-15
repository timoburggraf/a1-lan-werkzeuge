#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testlauf: Objekt ueberspringen am laufenden Druck
=================================================

Wartet, bis der Testdruck Schicht SKIP_AB erreicht, schickt dann den
skip_objects-Befehl fuer Wuerfel_3 und beobachtet, was danach passiert.
Bilder werden zu festen Schichten abgelegt, damit sich hinterher belegen
laesst, ob der Wuerfel wirklich stehengeblieben ist.

Laeuft im Hintergrund und schreibt alles nach testlauf.log.
"""

import io
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import a1_drucker as drucker
import a1_befehle as befehle

ZIEL_NAME = "Wuerfel_3"
ZIEL_ID = 105
SKIP_AB = 6                       # bei dieser Schicht wird uebersprungen
BILD_BEI = (5, 8, 12, 16, 20, 25)  # zu diesen Schichten ein Bild ablegen
WARTE_START = 20 * 60             # so lange auf den Druckbeginn warten
AUSSCHNITT = (0.08, 0.00, 1.00, 0.46)
ORDNER = os.path.dirname(os.path.abspath(__file__))
BILDER = os.path.join(ORDNER, "belege")


def sag(text):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def bild_ablegen(marke):
    """Ein Kamerabild ablegen, zugeschnitten wie bei der Wache."""
    try:
        roh = drucker.bilder_holen(1, 0)
        if not roh:
            return None
        b = Image.open(io.BytesIO(roh[0])).convert("RGB")
        w, h = b.size
        l, o, r, u = AUSSCHNITT
        b = b.crop((int(w * l), int(h * o), int(w * r), int(h * u)))
        os.makedirs(BILDER, exist_ok=True)
        pfad = os.path.join(BILDER, "%s.jpg" % marke)
        b.save(pfad, quality=90)
        return pfad
    except Exception as f:
        sag("  Bild %s fehlgeschlagen: %s" % (marke, str(f)[:80]))
        return None


def lage():
    try:
        return drucker.status(timeout=10)
    except Exception:
        return None


def main():
    sag("=== Testlauf Objekt-Ueberspringen ===")
    sag("Ziel: %s (ID %d), Skip bei Schicht %d" % (ZIEL_NAME, ZIEL_ID, SKIP_AB))

    # --- 1. auf Druckbeginn warten -------------------------------------
    ende = time.monotonic() + WARTE_START
    while time.monotonic() < ende:
        d = lage()
        if d:
            zustand = d.get("gcode_state")
            sag("warte... %s  Schicht %s/%s  Bett %.0f/%.0f C  Duese %.0f C" % (
                zustand, d.get("layer_num"), d.get("total_layer_num"),
                d.get("bed_temper", 0), d.get("bed_target_temper", 0),
                d.get("nozzle_temper", 0)))
            if zustand == "RUNNING" and (d.get("layer_num") or 0) >= 1:
                sag(">>> Druck laeuft: %s" % drucker.kurzfassung(d))
                break
        time.sleep(15)
    else:
        sag("!!! Kein Druckbeginn innerhalb %d Minuten — Abbruch."
            % (WARTE_START // 60))
        return 1

    # --- 2. bis zur Skip-Schicht begleiten ------------------------------
    gemacht = set()
    uebersprungen = False
    antwort = None
    while True:
        d = lage()
        if not d:
            time.sleep(10)
            continue
        schicht = d.get("layer_num") or 0
        zustand = d.get("gcode_state")

        if schicht in BILD_BEI and schicht not in gemacht:
            gemacht.add(schicht)
            p = bild_ablegen("schicht_%02d%s" % (
                schicht, "_nach_skip" if uebersprungen else ""))
            sag("Schicht %2d  Bild: %s" % (schicht, os.path.basename(p or "-")))

        # --- 3. der eigentliche Versuch ---------------------------------
        if not uebersprungen and zustand == "RUNNING" and schicht >= SKIP_AB:
            bild_ablegen("vor_skip_schicht_%02d" % schicht)
            sag(">>> SENDE skip_objects fuer %s (ID %d) bei Schicht %d"
                % (ZIEL_NAME, ZIEL_ID, schicht))
            try:
                ok, antwort, s_obj = befehle.ueberspringen([ZIEL_ID])
                sag("    angenommen: %s" % ok)
                sag("    Antwort:    %s" % json.dumps(antwort, ensure_ascii=False))
                sag("    s_obj:      %s" % s_obj)
                if ok and s_obj and ZIEL_ID in (s_obj or []):
                    sag("    ==> Drucker fuehrt das Objekt als uebersprungen")
                elif ok:
                    sag("    ==> angenommen, aber s_obj enthaelt es NICHT")
                else:
                    sag("    ==> ABGELEHNT (err_code %s)"
                        % antwort.get("err_code"))
            except Exception as f:
                sag("    FEHLER beim Senden: %s" % str(f)[:200])
            uebersprungen = True

        if zustand in ("FINISH", "FAILED", "IDLE") and schicht >= SKIP_AB:
            sag(">>> Druck beendet (%s) bei Schicht %s" % (zustand, schicht))
            bild_ablegen("ende_%s" % zustand.lower())
            break
        if schicht >= (d.get("total_layer_num") or 25):
            sag(">>> letzte Schicht erreicht")
            bild_ablegen("ende_letzte_schicht")
            time.sleep(60)
            break
        time.sleep(12)

    # --- 4. Schlussbefund ------------------------------------------------
    d = lage() or {}
    sag("=== Ende ===")
    sag("Zustand: %s" % drucker.kurzfassung(d))
    sag("s_obj laut Drucker: %s" % d.get("s_obj"))
    sag("Bilder in: %s" % BILDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
