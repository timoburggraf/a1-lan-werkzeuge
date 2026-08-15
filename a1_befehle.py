#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Schreibende Befehle an den Bambu Lab A1
=======================================

**Achtung: Dieses Modul veraendert den Drucker.** Es ist bewusst von
`a1_drucker.py` getrennt, damit dort die Zusage „nur lesend" gilt und die
Spaghetti-Wache nachweislich nichts anfassen kann. Die Wache importiert
dieses Modul nicht.

Enthalten ist nur, was gebraucht und geprueft wurde:

  objekte()       Objektliste des laufenden Auftrags — Name, ID, Position.
                  Holt die gesliceten 3MF vom Drucker und liest sie aus.
  ueberspringen() Objekte vom weiteren Druck ausnehmen (skip_objects).
  hochladen()     Datei per FTPS auf die SD-Karte legen.
  platte_frei()   Kamera-Freigabe: ist das Druckbett leer?
  druck_starten() Druck starten — nur mit bestandener Kamera-Freigabe.

Absichtlich NICHT enthalten: pausieren und abbrechen. Es soll keinen Weg
geben, auf dem eine Automatik einen laufenden Druck von sich aus anhaelt;
dafuer gibt es den Druckerbildschirm. Starten ist etwas anderes und seit
14.08.2026 ausdruecklich gewuenscht (Timo: „dann sollst Du den Druck auch von
Dir aus starten") — aber nur hinter der Kamera-Sperre: ohne nachweislich
leere Platte faehrt hier nichts los.

Zum Ueberspringen (Stand Firmware 01.08.01.00, A1):
Bambu vergibt beim Slicen jedem Objekt eine Nummer, die im G-Code als
`; start printing object, unique label id: N` und als M624/M625-Klammer
auftaucht. Genau diese Nummern nimmt `skip_objects` — NICHT die IDs aus
`bbox_objects` in plate_1.json, die anders zaehlen. Beide lassen sich aus der
Datei ableiten; `objekte()` liefert die richtige.
"""

import ftplib
import json
import os
import re
import ssl
import time

import a1_drucker

MQTT_PORT = 8883
FTPS_PORT = 990


class BefehlFehler(RuntimeError):
    """Befehl abgelehnt oder Datei nicht erreichbar."""


# ---------------------------------------------------------------------------
#  FTPS
# ---------------------------------------------------------------------------
class _ImplizitFTPS(ftplib.FTP_TLS):
    """Bambu spricht FTPS mit sofortigem TLS (implicit), nicht AUTH TLS."""

    def __init__(self, *a, **k):
        self._sock = None
        super().__init__(*a, **k)

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, wert):
        if wert is not None and not isinstance(wert, ssl.SSLSocket):
            wert = self.context.wrap_socket(wert, server_hostname=self.host)
        self._sock = wert

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Wie das Original, aber ohne `conn.unwrap()`.

        Der FTP-Server des Druckers beendet die TLS-Datenverbindung nicht
        regelkonform. Das Original wartet daher beim Abbau bis zum Timeout,
        obwohl die Datei laengst vollstaendig uebertragen ist.
        """
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while True:
                puffer = fp.read(blocksize)
                if not puffer:
                    break
                conn.sendall(puffer)
                if callback:
                    callback(puffer)
        return self.voidresp()


def _ftps():
    ip, _sn, code = a1_drucker.zugangsdaten()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    f = _ImplizitFTPS(context=ctx)
    try:
        f.connect(ip, FTPS_PORT, timeout=30)
        f.login("bblp", code)
        f.prot_p()
    # ftplib.all_errors ist bereits ein Tupel — verschachteln wirft sonst
    # beim ERSTEN echten FTPS-Fehler einen TypeError und verdeckt die Ursache.
    except (OSError,) + tuple(ftplib.all_errors) as fehler:
        raise BefehlFehler("FTPS %s:%d: %s" % (ip, FTPS_PORT, fehler))
    return f


def hochladen(pfad, ziel=None, ams=None, ohne_pruefung=False,
              material=("PLA",)):
    """Datei auf die SD-Karte des Druckers legen. Gibt den Zielnamen zurueck.

    Prueft vorher `pruefe_gcode.py`. Am 15.08.2026 hat ein selbst gebauter
    Auftrag Filament-Index 2 angefordert, obwohl nur 0 und 1 deklariert
    waren — der Drucker zog daraufhin graues PETG aus AMS-Fach 2 und
    verstopfte die Vierfachkopplung. Diese Pruefung faengt genau das ab und
    ist deshalb nicht optional; `ohne_pruefung=True` nur mit gutem Grund.

    `material` ist die Freigabeliste der Werkstoffe. Sie ist noetig, weil die
    blosse Existenz eines Filamentindex nichts sagt: Deklariert der Slicer
    sechs Filamente, dann "existiert" auch das PETG in Fach 2 — gezogen werden
    darf es trotzdem nicht.
    """
    if not ohne_pruefung and pfad.endswith(".3mf"):
        try:
            import pruefe_gcode
            r = pruefe_gcode.pruefen(pfad, ams, material)
            for h in r["hinweise"]:
                print("  Hinweis: %s" % h)
            if r["befunde"]:
                raise BefehlFehler(
                    "Abnahmepruefung fehlgeschlagen, NICHT hochgeladen:\n  - "
                    + "\n  - ".join(r["befunde"]))
        except BefehlFehler:
            raise
        except Exception as f:
            # Eine kaputte Pruefung darf nicht blockieren, aber sie muss
            # auffallen — stillschweigend durchwinken waere das Schlimmste.
            print("WARNUNG: Abnahmepruefung nicht durchfuehrbar (%s)"
                  % str(f)[:120])
    ziel = ziel or os.path.basename(pfad)
    f = _ftps()
    try:
        with open(pfad, "rb") as g:
            f.storbinary("STOR /%s" % ziel, g)
    finally:
        try:
            f.quit()
        except Exception:
            pass
    return ziel


def holen(fernpfad, zielpfad):
    """Datei von der SD-Karte holen."""
    f = _ftps()
    try:
        with open(zielpfad, "wb") as g:
            f.retrbinary("RETR %s" % fernpfad, g.write)
    finally:
        try:
            f.quit()
        except Exception:
            pass
    return zielpfad


# ---------------------------------------------------------------------------
#  Objektliste des laufenden Auftrags
# ---------------------------------------------------------------------------
def _ids_aus_gcode(gcode):
    """{Objekt-ID: [xmin, ymin, xmax, ymax]} aus den Objektklammern im G-Code.

    Nur extrudierende Zuege zaehlen — Leerfahrten wuerden die Bereiche
    ineinanderlaufen lassen und die Zuordnung unbrauchbar machen.
    """
    bereiche = {}
    akt = None
    x = y = 0.0
    for zeile in gcode.splitlines():
        if zeile.startswith(";"):
            if "start printing object" in zeile:
                m = re.search(r"id:\s*(\d+)", zeile)
                akt = m.group(1) if m else None
            elif "stop printing object" in zeile:
                akt = None
            continue
        if not zeile.startswith(("G1", "G0")):
            continue
        mx = re.search(r"X([-\d.]+)", zeile)
        my = re.search(r"Y([-\d.]+)", zeile)
        me = re.search(r"\sE([-\d.]+)", zeile)
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if akt and me and float(me.group(1)) > 0:
            b = bereiche.setdefault(akt, [9e9, 9e9, -9e9, -9e9])
            b[0] = min(b[0], x)
            b[1] = min(b[1], y)
            b[2] = max(b[2], x)
            b[3] = max(b[3], y)
    return bereiche


def objekte(datei=None, zwischenablage="/tmp"):
    """Objekte des laufenden (oder angegebenen) Auftrags.

    Rueckgabe: Liste von {"id", "name", "bbox"} — "id" ist die Nummer, die
    `ueberspringen()` erwartet.
    """
    import zipfile

    if datei is None:
        z = a1_drucker.status()
        name = z.get("gcode_file") or z.get("subtask_name")
        if not name:
            raise BefehlFehler("Kein laufender Auftrag (Drucker meldet keine "
                               "Datei).")
        if not name.endswith(".3mf"):
            name += ".gcode.3mf"
        datei = os.path.join(zwischenablage, name)
        holen("/" + name, datei)

    z = zipfile.ZipFile(datei)
    gcode = z.read("Metadata/plate_1.gcode").decode("utf-8", "replace")
    bereiche = _ids_aus_gcode(gcode)
    try:
        bbox = json.loads(z.read("Metadata/plate_1.json"))["bbox_objects"]
    except Exception:
        bbox = []

    aus = []
    for gid in sorted(bereiche, key=int):
        b = bereiche[gid]
        treffer, abstand = None, 9e9
        for o in bbox:
            ob = o.get("bbox")
            if not ob or len(ob) < 4:
                continue
            d = sum(abs(p - q) for p, q in zip(b, ob))
            if d < abstand:
                abstand, treffer = d, o
        aus.append({"id": int(gid),
                    "name": treffer["name"] if treffer else "?",
                    "bbox": [round(v, 1) for v in b],
                    "abweichung": round(abstand, 1)})
    return aus


# ---------------------------------------------------------------------------
#  Ueberspringen
# ---------------------------------------------------------------------------
def ueberspringen(ids, timeout=15):
    """Objekte vom weiteren Druck ausnehmen.

    Wirkt erst ab der naechsten Schicht — die laufende wird noch fertig
    gedruckt. Rueckgabe: (erfolg, antwort, s_obj_danach).
    """
    import paho.mqtt.client as mqtt

    ip, sn, code = a1_drucker.zugangsdaten()
    ids = [int(i) for i in ids]
    antwort = {}
    danach = {}

    def bei_verbindung(c, u, flags, rc, props=None):
        if rc == 0:
            c.subscribe("device/%s/report" % sn)

    def bei_nachricht(c, u, msg):
        try:
            d = json.loads(msg.payload).get("print", {})
        except ValueError:
            return
        if d.get("command") == "skip_objects":
            antwort.update(d)
        if "s_obj" in d:
            danach["s_obj"] = d["s_obj"]

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="a1-befehl-%d" % os.getpid())
    c.username_pw_set("bblp", code)
    c.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    c.tls_insecure_set(True)
    c.on_connect = bei_verbindung
    c.on_message = bei_nachricht
    try:
        c.connect(ip, MQTT_PORT, keepalive=30)
    except OSError as f:
        raise BefehlFehler("MQTT %s:%d: %s" % (ip, MQTT_PORT, f))
    c.loop_start()
    time.sleep(1.5)

    # `timestamp` steht so in der Bambu-Referenz; ohne das Feld hat die
    # Firmware den Befehl in einem frueheren Versuch abgelehnt.
    c.publish("device/%s/request" % sn, json.dumps(
        {"print": {"sequence_id": str(int(time.time()) % 100000),
                   "command": "skip_objects",
                   "timestamp": int(time.time()),
                   "obj_list": ids}}))

    ende = time.monotonic() + timeout
    while not antwort and time.monotonic() < ende:
        time.sleep(0.2)

    # Zustand nachfragen, damit s_obj wirklich vom Drucker kommt
    c.publish("device/%s/request" % sn, json.dumps(
        {"pushing": {"sequence_id": "1", "command": "pushall"}}))
    ende = time.monotonic() + 8
    while "s_obj" not in danach and time.monotonic() < ende:
        time.sleep(0.2)

    c.loop_stop()
    try:
        c.disconnect()
    except OSError:
        pass

    fehler = antwort.get("err_code") or antwort.get("result") == "fail"
    erfolg = bool(antwort) and not fehler
    return erfolg, antwort, danach.get("s_obj")


# ---------------------------------------------------------------------------
#  Kamera-Freigabe vor dem Start
# ---------------------------------------------------------------------------
PLATTE_PROMPT = """\
Du pruefst vor dem Start eines 3D-Drucks, ob die Druckplatte wirklich leer ist.

Die Kamera des Bambu Lab A1 sitzt vorn unten und blickt flach ueber das Bett.
Du siehst das ganze Kamerabild. Nach einem Druckende parkt das Bett meist
ganz vorn — dann fuellt die Platte den unteren Bildbereich gross aus. Das
helle Druckergehaeuse am Bildrand ist der Drucker selbst und kein Gegenstand
auf der Platte. Die leere Platte ist eine dunkle, koernige Flaeche mit
eingepraegter Kontur; blasse Umrisse frueherer Drucke auf der Oberflaeche
sind normale Gebrauchsspuren, keine Objekte.

Antworte "frei" nur, wenn du die Platte tatsaechlich SIEHST und sie leer ist.
Alles andere ist "belegt": ein fertiges oder angefangenes Teil, Filamentreste,
ein Spachtel oder Werkzeug, ein Klumpen, oder auch nur eine Stelle, die du
nicht beurteilen kannst. Siehst du die Platte gar nicht, ist das ebenfalls
"belegt" — im Zweifel wird nicht gedruckt.

Ein Fehlurteil "frei" laesst den Drucker in ein vorhandenes Teil fahren.
Die Begruendung ist ein kurzer deutscher Satz.
"""

PLATTE_SCHEMA = {
    "type": "object",
    "properties": {
        "lage": {"type": "string", "enum": ["frei", "belegt"]},
        "sicherheit": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
        "begruendung": {"type": "string"},
    },
    "required": ["lage", "sicherheit", "begruendung"],
    "additionalProperties": False,
}


def platte_frei(bilder=3, abstand=5, modell="claude-opus-5", ablegen=None):
    """Kamera-Freigabe: ist die Druckplatte leer?

    Rueckgabe: (frei, befund). `frei` ist nur dann True, wenn das Modell die
    Platte gesehen hat UND sie leer ist UND es sich dabei nicht unsicher ist.
    Jede Stoerung fuehrt zu False — die Sperre faellt zur sicheren Seite.
    """
    import base64
    import io

    import anthropic
    from PIL import Image

    roh = a1_drucker.bilder_holen(bilder, abstand)
    if not roh:
        return False, {"lage": "belegt", "sicherheit": "hoch",
                       "begruendung": "Keine Kamerabilder erhalten."}

    inhalt = []
    # Bewusst KEIN Zuschnitt: die Freigabe wird typischerweise nach einem
    # Druckende gebraucht, und dann parkt der A1 das Bett ganz vorn — die
    # Platte fuellt das untere Bilddrittel, das der Druck-Zuschnitt der
    # Wache genau wegschneiden wuerde. Gemessen am 14.08.: mit Zuschnitt
    # verweigerte die Freigabe eine leere, bestens sichtbare Platte.
    for i, b in enumerate(roh):
        bild = Image.open(io.BytesIO(b)).convert("RGB")
        w, h = bild.size
        kante = 1152
        if max(w, h) > kante:
            f = kante / float(max(w, h))
            bild = bild.resize((int(w * f), int(h * f)), Image.LANCZOS)
        if ablegen:
            bild.save(os.path.join(ablegen, "platte_%d.jpg" % (i + 1)),
                      quality=90)
        puffer = io.BytesIO()
        bild.save(puffer, format="JPEG", quality=88)
        inhalt.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(puffer.getvalue()).decode()}})
    inhalt.append({"type": "text",
                   "text": "Ist die Druckplatte leer und startbereit?"})

    try:
        antwort = anthropic.Anthropic().messages.create(
            model=modell, max_tokens=2000, system=PLATTE_PROMPT,
            output_config={"effort": "low",
                           "format": {"type": "json_schema",
                                      "schema": PLATTE_SCHEMA}},
            messages=[{"role": "user", "content": inhalt}])
        befund = json.loads(next(b.text for b in antwort.content
                                 if b.type == "text"))
    except Exception as f:
        return False, {"lage": "belegt", "sicherheit": "hoch",
                       "begruendung": "Pruefung fehlgeschlagen: %s" % str(f)[:150]}

    frei = befund["lage"] == "frei" and befund["sicherheit"] != "niedrig"
    return frei, befund


# ---------------------------------------------------------------------------
#  Druck starten
# ---------------------------------------------------------------------------
def druck_starten(dateiname, ams_fach=0, ohne_kamerafreigabe=False,
                  bilder_nach=None, timeout=20, nivellieren=True):
    """Druck einer Datei von der SD-Karte starten.

    Die Kamera-Freigabe ist Pflicht: ohne nachgewiesen leere Platte wird
    nicht gestartet. `ohne_kamerafreigabe=True` umgeht das nur fuer Faelle,
    in denen ein Mensch danebensteht und es ausdruecklich will.

    Rueckgabe: (gestartet, meldung, befund_der_platte)
    """
    import paho.mqtt.client as mqtt

    ip, sn, code = a1_drucker.zugangsdaten()

    zustand = a1_drucker.status()
    if a1_drucker.druckt(zustand):
        return False, ("Es laeuft bereits ein Druck: %s"
                       % a1_drucker.kurzfassung(zustand)), None

    befund = None
    if not ohne_kamerafreigabe:
        frei, befund = platte_frei(ablegen=bilder_nach)
        if not frei:
            return False, ("Kamera-Freigabe verweigert: %s"
                           % befund.get("begruendung")), befund

    kurz = dateiname[:-len(".gcode.3mf")] if dateiname.endswith(".gcode.3mf") \
        else os.path.splitext(dateiname)[0]
    quittung = {}

    def bei_verbindung(c, u, flags, rc, props=None):
        if rc == 0:
            c.subscribe("device/%s/report" % sn)

    def bei_nachricht(c, u, msg):
        try:
            d = json.loads(msg.payload).get("print", {})
        except ValueError:
            return
        if d.get("command") in ("project_file", "push_status"):
            quittung.update(d)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="a1-start-%d" % os.getpid())
    c.username_pw_set("bblp", code)
    c.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    c.tls_insecure_set(True)
    c.on_connect = bei_verbindung
    c.on_message = bei_nachricht
    try:
        c.connect(ip, MQTT_PORT, keepalive=30)
    except OSError as f:
        return False, "MQTT %s: %s" % (ip, f), befund
    c.loop_start()
    time.sleep(1.5)

    befehl = {"print": {
        "sequence_id": str(int(time.time()) % 100000),
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "url": "file:///sdcard/%s" % dateiname,
        "subtask_name": kurz,
        "use_ams": True,
        # Ein Fach je Filament; fuer Mehrfarbdrucke eine Liste uebergeben,
        # z. B. [0, 1] = Filament 1 -> Fach 0, Filament 2 -> Fach 1.
        "ams_mapping": ([int(f) for f in ams_fach]
                        if isinstance(ams_fach, (list, tuple))
                        else [int(ams_fach)]),
        # Der Drucker soll sein eigenes Ritual fahren — Nivellierung und
        # Kalibrierung kommen aus dem Start-G-Code der Datei.
        # AUSNAHME Fortsetzungsdruck: dort liegt Werkstueck auf der Platte,
        # und `G29` vermisst genau dessen Grundflaeche. Die Duese taeste auf
        # Kunststoff und `M500` schriebe das falsche Mesh fest —
        # nivellieren=False setzen (siehe fortsetzung_bauen.py).
        "bed_leveling": bool(nivellieren),
        "flow_cali": True,
        "vibration_cali": True,
        "layer_inspect": False,
        "timelapse": False,
        "bed_type": "textured_plate",
        "profile_id": "0", "project_id": "0",
        "subtask_id": "0", "task_id": "0",
    }}
    c.publish("device/%s/request" % sn, json.dumps(befehl))

    ende = time.monotonic() + timeout
    gestartet = False
    while time.monotonic() < ende:
        time.sleep(1.0)
        try:
            z = a1_drucker.status(timeout=6)
        except Exception:
            continue
        if z.get("gcode_state") in ("RUNNING", "PREPARE", "SLICING"):
            gestartet = True
            break

    c.loop_stop()
    try:
        c.disconnect()
    except OSError:
        pass

    if gestartet:
        return True, "Druck gestartet: %s" % kurz, befund
    return False, ("Kein Start erkannt. Quittung: %s"
                   % json.dumps(quittung, ensure_ascii=False)[:300]), befund


# ---------------------------------------------------------------------------
#  Stoerung quittieren (Druck FORTSETZEN — niemals anhalten)
# ---------------------------------------------------------------------------
def _print_befehl(nutzlast, timeout=10):
    """Einen print-Befehl senden und die Quittung einsammeln."""
    import paho.mqtt.client as mqtt

    ip, sn, code = a1_drucker.zugangsdaten()
    ant = []

    def bei_verbindung(c, u, flags, rc, props=None):
        if rc == 0:
            c.subscribe("device/%s/report" % sn)

    def bei_nachricht(c, u, msg):
        try:
            d = json.loads(msg.payload).get("print", {})
        except ValueError:
            return
        if d.get("command") == nutzlast["print"]["command"] \
                and ("result" in d or "err_code" in d):
            ant.append(d)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="a1-quitt-%d" % os.getpid())
    c.username_pw_set("bblp", code)
    c.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    c.tls_insecure_set(True)
    c.on_connect = bei_verbindung
    c.on_message = bei_nachricht
    c.connect(ip, MQTT_PORT, keepalive=30)
    c.loop_start()
    time.sleep(1.2)
    c.publish("device/%s/request" % sn, json.dumps(nutzlast))
    ende = time.monotonic() + timeout
    while not ant and time.monotonic() < ende:
        time.sleep(0.2)
    c.loop_stop()
    try:
        c.disconnect()
    except OSError:
        pass
    return ant[0] if ant else {}


def filament_wiederholen():
    """Der „Wiederholen"-Knopf bei Filamentfehlern (verklemmte Spule o. ae.).

    Entspricht dem Wegdruecken der Meldung am Bildschirm: die AMS versucht
    den Einzug erneut. Setzt den Druck FORT — haelt nie an.
    """
    return _print_befehl({"print": {"sequence_id": str(int(time.time()) % 100000),
                                    "command": "ams_control",
                                    "param": "resume"}})


def fortsetzen():
    """Pausierten Druck fortsetzen (print.resume). Setzt FORT — haelt nie an."""
    return _print_befehl({"print": {"sequence_id": str(int(time.time()) % 100000),
                                    "command": "resume", "param": ""}})


def druck_stoppen(grund):
    """Laufenden Druck abbrechen. NUR bei massivem, bestaetigtem Fehler.

    Regelaenderung vom 15.08.2026. Bis dahin galt: niemals selbst stoppen.
    Nach einem Testdruck, bei dem sich alles vom Bett loeste und die Duese
    45 Minuten lang ins Leere druckte, hat Timo das ausdruecklich geaendert:

        „So was musst Du auch sofort erkennen und musst den Druck dann auch
         eigenmaechtig stoppen. Also bei solchen massiven Fehlern."

    Die alte Zurueckhaltung gilt weiter fuer KLEINIGKEITEN — ein einzelner
    Faden, ein Verdacht, eine sich hebende Ecke: da wird gemeldet, nicht
    gestoppt. Gestoppt wird nur, was zweifach bestaetigt und gross ist:
    Spaghetti oder gar nichts mehr auf dem Bett.

    `grund` wird protokolliert und gemeldet — ein Abbruch ohne nachlesbare
    Begruendung darf es nicht geben.
    """
    antwort = _print_befehl({"print": {
        "sequence_id": str(int(time.time()) % 100000),
        "command": "stop", "param": ""}})
    zeile = {"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "aktion": "STOP",
             "grund": grund, "antwort": antwort}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "abbrueche.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError:
        pass
    erfolg = bool(antwort) and not antwort.get("err_code")
    return erfolg, antwort


def aufloesen(begriffe, liste=None):
    """Namen oder Nummern in Objekt-IDs uebersetzen.

    Erlaubt ist die ID selbst, der volle Name oder ein eindeutiges Stueck
    davon — "Wuerfel_3" genuegt, "Front" auch, solange es nur einmal passt.
    """
    liste = liste if liste is not None else objekte()
    gueltig = {o["id"] for o in liste}
    aus = []
    for b in begriffe:
        if b.isdigit() and int(b) in gueltig:
            aus.append(int(b))
            continue
        treffer = [o for o in liste if b.lower() in o["name"].lower()]
        if not treffer:
            raise BefehlFehler("Kein Objekt passt auf %r. Vorhanden: %s"
                               % (b, ", ".join(o["name"] for o in liste)))
        if len(treffer) > 1:
            raise BefehlFehler("%r passt auf mehrere: %s"
                               % (b, ", ".join(o["name"] for o in treffer)))
        aus.append(treffer[0]["id"])
    return aus


if __name__ == "__main__":
    import sys

    def _zeigen(liste, uebersprungen=()):
        for o in liste:
            marke = "  [uebersprungen]" if o["id"] in (uebersprungen or ()) else ""
            print("  %-6d %-30s x %6.1f-%6.1f  y %6.1f-%6.1f%s"
                  % (o["id"], o["name"], o["bbox"][0], o["bbox"][2],
                     o["bbox"][1], o["bbox"][3], marke))

    if "--liste" in sys.argv:
        z = a1_drucker.status()
        print("Auftrag: %s" % a1_drucker.kurzfassung(z))
        _zeigen(objekte(), z.get("s_obj") or ())
    elif "--skip" in sys.argv:
        begriffe = sys.argv[sys.argv.index("--skip") + 1:]
        if not begriffe:
            raise SystemExit("Nutzung: a1_befehle.py --skip <Name|ID> [...]")
        liste = objekte()
        ids = aufloesen(begriffe, liste)
        namen = [o["name"] for o in liste if o["id"] in ids]
        print("ueberspringe: %s" % ", ".join("%s (%d)" % (n, i)
                                             for n, i in zip(namen, ids)))
        ok, ant, s = ueberspringen(ids)
        print("angenommen:", ok)
        print("Antwort:", json.dumps(ant, ensure_ascii=False))
        print("uebersprungen laut Drucker (s_obj):", s)
        if not ok:
            raise SystemExit(1)
    else:
        print(__doc__)
