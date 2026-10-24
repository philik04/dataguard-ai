"""Command-line entry point for DataGuard AI."""

import argparse
from pathlib import Path


def main() -> None:
    """Keep the greeting and expose the implemented project phases."""
    parser = argparse.ArgumentParser(description="DataGuard AI")
    commands = parser.add_subparsers(dest="command")
    profile = commands.add_parser("profile", help="Profile the nine local Olist CSVs")
    profile.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    profile.add_argument("--output-dir", type=Path, default=Path("reports/generated"))
    ingest = commands.add_parser(
        "ingest", help="Replace the nine raw PostgreSQL tables"
    )
    ingest.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    ingest.add_argument("--output-dir", type=Path, default=Path("reports/generated"))
    validate = commands.add_parser(
        "validate", help="Run read-only deterministic quality rules"
    )
    validate.add_argument("--output-dir", type=Path, default=Path("reports/generated"))
    experiment = commands.add_parser(
        "experiment", help="Create an isolated corruption pilot (max 1000 orders)"
    )
    experiment.add_argument("--orders", type=int, default=1000)
    experiment.add_argument("--seed", type=int, default=42)
    experiment.add_argument("--rate", default="0.07")
    experiment.add_argument(
        "--output-dir", type=Path, default=Path("reports/generated")
    )
    evaluate = commands.add_parser(
        "evaluate", help="Evaluate the existing pilot read-only"
    )
    evaluate.add_argument("--output-dir", type=Path, default=Path("reports/generated"))
    explain = commands.add_parser(
        "explain", help="Explain saved findings without database access"
    )
    explain.add_argument("--provider", choices=("offline", "openai"), default="offline")
    explain.add_argument(
        "--input", type=Path, default=Path("reports/generated/quality.json")
    )
    explain.add_argument("--output-dir", type=Path, default=Path("reports/generated"))
    explain.add_argument("--allow-paid-api", action="store_true")
    explain.add_argument("--fallback-offline", action="store_true")
    explain.add_argument("--model", default="gpt-4o-mini-2024-07-18")
    explain.add_argument("--timeout-seconds", type=float, default=30)
    explain.add_argument("--max-output-tokens", type=int, default=1200)
    explain.add_argument("--max-input-tokens", type=int, default=16000)
    explain.add_argument("--max-requests", type=int, default=3)
    explain.add_argument("--max-cost-usd", type=float, default=0.10)
    explain.add_argument("--input-usd-per-million", type=float, default=1.0)
    explain.add_argument("--output-usd-per-million", type=float, default=4.0)
    args = parser.parse_args()

    if args.command == "profile":
        # Import Pandas only when profiling; the original greeting stays lightweight.
        from dataguard_ai.ingestion.profiler import profile_dataset, write_reports

        try:
            report = profile_dataset(args.input_dir)
            write_reports(report, args.output_dir)
        except (OSError, ValueError) as error:
            parser.exit(1, f"Profiling failed: {error}\n")
        print(
            f"Profiled {len(report['tables'])} tables (Phase 1A: dataset exploration)."
        )
        print(f"Reports: {args.output_dir / 'profile.json'}")
        print(f"         {args.output_dir / 'profile.md'}")
    elif args.command == "ingest":
        import csv

        import psycopg

        from dataguard_ai.ingestion.database import DatabaseConfig
        from dataguard_ai.ingestion.loader import ingest_dataset, write_ingestion_report

        if args.output_dir.resolve().is_relative_to(args.input_dir.resolve()):
            parser.exit(1, "Output directory must be outside the raw input directory\n")
        try:
            report = ingest_dataset(DatabaseConfig.from_environment(), args.input_dir)
        except (OSError, ValueError, csv.Error) as error:
            parser.exit(1, f"Ingestion failed: {error}\n")
        except psycopg.Error as error:
            # PostgreSQL error context can contain raw field values: do not print it.
            parser.exit(
                1, f"Ingestion not confirmed committed (SQLSTATE {error.sqlstate}).\n"
            )
        try:
            destination = write_ingestion_report(report, args.output_dir)
        except OSError:
            parser.exit(
                1, "Database import committed, but writing ingestion.json failed.\n"
            )
        for table, counts in report["tables"].items():
            print(f"raw.{table}: {counts['database_rows']:,} rows (CSV count matched)")
        print(f"Committed all nine tables. Report: {destination}")
    elif args.command == "validate":
        import psycopg

        from dataguard_ai.ingestion.database import DatabaseConfig
        from dataguard_ai.quality.engine import validate_database
        from dataguard_ai.quality.reporting import write_quality_reports

        try:
            report = validate_database(DatabaseConfig.from_environment())
            write_quality_reports(report, args.output_dir)
        except (OSError, ValueError) as error:
            parser.exit(1, f"Validation failed: {error}\n")
        except psycopg.Error as error:
            parser.exit(
                1,
                f"Validation failed (SQLSTATE {error.sqlstate}); no success report.\n",
            )
        for rule in report["rules"]:
            print(
                f"{rule['rule_id']}: {rule['confirmed_violations']} violations, "
                f"{rule['warnings']} warnings"
            )
        print(f"Reports: {args.output_dir / 'quality.json'} and quality.md")
    elif args.command == "experiment":
        from decimal import Decimal, InvalidOperation

        import psycopg

        from dataguard_ai.corruption.experiment import run_experiment
        from dataguard_ai.corruption.planning import ExperimentConfig
        from dataguard_ai.corruption.reporting import write_experiment_reports
        from dataguard_ai.ingestion.database import DatabaseConfig

        try:
            if args.output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
                raise ValueError("Experiment reports must be outside data/raw")
            pilot = ExperimentConfig(args.orders, args.seed, Decimal(args.rate))
            report = run_experiment(DatabaseConfig.from_environment(), pilot)
        except (ValueError, InvalidOperation) as error:
            parser.exit(1, f"Experiment failed: {error}\n")
        except psycopg.Error as error:
            parser.exit(
                1, f"Experiment not confirmed committed (SQLSTATE {error.sqlstate}).\n"
            )
        try:
            write_experiment_reports(report, args.output_dir)
        except OSError:
            parser.exit(
                1,
                "Experiment committed; file export failed. "
                "Recover data from experiment_pilot._run.\n",
            )
        print(f"Committed {report['experiment_id']} in {report['experiment_schema']}")
        for kind, count in report["mutation_counts"].items():
            print(f"{kind}: {count}")
        print(
            f"New finding matches: {len(report['comparison']['new'])}; raw unchanged: "
            f"{report['source_integrity']['unchanged']}"
        )
        print(
            f"Reports: {args.output_dir / 'experiment.md'} and mutation_manifest.json"
        )
    elif args.command == "evaluate":
        import psycopg

        from dataguard_ai.evaluation.reporting import write_evaluation_reports
        from dataguard_ai.evaluation.runner import evaluate_database
        from dataguard_ai.ingestion.database import DatabaseConfig

        try:
            if args.output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
                raise ValueError("Evaluation reports must be outside data/raw")
            report = evaluate_database(DatabaseConfig.from_environment())
            write_evaluation_reports(report, args.output_dir)
        except (OSError, ValueError) as error:
            parser.exit(1, f"Evaluation failed: {error}\n")
        except psycopg.Error as error:
            parser.exit(
                1,
                f"Evaluation failed (SQLSTATE {error.sqlstate}); "
                "no successful report.\n",
            )
        metrics = report["finding_level"]
        events = report["mutation_event_level"]
        print(
            f"Finding-level TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}"
        )
        print(
            f"Precision={metrics['precision']}, recall={metrics['recall']}, "
            f"F1={metrics['f1']}"
        )
        print(f"Mutation events detected: {events['detected']}/{events['total']}")
        print(f"Reports: {args.output_dir / 'evaluation.json'} and evaluation.md")
    elif args.command == "explain":
        from dataguard_ai.ai.explanations import explain_saved_report
        from dataguard_ai.ai.providers import OfflineProvider
        from dataguard_ai.ai.reporting import write_explanation_reports

        if args.provider == "openai" and not args.allow_paid_api:
            parser.exit(
                1, "OpenAI calls disabled; explicit --allow-paid-api required.\n"
            )
        try:
            provider = OfflineProvider()
            if args.provider == "openai":
                from dataguard_ai.ai.openai_provider import OpenAIConfig, OpenAIProvider

                provider = OpenAIProvider(
                    OpenAIConfig(
                        model=args.model,
                        allow_paid=args.allow_paid_api,
                        timeout_seconds=args.timeout_seconds,
                        max_output_tokens=args.max_output_tokens,
                        max_input_tokens=args.max_input_tokens,
                        max_requests=args.max_requests,
                        max_cost_usd=args.max_cost_usd,
                        input_usd_per_million=args.input_usd_per_million,
                        output_usd_per_million=args.output_usd_per_million,
                    )
                )
            try:
                report = explain_saved_report(args.input, provider)
            except ValueError:
                if args.provider != "openai" or not args.fallback_offline:
                    raise
                report = explain_saved_report(args.input, OfflineProvider())
                report["fallback"] = "Explicitly requested after OpenAI failure"
                report["api_metadata"] = provider.metadata()
                print(
                    "OpenAI failed; using requested offline fallback. "
                    "Attempted API requests may have incurred cost."
                )
            write_explanation_reports(report, args.output_dir, input_path=args.input)
        except (OSError, ValueError) as error:
            parser.exit(1, f"Explanation failed: {error}\n")
        print(
            f"Explained {report['summary']['finding_count']} findings in "
            f"{report['summary']['group_count']} groups "
            f"using {report['provider']} reviewed selections."
        )
        print(f"Reports written to {args.output_dir}")
    else:
        print("DataGuard AI | Phase 0: repository foundation")
