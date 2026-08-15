#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemeinsamer Melde-Helfer fuer alle Drucker-Waechter
===================================================

Loest zwei Probleme der bisherigen notify-send-Aufrufe (15.08.2026):

1. `critical`-Meldungen bleiben in GNOME stehen, bis man sie wegklickt —
   wiederholte Alarme stapelten sich zu einem Berg, der nach Druckende
   nicht verschwand.
2. Das installierte notify-send kann kein `--replace-id`.

Deshalb hier direkt ueber D-Bus (org.freedesktop.Notifications.Notify) mit
FESTEN Ersetzungs-IDs je Kanal: eine neue Meldung desselben Kanals ERSETZT
die alte, statt sich danebenzustellen. Nicht-dringende Meldungen laufen nach
`dauer_ms` von selbst ab.

Kanaele (eine sichtbare Meldung je Kanal):
  ALARM        dringende Druckfehler (Spaghetti, Limit erreicht) — bleibt
               stehen, bis sie ersetzt oder weggeklickt wird
  HINWEIS      Aufbau-/Sichtprobleme, Einzelquittierungen — laeuft ab
  ABSCHLUSS    die eine Zusammenfassung nach Druckende — ersetzt ALARM
"""

import os
import shutil
import subprocess

ALARM = 9101
HINWEIS = 9102
ABSCHLUSS = 9104

TON = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def _gdbus(kanal, titel, text, dringend, dauer_ms, symbol):
    if not shutil.which("gdbus"):
        return False
    hinweise = "{'urgency': <byte %d>}" % (2 if dringend else 1)
    r = subprocess.run(
        ["gdbus", "call", "--session",
         "--dest", "org.freedesktop.Notifications",
         "--object-path", "/org/freedesktop/Notifications",
         "--method", "org.freedesktop.Notifications.Notify",
         "Drucker-Wache", str(kanal), symbol, titel, text,
         "[]", hinweise, str(dauer_ms)],
        capture_output=True, text=True)
    return r.returncode == 0


def melden(kanal, titel, text, dringend=False, dauer_ms=20000, ton=0):
    """Meldung im Kanal anzeigen (ersetzt die vorige desselben Kanals).

    dringend=True laesst die Meldung stehen (dauer_ms wird dann ignoriert,
    GNOME behaelt critical bis zum Ersetzen/Wegklicken). `ton` = Anzahl
    Alarmklaenge.
    """
    symbol = "dialog-warning" if dringend else "dialog-information"
    ok = _gdbus(kanal, titel, text, dringend, 0 if dringend else dauer_ms,
                symbol)
    if not ok and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Drucker-Wache",
                        "-u", "critical" if dringend else "normal",
                        "-t", str(dauer_ms), "-i", symbol, titel, text],
                       check=False)
    for _ in range(max(0, int(ton))):
        if shutil.which("paplay") and os.path.exists(TON):
            subprocess.run(["paplay", TON], check=False)


def schliessen(kanal):
    """Die stehende Meldung eines Kanals aktiv schliessen."""
    if not shutil.which("gdbus"):
        return
    subprocess.run(
        ["gdbus", "call", "--session",
         "--dest", "org.freedesktop.Notifications",
         "--object-path", "/org/freedesktop/Notifications",
         "--method", "org.freedesktop.Notifications.CloseNotification",
         str(kanal)],
        capture_output=True, text=True)
