"""Kurgu'yu başlat:  python -m kurgu [proje.kurgu]
Komut satırından dışa aktar:  python -m kurgu --export proje.kurgu cikti.mp4
"""
import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kurgu", description="Kurgu video editor")
    ap.add_argument("project", nargs="?", help=".kurgu project file")
    ap.add_argument("--export", metavar="OUTPUT", help="render the project to an MP4 and exit")
    ap.add_argument("--allow-network-paths", action="store_true",
                    help="allow media on network (UNC) paths in the project file")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print("Kurgu", __version__)
        return 0
    if args.export:
        if not args.project:
            ap.error("--export needs a project file")
        from .model import Project
        from .render import export
        from .safety import RemotePathsError
        try:
            proj = Project.load(args.project,
                                remote="allow" if args.allow_network_paths else "error")
        except RemotePathsError as e:
            print("The project refers to network paths (use --allow-network-paths if you "
                  "trust it):\n  " + "\n  ".join(e.paths), file=sys.stderr)
            return 2

        def prog(f):
            print(f"\r{f * 100:5.1f}%", end="", flush=True)
        export(proj, args.export, progress=prog)
        print("\n->", args.export)
        return 0
    from .app import run
    return run(args.project)


if __name__ == "__main__":
    sys.exit(main())
