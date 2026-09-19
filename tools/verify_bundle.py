"""Prüft ein fertiges PyInstaller-Programm darauf, ob es starten kann.

PyInstaller meldet Erfolg, auch wenn seine Import-Analyse ein Paket nicht
gefunden hat – das Programm bricht dann erst beim Start des Nutzers ab. Genau
so sind v1.4.3 und v1.4.4 für Windows, macOS und Linux ausgeliefert worden:
ohne `ytmd`, also ohne den gesamten Programmcode. Diese Prüfung macht daraus
einen Fehlschlag im Build statt einer Fehlermeldung beim Nutzer.

Aufruf: python tools/verify_bundle.py <programm>
"""

import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

# Ohne diese Pakete ist das Programm wertlos: ytmd ist der eigene Code,
# die übrigen sind die Abhängigkeiten, die erst über ytmd erreichbar sind –
# also genau das, was eine gescheiterte Analyse als Erstes verliert.
PFLICHT = ("ytmd", "ytmd.app", "ytmd.youtube", "yt_dlp", "mutagen", "requests")


def enthaltene_module(pfad):
    """Alle Modulnamen im Programm – aus dem äußeren Archiv und dem PYZ."""
    archiv = CArchiveReader(pfad)
    namen = set(archiv.toc)

    pyz = [e for e in archiv.toc if e.lower().endswith(".pyz")]
    if not pyz:
        raise SystemExit(f"FEHLER: {pfad} enthält kein PYZ-Archiv.")

    for eintrag in pyz:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pyz", delete=False) as f:
                f.write(archiv.extract(eintrag))
                tmp = f.name
            namen |= set(ZlibArchiveReader(tmp).toc)
        finally:
            if tmp:
                os.unlink(tmp)
    return namen


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Aufruf: python tools/verify_bundle.py <programm>")
    pfad = sys.argv[1]

    module = enthaltene_module(pfad)
    fehlend = [name for name in PFLICHT if name not in module]

    print(f"{pfad}: {len(module)} Einträge im Programm")
    if fehlend:
        print()
        print("FEHLER: Dem Programm fehlen Module, ohne die es nicht startet:")
        for name in fehlend:
            print(f"  - {name}")
        print()
        print("Das heißt fast immer, dass PyInstallers Import-Analyse das Paket")
        print("`ytmd` nicht gefunden hat und deshalb auch nichts kennt, was erst")
        print("darüber importiert wird. Siehe die Zeile \"Module search paths\"")
        print("in der Build-Ausgabe: Steht dort das Repo-Wurzelverzeichnis statt")
        print("des Ordners mit main.py, fehlt dem Aufruf ein `--paths`.")
        raise SystemExit(1)

    print("OK – alle Pflichtmodule sind enthalten.")


if __name__ == "__main__":
    main()
