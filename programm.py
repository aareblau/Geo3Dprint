#!/usr/bin/env python3
"""Standard-Startpunkt fuer den gefuehrten Geo3Dprint-Assistenten."""

import sys
import traceback

import requests

import geo_assistent as _assistant
from geo_assistent import UserAbort, err, main
from geo_assistent import _prompt_length_m


def __getattr__(name: str):
    return getattr(_assistant, name)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserAbort:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except KeyboardInterrupt:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except ValueError as exc:
        err(str(exc))
        sys.exit(1)
    except requests.RequestException as exc:
        err(
            "Netzwerkfehler beim Laden der swisstopo-Daten. "
            "Bitte Internet/DNS pruefen und den Lauf erneut starten. "
            f"Details: {exc}"
        )
        sys.exit(1)
    except Exception:
        err("Unerwarteter Fehler")
        traceback.print_exc()
        sys.exit(1)
