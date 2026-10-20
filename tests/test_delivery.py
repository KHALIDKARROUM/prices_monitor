from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def test_readme_paths_and_vendored_chart_integrity():
    spec = spec_from_file_location('delivery', ROOT / 'scripts/check_delivery.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_source()

def test_every_chart_has_a_named_accessible_text_summary():
    template = (ROOT / 'src/price_monitor/templates/index.html').read_text(encoding='utf-8')
    canvases = re.findall(r'<canvas\b([^>]+)>', template)
    assert len(canvases) == 4
    for attributes in canvases:
        assert 'role="img"' in attributes and 'aria-label=' in attributes
        description = re.search(r'aria-describedby="([^"]+)"', attributes)
        assert description
        for identifier in description[1].split():
            assert f'id="{identifier}"' in template
