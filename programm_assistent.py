#!/usr/bin/env python3
"""Neutraler Startpunkt fuer den gefuehrten Geo3Dprint-Assistenten."""

import sys
import traceback

import requests

import geo_assistent as assistant


if __name__ == "__main__":
    try:
        raise SystemExit(assistant.main())
    except assistant.UserAbort:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except KeyboardInterrupt:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except ValueError as exc:
        assistant.err(str(exc))
        sys.exit(1)
    except requests.RequestException as exc:
        assistant.err(
            "Netzwerkfehler beim Laden der swisstopo-Daten. "
            "Bitte Internet/DNS pruefen und den Lauf erneut starten. "
            f"Details: {exc}"
        )
        sys.exit(1)
    except Exception:
        assistant.err("Unerwarteter Fehler")
        traceback.print_exc()
        sys.exit(1)
