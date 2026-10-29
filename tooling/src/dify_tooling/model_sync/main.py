"""Generate a reviewed projection or explicitly launch the installed native Job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from k8s_stack_tooling.api.jobs import JobError, job_api_client
from k8s_stack_tooling.gateway_catalog.catalog import GatewayCatalog
from k8s_stack_tooling.gateway_catalog.metadata import MetadataError, load_json
from k8s_stack_tooling.gateway_catalog.values import write_values
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from pydantic import ValidationError

from dify_tooling.model_sync.launcher import launch
from dify_tooling.model_sync.projection import build_projection


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual Dify model-sync operations.")
    commands = parser.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project", help="Generate reviewed Dify projection and roles.")
    project.add_argument("--catalog", type=Path, required=True)
    project.add_argument("--metadata", type=Path, required=True)
    project.add_argument("--output", type=Path)
    project.add_argument("--write", action="store_true")
    run = commands.add_parser(
        "launch", help="Create a one-shot Job from the installed inert template."
    )
    run.add_argument("--context", required=True)
    run.add_argument("--namespace", default="frontend-dify")
    run.add_argument("--catalog-namespace", default="frontend-studio")
    run.add_argument("--mode", choices=["preview", "apply"], default="preview")
    run.add_argument("--expected-projection-revision", required=True)
    run.add_argument("--max-global-models", type=int, default=27)
    run.add_argument("--wait-seconds", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        if args.command == "launch":
            if not args.context.strip() or not 0 <= args.wait_seconds <= 86400:
                raise MetadataError("explicit context and bounded wait are required")
            with job_api_client(context=args.context) as api:
                result = launch(
                    client.CoreV1Api(api),
                    client.BatchV1Api(api),
                    catalog_namespace=args.catalog_namespace,
                    namespace=args.namespace,
                    mode=args.mode,
                    expected=args.expected_projection_revision,
                    max_global=args.max_global_models,
                    wait_seconds=args.wait_seconds,
                )
            print(json.dumps(result, sort_keys=True))
            return 1 if result["status"] in {"failed", "timeout", "uncertain"} else 0
        if args.write and args.output is None:
            raise MetadataError("--write requires an explicit --output values file")
        catalog = GatewayCatalog.model_validate(load_json(args.catalog.read_text()))
        projection = build_projection(catalog, load_json(args.metadata.read_text()))
        additions = {
            "frontendDify": {"modelSync": {"projection": projection}},
            "authKeycloak": {"difyAgentgatewayClientRoles": projection["roles"]},
        }
        print(
            json.dumps(
                {
                    "projection_revision": projection["projection_revision"],
                    "selected": len(projection["models"]),
                    "proposed_values": additions,
                    "missing_metadata": sum(m["metadata"] is None for m in projection["models"]),
                },
                sort_keys=True,
            )
        )
        if args.write:
            write_values(args.output, additions)
        return 0
    except (MetadataError, JobError) as error:
        print(f"Model-sync failed: {error}", file=sys.stderr)
        return 1
    except (
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        yaml.YAMLError,
        ApiException,
        ConfigException,
    ):
        print(
            "Model-sync failed; check contract, freshness, permissions and approved inputs.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
