"""Command-line interface for generating synthetic grant demo documents."""

import argparse
from pathlib import Path
import sys

from app.synthetic.generator import SyntheticDataGenerator


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Generate high-fidelity synthetic grant documents and evaluation manifests for GrantLoom."
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("data/synthetic"),
        help="Target directory to write synthetic documents (default: data/synthetic)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress console output during generation",
    )
    return parser.parse_args(args)


def main():
    args = parse_args()
    print(f"=== GrantLoom Synthetic Data Generator ===")
    print(f"Destination: {args.output_dir.resolve()}")

    generator = SyntheticDataGenerator(output_dir=args.output_dir)
    results = generator.generate_all(quiet=args.quiet)

    print("\nSummary of Generated Artifacts:")
    for file_info in results["files"]:
        print(f" - {file_info['filename']} ({file_info['size_bytes']:,} bytes)")
    print(f" - Manifest: {results['manifest_path']} ({results['fact_count']} planted benchmark facts)")
    print("=== Synthetic Generation Complete ===\n")


if __name__ == "__main__":
    main()
