"""Dify definition validation and inert native resource staging."""

import argparse
from importlib.resources import as_file, files
from pathlib import Path

from k8s_stack_tooling.addons import ContractError, load_manifest
from k8s_stack_tooling.api.payloads import DEFAULT_DESTINATION, PayloadError, stage_payloads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--list-packages", action="store_true")
    args = parser.parse_args(argv)
    try:
        addon = load_manifest(args.root / "release/manifest.yaml")
        if addon.metadata.name != "dify":
            raise ContractError("expected the Dify add-on")
    except ContractError as error:
        parser.exit(1, f"{error}\n")
    if args.list_packages:
        for package in addon.spec.packages:
            print(package.path)
    else:
        print("Dify definition schema is valid")
    return 0


def stage(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Dify native model-sync code only.")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args(argv)
    try:
        with as_file(files("dify_tooling.payloads")) as source:
            stage_payloads(source, ["sync_models.py"], destination=args.destination)
    except (PayloadError, OSError):
        parser.exit(1, "Dify payload staging failed\n")
    return 0
