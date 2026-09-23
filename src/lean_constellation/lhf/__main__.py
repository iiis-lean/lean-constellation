"""Standalone LHF CLI; does not construct an LC runtime."""
import argparse
import json
from pathlib import Path
import sys

from .lc_export import export_release
from .restructure_export import export_restructure
from .storage import load_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='LHF static workspace tools')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='export a fixed LC Native Release closure')
    export.add_argument('--repo', action='append', required=True, metavar='KEY=PATH')
    export.add_argument('--main-repo')
    export.add_argument('--release-id', required=True)
    export.add_argument('--release-commit', help='full commit when custom Release ref is unavailable')
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--source-path', action='append', help='include a corpus-relative file/directory (repeatable)')
    export.add_argument('--resource-path', action='append', help='include resources/items-relative KEY/path (repeatable)')
    export.add_argument('--no-sources', action='store_true', help='omit all source materials and origin records')
    restructure = commands.add_parser('export-restructure', help='export sealed schema-v2 Restructure acceptances')
    restructure.add_argument('--acceptance', action='append', required=True, metavar='KEY=DIR')
    restructure.add_argument('--main-repo')
    restructure.add_argument('--output', required=True, type=Path)
    validate = commands.add_parser('validate', help='load and validate static structure without Lean')
    validate.add_argument('workspace', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export-restructure':
            acceptances = {}
            for item in args.acceptance:
                key, sep, path = item.partition('=')
                if not sep or not path or key in acceptances:
                    raise ValueError('--acceptance requires unique KEY=DIR entries')
                acceptances[key] = Path(path)
            report = export_restructure(acceptances=acceptances, main_repo=args.main_repo, output=args.output)
        elif args.command == 'export':
            if args.no_sources and (args.source_path is not None or args.resource_path is not None):
                raise ValueError('--no-sources cannot be combined with material path selections')
            repos = {}
            for item in args.repo:
                key, sep, path = item.partition('=')
                if not sep or not path or key in repos:
                    raise ValueError('--repo requires unique KEY=PATH entries')
                repos[key] = Path(path)
            report = export_release(repo_paths=repos, main_repo=args.main_repo, release_id=args.release_id,
                                    release_commit=args.release_commit, output=args.output,
                                    source_paths=[] if args.no_sources else args.source_path,
                                    resource_paths=args.resource_path)
        else:
            data = load_workspace(args.workspace)
            report = {'main_repo': data.metadata.main_repo, 'repos': list(data.repos), 'valid': True}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'LHF: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
