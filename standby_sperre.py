#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standby-Sperre — haelt den Laptop wach, solange gedruckt wird
=============================================================

Die Spaghetti-Wache laeuft als Timer. Geht der Laptop in Bereitschaft, laeuft
kein Timer mehr, und ein Fehldruck bliebe stundenlang unbemerkt. Dieser Dienst
haelt deshalb waehrend eines laufenden Drucks eine logind-Sperre.

Bewusst nur waehrend eines Drucks: sobald der Drucker fertig oder aus ist,
faellt die Sperre weg und der Laptop schlaeft wieder ganz normal ein. Es wird
keine Systemeinstellung veraendert — die Sperre ist ein Prozess, mit seinem
Ende ist alles wieder wie vorher.

Gesperrt werden drei Dinge:
  sleep              das eigentliche Einschlafen
  idle               das Einschlafen nach Untaetigkeit (hier: nach 30 min)
  handle-lid-switch  das Einschlafen beim Zuklappen des Deckels

Der Bildschirm darf weiter dunkel werden — die Bilder kommen ueber das Netz
direkt vom Drucker, dafuer muss nichts zu sehen sein.

Laeuft der Drucker nicht mehr oder ist er laenger nicht erreichbar, wird die
Sperre wieder freigegeben; der Laptop soll nicht wegen eines abgeschalteten
Druckers ewig wachbleiben.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime

import a1_drucker

TAKT = 300           # Sekunden zwischen zwei Abfragen
AUFGEBEN_NACH = 30   # Minuten ohne Druckerantwort -> Sperre freigeben

_sperre = None


def protokoll(text):
    print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def sperren(grund):
    global _sperre
    if _sperre and _sperre.poll() is None:
        return
    _sperre = subprocess.Popen(
        ["systemd-inhibit",
         "--what=sleep:idle:handle-lid-switch",
         "--who=Spaghetti-Wache",
         "--why=%s" % grund,
         "--mode=block",
         "sleep", "infinity"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    protokoll("Sperre gesetzt — %s" % grund)


def freigeben():
    global _sperre
    if not _sperre:
        return
    if _sperre.poll() is None:
        _sperre.terminate()
        try:
            _sperre.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _sperre.kill()
    _sperre = None
    protokoll("Sperre freigegeben — Laptop darf wieder schlafen")


def beenden(signum, rahmen):
    freigeben()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, beenden)
    signal.signal(signal.SIGINT, beenden)

    letzte_antwort = time.monotonic()
    war_aktiv = None

    try:
        while True:
            try:
                d = a1_drucker.status(timeout=10)
                letzte_antwort = time.monotonic()
                aktiv = a1_drucker.druckt(d)
                kurz = a1_drucker.kurzfassung(d)
            except Exception as f:
                # Eine einzelne Stoerung darf die Sperre nicht kippen — sonst
                # schlaeft der Laptop mitten im Druck wegen eines WLAN-Hakens
                # ein. Erst nach laengerem Schweigen aufgeben.
                still = (time.monotonic() - letzte_antwort) / 60
                if still > AUFGEBEN_NACH:
                    if war_aktiv is not False:
                        protokoll("Drucker seit %.0f min stumm (%s) — gebe frei"
                                  % (still, str(f)[:80]))
                        freigeben()
                        war_aktiv = False
                time.sleep(TAKT)
                continue

            if aktiv and war_aktiv is not True:
                sperren("3D-Druck laeuft: %s" % kurz)
                war_aktiv = True
            elif not aktiv and war_aktiv is not False:
                protokoll("Kein Druck aktiv (%s)" % kurz)
                freigeben()
                war_aktiv = False

            # Abgestuerzte Sperre neu setzen
            if aktiv and (_sperre is None or _sperre.poll() is not None):
                sperren("3D-Druck laeuft: %s" % kurz)

            time.sleep(TAKT)
    finally:
        freigeben()


if __name__ == "__main__":
    sys.exit(main() or 0)
