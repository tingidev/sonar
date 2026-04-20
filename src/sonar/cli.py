"""CLI entrypoint for Sonar."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Sonar — data context for AI agents")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Discover and describe a data source")
    scan_parser.add_argument("connection_string", help="Database connection string")
    scan_parser.add_argument("--schemas", nargs="*", help="Schemas to scan (default: all)")

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    if args.command == "scan":
        print(f"Scanning: {args.connection_string}")
    elif args.command == "serve":
        print("Starting Sonar MCP server...")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
