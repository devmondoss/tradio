import json, urllib.request, urllib.parse
from pathlib import Path
ROOT = Path(__file__).parent.parent.parent.parent
env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
URL = env['SUPABASE_URL']; KEY = env['SUPABASE_KEY']
qs = urllib.parse.urlencode({
    'select': 'ts_ms,open,high,low,close,volume,bar_delta',
    'ts_ms': 'gte.1781000000000',
    'order': 'ts_ms.asc',
    'limit': '3'
})
req = urllib.request.Request(
    f'{URL}/rest/v1/btc_bars?{qs}',
    headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
)
rows = json.loads(urllib.request.urlopen(req, timeout=10).read())
for r in rows:
    print(r)
