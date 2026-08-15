#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zugriff auf den Bambu Lab A1 im lokalen Netz
============================================

Zwei Kanaele, beide ohne Cloud und ohne Zusatzhardware:

  Kamera  Port 6000, TLS. Nach einem 80-Byte-Anmeldepaket schiebt der Drucker
          fortlaufend JPEGs, jedes mit 16-Byte-Kopf davor (die ersten 4 Byte
          sind die Nutzlastlaenge, little endian).
  Status  Port 8883, MQTT ueber TLS. Nutzer "bblp", Passwort = Zugangscode.
          Ein "pushall" auf device/<SN>/request liefert den vollen Zustand
          auf device/<SN>/report.

Beides braucht Seriennummer und LAN-Zugangscode. Die kommen aus der Umgebung
(BAMBU_A1_*), die `lauf.sh` aus dem Vault fuellt. Ist dort nichts gesetzt,
wird ersatzweise die Konfiguration von Bambu Studio gelesen — praktisch beim
Ausprobieren von Hand, aber nicht der vorgesehene Weg.

Der Drucker wird ausschliesslich GELESEN. Dieses Modul kennt keinen einzigen
Befehl, der etwas am Drucker aendert.
"""

import json
import os
import pathlib
import socket
import ssl
import struct
import time

KAMERA_PORT = 6000
MQTT_PORT = 8883
STUDIO_KONF = pathlib.Path.home() / (
    ".var/app/com.bambulab.BambuStudio/config/BambuStudio/BambuStudio.conf")

# Der A1 liefert 1536x1080. Groesser wird das Bild nie, kleiner nur bei
# Uebertragungsfehlern — dann ist die Laengenangabe unplausibel.
MAX_BILD = 5_000_000


class DruckerFehler(RuntimeError):
    """Drucker nicht erreichbar oder Antwort unbrauchbar."""


# ---------------------------------------------------------------------------
#  Zugangsdaten
# ---------------------------------------------------------------------------
def zugangsdaten():
    """(ip, seriennummer, zugangscode) — aus der Umgebung, sonst aus Studio."""
    ip = os.environ.get("BAMBU_A1_IP")
    sn = os.environ.get("BAMBU_A1_SERIAL")
    code = os.environ.get("BAMBU_A1_ACCESS_CODE")
    if ip and sn and code:
        return ip, sn, code

    if not STUDIO_KONF.exists():
        raise DruckerFehler(
            "Keine Zugangsdaten: BAMBU_A1_IP/_SERIAL/_ACCESS_CODE nicht "
            "gesetzt und %s nicht gefunden. Ueber lauf.sh starten." % STUDIO_KONF)
    try:
        konf = json.loads(STUDIO_KONF.read_text())
        codes = konf["access_code"]
        sn2 = sn or next(iter(codes))
        if not ip:
            # Kein Rateversuch: eine fest verdrahtete Adresse koennte im
            # falschen Netz einen FREMDEN Drucker ansprechen.
            raise DruckerFehler(
                "BAMBU_A1_IP ist nicht gesetzt. Die Adresse wird nicht "
                "geraten — per Umgebungsvariable uebergeben (siehe README).")
        return ip, sn2, code or codes[sn2]
    except (KeyError, StopIteration, ValueError) as f:
        raise DruckerFehler("Studio-Konfiguration unlesbar: %s" % f)


# ---------------------------------------------------------------------------
#  Kamera
# ---------------------------------------------------------------------------
def _anmeldepaket(code):
    p = bytearray()
    p += struct.pack("<I", 0x40)      # Paketlaenge 64
    p += struct.pack("<I", 0x3000)    # Typ: Anmeldung
    p += struct.pack("<I", 0)
    p += struct.pack("<I", 0)
    p += b"bblp".ljust(32, b"\x00")
    p += code.encode("ascii").ljust(32, b"\x00")
    return bytes(p)


def bilder_holen(anzahl=1, abstand=0.0, timeout=15):
    """Liefert eine Liste von JPEG-Bytes frisch von der Druckerkamera.

    `abstand` ist die Pause zwischen zwei behaltenen Bildern in Sekunden.
    Das ist beim A1 wichtig: das Bett faehrt in Y, und weil die Kamera vorn
    unten sitzt, gibt jede Bettstellung einen anderen Blick aufs Werkstueck.
    Mehrere Bilder ueber ein paar Sekunden zeigen zusammen deutlich mehr als
    ein einzelnes.
    """
    ip, _sn, code = zugangsdaten()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    bilder = []
    try:
        with socket.create_connection((ip, KAMERA_PORT), timeout=timeout) as roh:
            with ctx.wrap_socket(roh, server_hostname=ip) as s:
                s.settimeout(timeout)
                s.sendall(_anmeldepaket(code))

                def lies(n):
                    puffer = b""
                    while len(puffer) < n:
                        stueck = s.recv(n - len(puffer))
                        if not stueck:
                            raise DruckerFehler("Kamera hat die Verbindung "
                                                "geschlossen")
                        puffer += stueck
                    return puffer

                naechstes = 0.0
                while len(bilder) < anzahl:
                    kopf = lies(16)
                    laenge = int.from_bytes(kopf[0:4], "little")
                    if not 0 < laenge < MAX_BILD:
                        raise DruckerFehler(
                            "Unplausible Bildlaenge %d — falscher Zugangscode?"
                            % laenge)
                    bild = lies(laenge)
                    jetzt = time.monotonic()
                    if jetzt < naechstes:
                        continue          # Bild verwerfen, Strom weiterlesen
                    if not (bild.startswith(b"\xff\xd8") and
                            bild.endswith(b"\xff\xd9")):
                        continue          # kein vollstaendiges JPEG
                    bilder.append(bild)
                    naechstes = jetzt + abstand
    except DruckerFehler:
        raise
    except (OSError, ssl.SSLError) as f:
        raise DruckerFehler("Kamera %s:%d nicht erreichbar: %s"
                            % (ip, KAMERA_PORT, f))
    return bilder


# ---------------------------------------------------------------------------
#  Status
# ---------------------------------------------------------------------------
#  Nur die Felder, die die Wache braucht. Der Drucker schickt gut 60.
FELDER = ("gcode_state", "mc_percent", "mc_remaining_time", "layer_num",
          "total_layer_num", "subtask_name", "gcode_file", "nozzle_temper",
          "bed_temper", "nozzle_target_temper", "bed_target_temper",
          "print_error", "hms", "wifi_signal", "spd_lvl", "print_type",
          "s_obj")


def hms_lesbar(hms):
    """HMS-Meldungen in die Schreibweise bringen, unter der Bambu sie fuehrt.

    Aus {"attr": 50331904, "code": 65543} wird "0300_0100_0001_0007" — genau
    so steht der Code in der Bambu-Wiki-Fehlerliste.
    """
    aus = []
    for m in hms or []:
        try:
            a, c = int(m["attr"]), int(m["code"])
            aus.append("%04X_%04X_%04X_%04X"
                       % (a >> 16, a & 0xFFFF, c >> 16, c & 0xFFFF))
        except (KeyError, TypeError, ValueError):
            aus.append(str(m))
    return aus


def status(timeout=12, roh=False):
    """Vollen Druckerzustand per MQTT holen. Wirft DruckerFehler bei Stille.

    `roh=True` gibt die ungefilterte Antwort zurueck — noetig fuer die
    AMS-Belegung (`ams`, `vt_tray`), die nicht in FELDER steht. Bleibt eine
    reine Leseoperation.
    """
    import paho.mqtt.client as mqtt

    ip, sn, code = zugangsdaten()
    gesammelt = {}
    fertig = []

    def bei_verbindung(c, u, flags, rc, props=None):
        if rc != 0:
            return
        c.subscribe("device/%s/report" % sn)
        c.publish("device/%s/request" % sn,
                  json.dumps({"pushing": {"sequence_id": "1",
                                          "command": "pushall"}}))

    def bei_nachricht(c, u, msg):
        try:
            teil = json.loads(msg.payload).get("print", {})
        except ValueError:
            return
        gesammelt.update(teil)
        # Die Vollmeldung enthaelt gcode_state; Teilmeldungen oft nicht.
        if "gcode_state" in gesammelt and not fertig:
            fertig.append(True)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="spaghetti-wache-%d" % os.getpid())
    c.username_pw_set("bblp", code)
    c.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    c.tls_insecure_set(True)
    c.on_connect = bei_verbindung
    c.on_message = bei_nachricht

    try:
        c.connect(ip, MQTT_PORT, keepalive=30)
    except OSError as f:
        raise DruckerFehler("MQTT %s:%d nicht erreichbar: %s"
                            % (ip, MQTT_PORT, f))

    c.loop_start()
    ende = time.monotonic() + timeout
    while not fertig and time.monotonic() < ende:
        time.sleep(0.2)
    c.loop_stop()
    try:
        c.disconnect()
    except OSError:
        pass

    if not gesammelt:
        raise DruckerFehler("Drucker antwortet nicht auf pushall (Zugangscode "
                            "falsch oder Drucker im Cloud-Modus?)")

    if roh:
        gesammelt["hms_codes"] = hms_lesbar(gesammelt.get("hms"))
        return gesammelt
    z = {k: gesammelt[k] for k in FELDER if k in gesammelt}
    z["hms_codes"] = hms_lesbar(gesammelt.get("hms"))
    return z


def ams_belegung(timeout=15):
    """Was liegt WIRKLICH in den Faechern? {index: (typ, farbe)}.

    Die Freigabeliste fuer einen Druckauftrag muss hieraus stammen, nicht aus
    der Erinnerung. Am 15.08.2026 zog ein Auftrag Fach 2 an, weil der Slicer
    dort ein Filament deklariert hatte — physisch lag darin PETG, und das hat
    den Extruder verstopft.
    """
    z = status(timeout=timeout, roh=True)
    belegung = {}
    for einheit in ((z.get("ams") or {}).get("ams") or []):
        for t in einheit.get("tray", []):
            typ = t.get("tray_type")
            if typ:
                belegung[int(t.get("id"))] = (typ, (t.get("tray_color") or "")[:6])
    return belegung


def faecher_mit(material, belegung=None):
    """Indizes der Faecher, in denen `material` liegt (z. B. "PLA")."""
    b = belegung if belegung is not None else ams_belegung()
    return sorted(i for i, (typ, _f) in b.items() if typ.upper() == material.upper())


def druckt(z):
    """Laeuft gerade ein Druck? PAUSE zaehlt mit — pausiert wird auch nach
    einem erkannten Fehler, das will man sehen."""
    return z.get("gcode_state") in ("RUNNING", "PAUSE", "PREPARE", "SLICING")


def kurzfassung(z):
    """Einzeiler fuer Protokoll und Meldungstext."""
    teile = [z.get("gcode_state", "?")]
    if z.get("subtask_name"):
        teile.append(str(z["subtask_name"]))
    if z.get("layer_num") is not None:
        teile.append("Schicht %s/%s" % (z.get("layer_num"),
                                        z.get("total_layer_num", "?")))
    if z.get("mc_percent") is not None:
        teile.append("%s%%" % z["mc_percent"])
    return ", ".join(teile)


if __name__ == "__main__":
    import sys
    if "--bild" in sys.argv:
        for i, b in enumerate(bilder_holen(3, abstand=5)):
            pfad = "/tmp/a1_%d.jpg" % i
            pathlib.Path(pfad).write_bytes(b)
            print(pfad, len(b), "Byte")
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
