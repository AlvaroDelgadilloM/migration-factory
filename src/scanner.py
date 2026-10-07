"""CLI for the static inventory (stdlib only). Implementation lives in backend/mf/analysis."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from mf.analysis.report import render  # noqa: E402
from mf.analysis.scanner import scan  # noqa: E402,F401


def main():
    parser = argparse.ArgumentParser(description='Inventario estático Camel/JBoss; no ejecuta Maven ni modifica el origen.')
    parser.add_argument('source')
    parser.add_argument('--target', choices=['spring', 'quarkus'], default='spring')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root, out = Path(args.source).resolve(), Path(args.output).resolve()
    if out == root or root in out.parents:
        parser.error('Output must be outside source repository')
    result = scan(root, args.target)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'report.html').write_text(render(result), encoding='utf-8')
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
