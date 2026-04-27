#!/usr/bin/env python3
"""
NextCloud Sherpa — main.py
Layer 4 Operational Unit for Raj Sadan

Performs CRUD operations on NextCloud via the API service on Pi.
Supports both structured (--op) and natural language (--prompt) invocation.

Structured usage:
  python sherpa/nextcloud/main.py --op state-save --domain finance --key budget --data '{"monthly":50000}'
  python sherpa/nextcloud/main.py --op state-load --domain finance --key budget
  python sherpa/nextcloud/main.py --op list --path /raj-sadan/state
  python sherpa/nextcloud/main.py --op health
  python sherpa/nextcloud/main.py --op upload --path /raj-sadan/docs/file.txt --file /local/file.txt

Natural language usage:
  python sherpa/nextcloud/main.py --prompt "save my monthly budget of 50000 to finance"
  python sherpa/nextcloud/main.py --prompt "what files are in the health domain?"
  python sherpa/nextcloud/main.py --prompt "load my career goals"
"""

import argparse
import subprocess
import json
import sys
import os
import urllib.request
import urllib.error

GATEWAY = os.path.join(os.path.dirname(__file__), '..', '..', 'gateway', 'nextcloud.cjs')
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
NL_MODEL = 'qwen2.5-coder:7b'
API_BASE = os.environ.get('NEXTCLOUD_API_URL', 'http://192.168.1.100:3481')

DOMAINS = ['finance', 'health', 'house-ops', 'infrastructure', 'career']

FILE_OPS = ['list', 'stat', 'exists', 'upload', 'download', 'delete', 'mkdir', 'move', 'copy', 'search']
STATE_OPS = ['state-save', 'state-load', 'state-list', 'state-versions', 'state-restore']
SYSTEM_OPS = ['health', 'disk', 'init']
ALL_OPS = FILE_OPS + STATE_OPS + SYSTEM_OPS


# ─── Gateway Invocation ─────────────────────────────────────────────────────

def run_gateway(flags: list[str], dry_run: bool = False) -> dict:
    """
    Invoke gateway/nextcloud.cjs with the given flags.
    Returns { success, data, raw_output }.
    """
    cmd = ['node', GATEWAY] + flags

    if dry_run:
        print(f"[DRY RUN] Would execute: {' '.join(cmd)}", file=sys.stderr)
        return {'success': True, 'data': {'dry_run': True, 'command': cmd}}

    print(f"[nextcloud-sherpa] Running: node nextcloud.cjs {' '.join(flags)}", file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, 'NEXTCLOUD_API_URL': API_BASE},
        )

        raw = result.stdout.strip()
        stderr = result.stderr.strip()

        if stderr:
            print(f"[nextcloud-sherpa] {stderr}", file=sys.stderr)

        if result.returncode != 0:
            return {'success': False, 'error': stderr or raw or f'Exit code {result.returncode}', 'raw': raw}

        # Try to parse as JSON
        try:
            data = json.loads(raw)
            return {'success': True, 'data': data, 'raw': raw}
        except json.JSONDecodeError:
            return {'success': True, 'data': raw, 'raw': raw}

    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Gateway request timed out (30s)'}
    except FileNotFoundError:
        return {'success': False, 'error': f'Node.js not found or gateway missing: {GATEWAY}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─── Operation Router ───────────────────────────────────────────────────────

def route_operation(op: str, args_ns: argparse.Namespace, dry_run: bool = False) -> dict:
    """Map --op to gateway flags."""
    flags = []

    if op == 'health':
        flags = ['--health']

    elif op == 'disk':
        flags = ['--disk']

    elif op == 'init':
        flags = ['--init']

    elif op == 'list':
        flags = ['--list']
        if args_ns.path:
            flags += ['--path', args_ns.path]

    elif op == 'stat':
        if not args_ns.path:
            return {'success': False, 'error': 'stat requires --path'}
        flags = ['--stat', '--path', args_ns.path]

    elif op == 'exists':
        if not args_ns.path:
            return {'success': False, 'error': 'exists requires --path'}
        flags = ['--exists', '--path', args_ns.path]

    elif op == 'upload':
        if not args_ns.path or not args_ns.file:
            return {'success': False, 'error': 'upload requires --path and --file'}
        flags = ['--upload', '--path', args_ns.path, '--file', args_ns.file]

    elif op == 'download':
        if not args_ns.path:
            return {'success': False, 'error': 'download requires --path'}
        flags = ['--download', '--path', args_ns.path]
        if args_ns.out:
            flags += ['--out', args_ns.out]

    elif op == 'delete':
        if not args_ns.path:
            return {'success': False, 'error': 'delete requires --path'}
        flags = ['--delete', '--path', args_ns.path]

    elif op == 'mkdir':
        if not args_ns.path:
            return {'success': False, 'error': 'mkdir requires --path'}
        flags = ['--mkdir', '--path', args_ns.path]

    elif op == 'move':
        if not args_ns.path_from or not args_ns.path_to:
            return {'success': False, 'error': 'move requires --from and --to'}
        flags = ['--move', '--from', args_ns.path_from, '--to', args_ns.path_to]

    elif op == 'copy':
        if not args_ns.path_from or not args_ns.path_to:
            return {'success': False, 'error': 'copy requires --from and --to'}
        flags = ['--copy', '--from', args_ns.path_from, '--to', args_ns.path_to]

    elif op == 'search':
        if not args_ns.query:
            return {'success': False, 'error': 'search requires --query'}
        flags = ['--search', '--query', args_ns.query]
        if args_ns.path:
            flags += ['--path', args_ns.path]

    elif op == 'state-save':
        if not args_ns.domain or not args_ns.key or args_ns.data is None:
            return {'success': False, 'error': 'state-save requires --domain, --key, --data'}
        data_str = args_ns.data if isinstance(args_ns.data, str) else json.dumps(args_ns.data)
        flags = ['--state-save', '--domain', args_ns.domain, '--key', args_ns.key, '--data', data_str]

    elif op == 'state-load':
        if not args_ns.domain or not args_ns.key:
            return {'success': False, 'error': 'state-load requires --domain and --key'}
        flags = ['--state-load', '--domain', args_ns.domain, '--key', args_ns.key]

    elif op == 'state-list':
        if not args_ns.domain:
            return {'success': False, 'error': 'state-list requires --domain'}
        flags = ['--state-list', '--domain', args_ns.domain]

    elif op == 'state-versions':
        if not args_ns.domain or not args_ns.key:
            return {'success': False, 'error': 'state-versions requires --domain and --key'}
        flags = ['--state-versions', '--domain', args_ns.domain, '--key', args_ns.key]

    elif op == 'state-restore':
        if not args_ns.domain or not args_ns.key or not args_ns.timestamp:
            return {'success': False, 'error': 'state-restore requires --domain, --key, --timestamp'}
        flags = ['--state-restore', '--domain', args_ns.domain, '--key', args_ns.key,
                 '--timestamp', args_ns.timestamp]

    else:
        return {'success': False, 'error': f'Unknown operation: {op}. Valid: {", ".join(ALL_OPS)}'}

    return run_gateway(flags, dry_run=dry_run)


# ─── Natural Language Parser ────────────────────────────────────────────────

def parse_natural_language(prompt: str) -> dict:
    """
    Use Ollama to parse a natural language prompt into a structured operation.
    Returns { op, domain, key, data, path, query, ... }
    """
    system_prompt = f"""You are a NextCloud operation parser for the Raj Sadan home assistant system.
Parse the user's natural language request into a structured JSON command.

Available operations: {json.dumps(ALL_OPS)}
Available domains: {json.dumps(DOMAINS)}

Return ONLY valid JSON with these fields (omit fields that are not needed):
{{
  "op": "<operation>",
  "domain": "<domain or null>",
  "key": "<state key or null>",
  "data": <json data object or null>,
  "path": "<file path or null>",
  "query": "<search query or null>",
  "path_from": "<source path for move/copy or null>",
  "path_to": "<dest path for move/copy or null>"
}}

Examples:
- "save my budget" → {{"op": "state-save", "domain": "finance", "key": "budget", "data": null}}
- "load career goals" → {{"op": "state-load", "domain": "career", "key": "goals"}}
- "list files in health" → {{"op": "state-list", "domain": "health"}}
- "check disk usage" → {{"op": "disk"}}
- "what files are in /raj-sadan" → {{"op": "list", "path": "/raj-sadan"}}
- "is the service healthy" → {{"op": "health"}}

Return ONLY the JSON object, no explanation."""

    payload = json.dumps({
        'model': NL_MODEL,
        'prompt': prompt,
        'system': system_prompt,
        'stream': False,
        'options': {'temperature': 0.1},
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            f'{OLLAMA_URL}/api/generate',
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode('utf-8'))
            raw_response = response.get('response', '').strip()

            # Extract JSON from response
            json_start = raw_response.find('{')
            json_end = raw_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(raw_response[json_start:json_end])
                return {'success': True, 'data': parsed}

            return {'success': False, 'error': f'LLM did not return valid JSON: {raw_response}'}

    except urllib.error.URLError as e:
        return {'success': False, 'error': f'Ollama not reachable at {OLLAMA_URL}: {e}'}
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'Failed to parse LLM response: {e}'}
    except Exception as e:
        return {'success': False, 'error': f'NL parsing failed: {e}'}


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='NextCloud Sherpa — CRUD operations via Raj Sadan NextCloud API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Operations: health, disk, init,
            list, stat, exists, upload, download, delete, mkdir, move, copy, search,
            state-save, state-load, state-list, state-versions, state-restore

Examples:
  python sherpa/nextcloud/main.py --op health
  python sherpa/nextcloud/main.py --op state-save --domain finance --key budget --data '{"monthly":50000}'
  python sherpa/nextcloud/main.py --op state-load --domain finance --key budget
  python sherpa/nextcloud/main.py --op list --path /raj-sadan/state
  python sherpa/nextcloud/main.py --op upload --path /raj-sadan/docs/note.txt --file note.txt
  python sherpa/nextcloud/main.py --prompt "save health goals to health domain"
  python sherpa/nextcloud/main.py --dry-run --op state-save --domain finance --key budget --data '{}'
        """,
    )

    parser.add_argument('--op', choices=ALL_OPS, help='Operation to perform')
    parser.add_argument('--prompt', help='Natural language prompt (uses Ollama for parsing)')
    parser.add_argument('--domain', choices=DOMAINS + [''], help='Domain (finance, health, house-ops, infrastructure, career)')
    parser.add_argument('--key', help='State key (e.g. budget, goals)')
    parser.add_argument('--data', help='JSON data for state-save (string or JSON)')
    parser.add_argument('--path', help='Remote file/directory path on NextCloud')
    parser.add_argument('--file', help='Local file path for upload')
    parser.add_argument('--out', help='Local output file path for download')
    parser.add_argument('--query', help='Search query')
    parser.add_argument('--from', dest='path_from', help='Source path for move/copy')
    parser.add_argument('--to', dest='path_to', help='Destination path for move/copy')
    parser.add_argument('--timestamp', help='Version timestamp for state-restore')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be executed without doing it')
    parser.add_argument('--json', action='store_true', help='Force JSON output only')

    args = parser.parse_args()

    if not args.op and not args.prompt:
        parser.print_help()
        sys.exit(1)

    result = None

    # Natural language path
    if args.prompt:
        print(f"[nextcloud-sherpa] Parsing: '{args.prompt}'", file=sys.stderr)
        nl_result = parse_natural_language(args.prompt)

        if not nl_result['success']:
            error = {'success': False, 'error': nl_result['error']}
            print(json.dumps(error, indent=2))
            sys.exit(1)

        parsed = nl_result['data']
        print(f"[nextcloud-sherpa] Parsed intent: {json.dumps(parsed)}", file=sys.stderr)

        # Merge parsed fields into args namespace (only if not already set by CLI)
        op = parsed.get('op')
        if not op:
            print(json.dumps({'success': False, 'error': 'Could not determine operation from prompt'}), indent=2)
            sys.exit(1)

        # Build a synthetic namespace from parsed + any explicit CLI args
        if not args.domain and parsed.get('domain'):
            args.domain = parsed['domain']
        if not args.key and parsed.get('key'):
            args.key = parsed['key']
        if args.data is None and parsed.get('data') is not None:
            args.data = json.dumps(parsed['data'])
        if not args.path and parsed.get('path'):
            args.path = parsed['path']
        if not args.query and parsed.get('query'):
            args.query = parsed['query']
        if not args.path_from and parsed.get('path_from'):
            args.path_from = parsed['path_from']
        if not args.path_to and parsed.get('path_to'):
            args.path_to = parsed['path_to']

        result = route_operation(op, args, dry_run=args.dry_run)

    # Structured path
    else:
        result = route_operation(args.op, args, dry_run=args.dry_run)

    # Output
    if result is None:
        result = {'success': False, 'error': 'No result returned'}

    if args.json or not sys.stdout.isatty():
        # Machine-readable: JSON only
        print(json.dumps(result, indent=2))
    else:
        # Human-readable summary to stderr, JSON to stdout
        if result.get('success'):
            data = result.get('data')
            if isinstance(data, dict) and 'status' in data:
                print(f"[nextcloud-sherpa] Status: {data.get('status')} | Connected: {data.get('connected')}", file=sys.stderr)
            elif isinstance(data, list):
                print(f"[nextcloud-sherpa] {len(data)} item(s) returned", file=sys.stderr)
            else:
                print(f"[nextcloud-sherpa] Success", file=sys.stderr)
        else:
            print(f"[nextcloud-sherpa] FAILED: {result.get('error')}", file=sys.stderr)

        print(json.dumps(result, indent=2))

    sys.exit(0 if result.get('success') else 1)


if __name__ == '__main__':
    main()
