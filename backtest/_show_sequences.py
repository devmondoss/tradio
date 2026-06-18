"""Muestra los patrones multi-barra accionables, filtrando el ruido FVG."""
import pandas as pd

df = pd.read_csv('exports/spot_sequences_mining.csv')

# FVG conditions cubren 80-94% de candidatos -> no filtran, excluir patrones 100% FVG
FVG_KEYS = ['fvg', 'rec_fvg', 'rec_near_fvg', 'seq_fvg', 'near_fvg', 'fvg_act']

def is_fvg_dominated(p):
    parts = p.split(' + ')
    fvg_count = sum(1 for x in parts if any(f in x for f in FVG_KEYS))
    return fvg_count == len(parts)

df['fvg_dom'] = df['pattern'].apply(is_fvg_dominated)
DAYS = 351
df['tpd'] = df['n'] / DAYS

# Filtro: AvgR >= 0.20 (neto aprox 0.05-0.10R despues de ~0.15R fees), n >= 300
clean = df[~df['fvg_dom'] & (df['avg_r'] >= 0.20) & (df['n'] >= 300)].copy()
clean = clean.sort_values('total_r', ascending=False)

# Dedup por combinacion
seen = set()
unique = []
for _, row in clean.iterrows():
    key = frozenset(row['pattern'].split(' + '))
    if key not in seen:
        seen.add(key)
        unique.append(row.to_dict())

print(f"Patrones accionables (AvgR>=0.20, n>=300, sin FVG puro): {len(unique)} unicos\n")
print(f"{'#':>3}  {'n':>6}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}  patron")
print('-' * 85)
for i, r in enumerate(unique[:50]):
    print(f"{i+1:>3}  {r['n']:>6}  {r['tpd']:>5.1f}  {r['wr']:>6.1f}%  "
          f"{r['avg_r']:>7.3f}  {r['total_r']:>8.0f}  {r['pattern']}")
