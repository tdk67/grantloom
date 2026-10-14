"""Command-line interface for generating synthetic grant demo documents."""

import argparse
from pathlib import Path
import sys

from app.core.config import get_settings
from app.synthetic.generator import SyntheticDataGenerator


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Generate high-fidelity synthetic grant documents and evaluation manifests for GrantLoom."
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Target directory to write synthetic documents (default: config database.synthetic_path)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress console output during generation",
    )
    parser.add_argument(
        "--solicitations",
        "-s",
        type=int,
        default=20,
        help="Number of diverse funding solicitations to generate across agencies (default: 20)",
    )
    return parser.parse_args(args)


def main():
    args = parse_args()
    if args.output_dir is None:
        args.output_dir = Path(get_settings().database.synthetic_path)
    print(f"=== GrantLoom Synthetic Data Generator ===")
    print(f"Destination: {args.output_dir.resolve()}")
    print(f"Solicitation pipeline count: {args.solicitations}")

    generator = SyntheticDataGenerator(output_dir=args.output_dir)
    results = generator.generate_all(quiet=args.quiet, solicitation_count=args.solicitations)

    print("\nSummary of Generated Artifacts:")
    for file_info in results["files"]:
        print(f" - {file_info['filename']} ({file_info['size_bytes']:,} bytes)")
    print(f" - Manifest: {results['manifest_path']} ({results['fact_count']} planted benchmark facts)")
    print("=== Synthetic Generation Complete ===\n")


if __name__ == "__main__":
    main()
