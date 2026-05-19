from backend.app import _read_sheets
dfs = _read_sheets()
post = dfs['postulaciones']
print('Cols:', post.columns.tolist() if not post.empty else 'EMPTY')
if not post.empty:
    print('Estados únicos:', post.get('Estado', post.get('ESTADO', post.get('estado', []))).unique().tolist()[:10])
    print('Periodos únicos:', post.get('Periodo', post.get('PERIODO', [])).unique().tolist()[:10])
    print('Sample RUTs:', post.get('RUT', post.get('Rut', [])).dropna().head().tolist())
    
prom = dfs['promedios']
if not prom.empty:
    print('Promedios RUT sample:', prom['RUT'].dropna().head().tolist())
