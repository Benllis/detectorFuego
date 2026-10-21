// ============================================================
// FireWatch · Mapa 3D — cuarteles y grifos desde la copia local del SIG de Bomberos
//
// El navegador NO consulta sig.bomberos.cl: el servidor del mapa sirve una copia
// descargada una sola vez con `python tools/ingest_bomberos.py` (data/bomberos/,
// fuera de Git). Si la copia no existe, el mapa sigue usando OpenStreetMap como antes.
//
// Fuente: SIG Bomberos de Chile (sig.bomberos.cl) · grifos: catastro 2019.
// Los datos no son oficiales "en vivo": pueden estar desactualizados; se contrastan con OSM.
// ============================================================

const bomberosLocal = { available: false, label: '', meta: null };

/** Pide la copia local. Devuelve true si cuarteles y grifos quedaron cargados en el mapa. */
async function bomberosLoadLocal() {
  if (typeof FW === 'undefined' || !FW.proxy) return false;
  try {
    const [rs, rh, rm] = await Promise.all(['cuarteles', 'grifos', 'meta'].map((k) => fetch(`/api/bomberos/${k}`)));
    if (rs.status === 404 || rh.status === 404) return false; // todavía no se ha descargado
    if (!rs.ok || !rh.ok) throw new Error(`HTTP ${rs.status}/${rh.status}`);
    const st = await rs.json(), hy = await rh.json();
    const meta = rm.ok ? await rm.json() : null;
    const cols = hy.campos || [];
    const ix = (n) => cols.indexOf(n);
    const hydrantsList = (hy.items || []).map((a) => ({
      id: a[ix('id')], lat: a[ix('lat')], lon: a[ix('lon')], tipo: a[ix('tipo')], diametro: a[ix('diametro')], anio: a[ix('anio')], comuna: a[ix('comuna')],
    }));
    const year = hy.anio || '';
    const label = `SIG Bomberos de Chile${year ? ', grifos ' + year : ''}`;
    zonesApplyBomberos({ stations: st.items || [], hydrants: hydrantsList, label });
    bomberosLocal.available = true;
    bomberosLocal.label = label;
    bomberosLocal.meta = meta;
    const fecha = (st.descargado || '').slice(0, 10);
    const nC = (st.items || []).length;
    reportSource('firestations', 'ok', `${nC} cuarteles (cuerpos y compañías) · ${label}` + (fecha ? ` · descargado ${fecha}` : ''));
    reportSource('hydrants', 'ok', `${hydrantsList.length} grifos · ${label} · pueden faltar o estar desactualizados`);
    return true;
  } catch (err) {
    console.warn('[Bomberos] no se pudo usar la copia local; se usa OpenStreetMap:', err);
    if (typeof fwLog === 'function') fwLog('bomberos', 'warn', err.message, { phase: 'copia local', variant: 'sig' });
    return false;
  }
}
