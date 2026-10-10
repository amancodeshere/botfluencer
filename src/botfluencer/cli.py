"""Operator entrypoint. Runtime subcommands are added in subsequent stages."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(description="Botfluencer mock competition runtime")
    parser.add_argument("--version", action="version", version=version("botfluencer"))
    parser.parse_args()
    parser.print_help()
