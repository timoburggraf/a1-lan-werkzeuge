#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stoerungs-Quittierer — drueckt bekannte Filamentfehler weg
==========================================================

Hintergrund (15.08.2026): Beim Korpus-Nachdruck meldete der A1 zweimal
`print_error 302022672` — Filament beim Einzug verklemmt, weil die Spule ab
Werk unsauber gewickelt ist. Timo hat die Meldung am Bildschirm weggedrueckt,
der Druck lief weiter. Genau dieses Wegdruecken macht dieser Dienst
automatisch, nach Timos Regel:

    Hoechstens 3 Quittierungen in 10 Minuten. Kommt der Fehler haeufiger,
    ist es KEINE Kleinigkeit mehr -> Alarm an den Menschen, keine weitere
    Quittierung, bis das Fenster wieder frei ist.

Sicherungen:
  - Nur Fehlercodes von der WEISSLISTE werden quittiert. Unbekannte Codes
    geben sofort Alarm und werden nie automatisch weggedrueckt.
  - Quittieren heisst ausschliesslich FORTSETZEN (ams_control resume bzw.
    print.resume). Dieser Dienst kann den Druck nicht anhalten.
  - Jede Quittierung wird gemeldet (Desktop, ohne Ton) und protokolliert.

Der Dienst laeuft dauerhaft; ohne aktiven Druck prueft er nur langsam weiter.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a1_drucker
import a1_befehle
import melde_helfer

# Fehler, die erfahrungsgemaess ein Wiederholen wert sind. Nur exakte Codes —
# lieber einmal zu oft der Mensch als einmal zu wenig.
WEISSLISTE = {
    302022672,   # 0x12008010: Filament beim AMS-Einzug verklemmt (15.08.2026)
}

TAKT_DRUCK = 45          # Sekunden zwischen Pruefungen bei laufendem Druck
TAKT_RUHE = 300          # ... ohne Druck
FENSTER = 10 * 60        # Timos Regel: Fenster 10 Minuten ...
MAX_JE_FENSTER = 3       # ... hoechstens 3 Quittierungen darin
TON = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
BASIS = os.path.dirname(os.path.abspath(__file__))
PROTOKOLL = os.path.join(BASIS, "quittierungen.jsonl")


def sag(text):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def melden(titel, text, ton=False):
    """Dringendes in den ALARM-Kanal (bleibt stehen, ersetzt Vorgaenger),
    Routinemeldungen in den HINWEIS-Kanal (laeuft von selbst ab)."""
    melde_helfer.melden(melde_helfer.ALARM if ton else melde_helfer.HINWEIS,
                        titel, text, dringend=ton, ton=3 if ton else 0)


def protokollieren(eintrag):
    eintrag["zeit"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with open(PROTOKOLL, "a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


def main():
    sag("=== Stoerungs-Quittierer laeuft (Weissliste: %s) ==="
        % sorted(WEISSLISTE))
    quittiert = []            # Zeitstempel der letzten Quittierungen
    gemeldet_fremd = set()    # unbekannte Codes, die schon Alarm hatten
    limit_gemeldet = 0.0
    war_aktiv = False
    druck_fehler = {}         # code -> [Episoden, davon quittiert]
    letzter_code = 0          # fuer die Episodenzaehlung

    while True:
        try:
            z = a1_drucker.status(timeout=10)
        except Exception:
            time.sleep(TAKT_RUHE)
            continue

        aktiv = a1_drucker.druckt(z)
        fehler = z.get("print_error") or 0

        # Druckende: stehende Alarme aufraeumen und EINMAL zusammenfassen.
        if war_aktiv and not aktiv:
            melde_helfer.schliessen(melde_helfer.ALARM)
            if druck_fehler:
                zeilen = ["Code %d: %dx aufgetreten, %dx quittiert"
                          % (c, v[0], v[1])
                          for c, v in sorted(druck_fehler.items())]
                melde_helfer.melden(
                    melde_helfer.ABSCHLUSS, "Druck beendet — Stoerungsbilanz",
                    "%s\n%s" % (a1_drucker.kurzfassung(z), "\n".join(zeilen)),
                    dauer_ms=60000)
                protokollieren({"aktion": "bilanz",
                                "fehler": {str(c): v for c, v in
                                           druck_fehler.items()},
                                "drucker": a1_drucker.kurzfassung(z)})
                sag("Druckende — Bilanz gemeldet: %s" % druck_fehler)
            druck_fehler = {}
            quittiert = []
            limit_gemeldet = 0.0
        war_aktiv = aktiv

        if aktiv and fehler:
            jetzt = time.time()
            quittiert = [t for t in quittiert if jetzt - t < FENSTER]

            eintrag = druck_fehler.setdefault(fehler, [0, 0])
            if fehler != letzter_code:
                eintrag[0] += 1          # neue Fehler-Episode
            if fehler not in WEISSLISTE:
                if fehler not in gemeldet_fremd:
                    gemeldet_fremd.add(fehler)
                    melden("Drucker: unbekannter Fehler %d" % fehler,
                           "0x%08X — steht nicht auf der Weissliste, wird "
                           "NICHT automatisch quittiert. Bitte nachsehen.\n%s"
                           % (fehler, a1_drucker.kurzfassung(z)), ton=True)
                    protokollieren({"aktion": "alarm_fremd", "code": fehler,
                                    "drucker": a1_drucker.kurzfassung(z)})
                    sag("Fremder Fehler %d — nur Alarm" % fehler)
            elif len(quittiert) >= MAX_JE_FENSTER:
                if jetzt - limit_gemeldet > FENSTER:
                    limit_gemeldet = jetzt
                    melden("Drucker: Fehler haeuft sich",
                           "Fehler %d zum %d. Mal in 10 Minuten — das ist "
                           "keine Kleinigkeit mehr, bitte selbst nachsehen. "
                           "Es wird nicht weiter quittiert."
                           % (fehler, len(quittiert) + 1), ton=True)
                    protokollieren({"aktion": "limit", "code": fehler,
                                    "anzahl": len(quittiert)})
                    sag("Limit erreicht — keine Quittierung mehr")
            else:
                sag("Quittiere Fehler %d (Nr. %d im Fenster)"
                    % (fehler, len(quittiert) + 1))
                eintrag[1] += 1
                a1_befehle.filament_wiederholen()
                time.sleep(6)
                try:
                    z2 = a1_drucker.status(timeout=8)
                except Exception:
                    z2 = {}
                if z2.get("gcode_state") == "PAUSE":
                    a1_befehle.fortsetzen()
                    time.sleep(6)
                    try:
                        z2 = a1_drucker.status(timeout=8)
                    except Exception:
                        z2 = {}
                erfolg = not (z2.get("print_error") or 0)
                quittiert.append(jetzt)
                melden("Filamentfehler quittiert (%d/3 in 10 min)"
                       % len(quittiert),
                       "Code %d weggedrueckt, Druck %s.\n%s"
                       % (fehler,
                          "laeuft weiter" if erfolg else "meldet weiterhin",
                          a1_drucker.kurzfassung(z2 or z)))
                protokollieren({"aktion": "quittiert", "code": fehler,
                                "nummer": len(quittiert), "erfolg": erfolg,
                                "drucker": a1_drucker.kurzfassung(z2 or z)})

        elif not fehler:
            gemeldet_fremd.clear()

        letzter_code = fehler
        time.sleep(TAKT_DRUCK if aktiv else TAKT_RUHE)


if __name__ == "__main__":
    sys.exit(main())
