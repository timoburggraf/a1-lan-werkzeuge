#!/usr/bin/env bash
# Erzeugt die Testplatte (vier Wuerfel) neu: STLs -> Slice -> Vorschaubilder.
# Braucht Bambu Studio als Flatpak, aber KEINE laufende Oberflaeche.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
S="$HOME/.var/app/com.bambulab.BambuStudio/config/BambuStudio/system/BBL"

python3 wuerfel_erzeugen.py
flatpak run --filesystem=home --command=bambu-studio com.bambulab.BambuStudio \
  --load-settings "$S/machine/Bambu Lab A1 0.4 nozzle.json;$S/process/0.20mm Standard @BBL A1.json" \
  --load-filaments "$S/filament/Generic PLA @BBL A1.json" \
  --arrange 0 --slice 0 --export-3mf "Skiptest.gcode.3mf" --outputdir "$PWD" \
  Wuerfel_1.stl Wuerfel_2.stl Wuerfel_3.stl Wuerfel_4.stl 2>&1 \
  | grep -viE "\[(trace|debug)\]" | tail -3
python3 vorschau_nachruesten.py
echo "fertig: Skiptest_final.gcode.3mf"
