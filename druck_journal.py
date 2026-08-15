#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Druck-Journal — schreibt laufend mit, wo der Druck steht
========================================================

Damit ein abgebrochener Druck spaeter genau dort fortgesetzt werden kann, wo
er stehengeblieben ist, muss der Zustand VORHER festgehalten sein — hinterher
ist er weg. Dieser Dienst schreibt ihn im Sekundentakt mit.

Was ueber MQTT ueberhaupt zu holen ist, bestimmt die erreichbare Genauigkeit:
`layer_num` gibt es, die Position INNERHALB einer Schicht nicht. Feiner als
auf die Schicht genau geht es also nicht — das ist keine Schwaeche dieses
Skripts, sondern die Grenze der Schnittstelle. Bei 0.12 mm Schichthoehe
bedeutet das im schlimmsten Fall eine halb gedruckte Schicht.

Je Druckauftrag entsteht `journal/<datei>_<start>.jsonl` mit einer Zeile je
Zustandsaenderung, plus `journal/letzter.json` mit dem Schlussstand — das ist
die Datei, aus der `fortsetzung_bauen.py` spaeter die Fortsetzung ableitet.
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a1_drucker

TAKT_DRUCK = 10          # Sekunden waehrend des Drucks
TAKT_RUHE = 60           # ... ohne Druck
BASIS = os.path.dirname(os.path.abspath(__file__))
ORDNER = os.path.join(BASIS, "journal")

# Diese Felder machen den Zustand aus. Aendert sich eines, wird geschrieben.
FELDER = ("gcode_state", "layer_num", "total_layer_num", "mc_percent",
          "s_obj", "subtask_name", "gcode_file", "nozzle_target_temper",
          "bed_target_temper", "spd_lvl", "print_error")


def kennung(z):
    """Stabiler Name je Druckauftrag."""
    n = (z.get("gcode_file") or z.get("subtask_name") or "unbekannt")
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in n)


def zustand(z):
    d = {k: z.get(k) for k in FELDER if k in z}
    d["zeit"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return d


def main():
    os.makedirs(ORDNER, exist_ok=True)
    print("%s  Druck-Journal laeuft" % datetime.now().strftime("%H:%M:%S"),
          flush=True)
    letzte_kernwerte = None
    aktuelle_datei = None

    while True:
        try:
            z = a1_drucker.status(timeout=10)
        except Exception:
            time.sleep(TAKT_RUHE)
            continue

        aktiv = a1_drucker.druckt(z)
        d = zustand(z)
        # Nur bei echter Aenderung schreiben — sonst laeuft die Datei voll
        kern = tuple(d.get(k) for k in ("gcode_state", "layer_num", "s_obj",
                                        "gcode_file", "print_error"))
        kern = tuple(tuple(x) if isinstance(x, list) else x for x in kern)

        if aktiv and kern != letzte_kernwerte:
            name = kennung(z)
            if aktuelle_datei is None or not aktuelle_datei.startswith(name):
                aktuelle_datei = "%s_%s.jsonl" % (
                    name, datetime.now().strftime("%Y%m%d_%H%M%S"))
                print("  neuer Auftrag -> %s" % aktuelle_datei, flush=True)
            with open(os.path.join(ORDNER, aktuelle_datei), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            # Schlussstand immer mitschreiben: DAS ist die Fortsetzungsquelle
            d["journal"] = aktuelle_datei
            with open(os.path.join(ORDNER, "letzter.json"), "w",
                      encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            letzte_kernwerte = kern

        if not aktiv and aktuelle_datei is not None:
            print("  Auftrag beendet (%s), Journal %s"
                  % (z.get("gcode_state"), aktuelle_datei), flush=True)
            aktuelle_datei = None
            letzte_kernwerte = None

        time.sleep(TAKT_DRUCK if aktiv else TAKT_RUHE)


if __name__ == "__main__":
    sys.exit(main())
