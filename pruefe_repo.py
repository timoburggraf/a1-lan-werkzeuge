#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abnahmepruefung vor dem Veroeffentlichen
========================================

Ein `git push` in ein oeffentliches Repo ist nicht zurueckzunehmen: GitHub
indexiert sofort, Forks und Caches ueberleben jedes Loeschen. Deshalb dieselbe
Regel wie beim Drucken — geprueft wird maschinell, und der Befund sperrt.

Aufruf:
    python3 pruefe_repo.py            # alles, was git verfolgen wuerde
    python3 pruefe_repo.py --staged   # nur was zum Commit vorgemerkt ist

Rueckgabe 0 = sauber, 1 = Befund. Als pre-push-Hook eingehaengt.
"""

import re
import subprocess
import sys

# Was gesucht wird. Bewusst breit: lieber ein Fehlalarm, den man entkraeftet,
# als ein Schluessel, den man zurueckziehen muss.
MUSTER = [
    ("Anthropic-Schluessel", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-Schluessel", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("AWS-Schluessel", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bambu-Seriennummer", re.compile(r"\b0[01][A-Z][0-9A-Z]{10,}\b")),
    ("private IP", re.compile(r"\b(?:192\.168|10\.\d{1,3})\.\d{1,3}\.\d{1,3}\b")),
    ("Zugangscode im Klartext",
     re.compile(r"(?:ACCESS_CODE|access_code|passwort|password)\s*[=:]\s*"
                r"['\"][^'\"$({\s]{6,}['\"]")),
    ("privater Schluessel", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# Stellen, an denen ein Treffer erklaerbar ist: Platzhalter in der Doku und
# die Muster in dieser Pruefdatei selbst.
AUSNAHMEN = [
    re.compile(r"192\.168\.0\.X"),          # Platzhalter im README
    re.compile(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}\s*=\s*Beispiel"),
]


def dateien(nur_staged):
    if nur_staged:
        b = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    else:
        b = ["git", "ls-files"]
    roh = subprocess.run(b, capture_output=True, text=True).stdout
    return [z for z in roh.splitlines() if z.strip()]


def pruefen(nur_staged=False):
    befunde = []
    for pfad in dateien(nur_staged):
        if pfad == "pruefe_repo.py":        # die Muster stehen hier drin
            continue
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                zeilen = f.readlines()
        except (OSError, IsADirectoryError):
            continue
        for nr, zeile in enumerate(zeilen, 1):
            if any(a.search(zeile) for a in AUSNAHMEN):
                continue
            for name, m in MUSTER:
                if m.search(zeile):
                    befunde.append((pfad, nr, name))
    return befunde


def main():
    nur_staged = "--staged" in sys.argv
    befunde = pruefen(nur_staged)
    n = len(dateien(nur_staged))
    if not befunde:
        print("Repo-Abnahme bestanden — %d Datei(en) geprueft, nichts gefunden." % n)
        return 0
    print("!!! NICHT VEROEFFENTLICHEN — %d Fundstelle(n) in %d Datei(en):"
          % (len(befunde), n))
    for pfad, nr, name in befunde:
        # Nur Fundstelle nennen, nie den Wert — sonst steht das Geheimnis
        # anschliessend im Protokoll.
        print("  %s:%d  %s" % (pfad, nr, name))
    return 1


if __name__ == "__main__":
    sys.exit(main())
