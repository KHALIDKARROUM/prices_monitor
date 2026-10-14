"""Check documented source paths and assets shipped in an installable wheel."""
from pathlib import Path
import argparse
import hashlib
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def check_source():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    paths = set(re.findall(r'`((?:notebooks|reports)/[^`]+\.(?:ipynb|md|json))`', readme))
    paths.update(re.findall(r'\]\(((?:notebooks|reports)/[^)#]+)\)', readme))
    assert {'notebooks/data_workflow.ipynb', 'reports/data_workflow/report.md', 'reports/data_workflow/bitumen_report.md'} <= paths
    for path in paths:
        assert (ROOT / path).is_file(), f'Broken README source path: {path}'
    static = ROOT / 'price_monitor/static'
    bundle = static / 'chart.umd-4.4.3.js'
    recorded = re.search(r'Bundle SHA-256: `([0-9a-f]{64})`', (static / 'THIRD_PARTY.md').read_text())
    assert recorded and hashlib.sha256(bundle.read_bytes()).hexdigest() == recorded[1], 'Chart bundle differs from its provenance record'
    assert 'MIT' in (static / 'chartjs-LICENSE.md').read_text()
    template = (ROOT / 'price_monitor/templates/index.html').read_text(encoding='utf-8')
    assert 'src="/static/chart.umd-4.4.3.js"' in template
    assert not re.search(r'<script[^>]+src="https?://', template), 'Runtime scripts must be bundled locally'

def check_wheel(path):
    with zipfile.ZipFile(path) as wheel:
        files = set(wheel.namelist())
        for name in ('chart.umd-4.4.3.js', 'chartjs-LICENSE.md', 'THIRD_PARTY.md', 'attention.js', 'auth.js', 'team.js'):
            assert 'price_monitor/static/' + name in files, f'Missing packaged asset: {name}'
        for backend in ('migrations', 'mysql_migrations'):
            assert f'price_monitor/{backend}/015_provider_vintages.sql' in files
        assert not any('/storage/' in name or name.endswith('/.env') for name in files), 'Private runtime data found in package'

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args()
    check_source()
    if args.wheel:
        check_wheel(args.wheel)
    print('Documentation paths, bundled charts and delivery assets verified')
