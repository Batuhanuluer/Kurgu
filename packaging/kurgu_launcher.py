"""PyInstaller giriş noktası (paket içi göreli içe aktarmalar için ayrı betik)."""
import sys

from kurgu.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
