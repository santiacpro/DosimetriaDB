import React, { useState, useEffect, useMemo, useRef } from 'react';
import { AgGridReact } from 'ag-grid-react';
import axios from 'axios';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

const API_URL = 'https://dosimetriadb.onrender.com/api';

export default function App() {
  // =====================================================================
  // 1. ZONA DE HOOKS
  // =====================================================================
  const gridRef = useRef();
  const [autenticado, setAutenticado] = useState(false);
  const [clave, setClave] = useState('');
  
  const [rowData, setRowData] = useState([]);
  const [avisos, setAvisos] = useState([]);
  const [mesesNombres, setMesesNombres] = useState({});
  const [modalUser, setModalUser] = useState(null);
  const [cargando, setCargando] = useState(true);

  const [filtroCentro, setFiltroCentro] = useState('Todos los centros');
  const [filtroBusqueda, setFiltroBusqueda] = useState('');
  const [soloAvisos, setSoloAvisos] = useState(false);
  const [centroExportar, setCentroExportar] = useState('');
  const [mesBorrar, setMesBorrar] = useState('');
  const [centroBorrar, setCentroBorrar] = useState('Todos los centros');

  const cargarDatos = async () => {
    try {
      setCargando(true);
      const res = await axios.get(`${API_URL}/dosimetria`);
      setRowData(res.data.data || []);
      setAvisos(res.data.avisos || []);
      setMesesNombres(res.data.meses_nombres || {});
    } catch (err) {
      alert("❌ Error de conexión con el servidor. ¿Está Uvicorn encendido?");
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => { 
    if (autenticado) cargarDatos(); 
  }, [autenticado]);

  const centrosDisponibles = useMemo(() => Array.from(new Set(rowData.map(r => r.CENTRO).filter(Boolean))).sort(), [rowData]);
  const mesesDisponibles = useMemo(() => ["Todos los meses", ...Array.from(new Set(avisos.map(a => a.periodo))).sort().reverse()], [avisos]);

  const dataFiltrada = useMemo(() => {
    return rowData.filter(row => {
      if (filtroCentro !== 'Todos los centros' && row.CENTRO !== filtroCentro) return false;
      if (soloAvisos && row.ESTADO_AVISO !== '⚠️') return false;
      if (filtroBusqueda) {
        const q = filtroBusqueda.toLowerCase();
        const str = `${row.NOMBRE || ''} ${row.DNI || ''} ${row.CODIGO || ''} ${row.CENTRO || ''}`.toLowerCase();
        if (!str.includes(q)) return false;
      }
      return true;
    });
  }, [rowData, filtroCentro, soloAvisos, filtroBusqueda]);

  const maxMonthReading = useMemo(() => {
    if (!modalUser) return 0;
    const meses = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'];
    let max = 0;
    meses.forEach((m, idx) => {
      const hsm = modalUser[`${m}_HSM`], hpm = modalUser[`${m}_HPM`], obs = modalUser[`${m}_OBS`];
      if (hsm || hpm || (obs && !['No entregado', 'Baja'].includes(obs))) max = idx + 1;
    });
    return max;
  }, [modalUser]);

  const columnDefs = useMemo(() => {
    const BotonResolverRenderer = (params) => {
      if (params.data && params.value === '⚠️') {
        return (
          <button 
            onClick={() => setModalUser(params.data)} 
            style={{ background:'#fef08a', border:'1px solid #eab308', borderRadius:'4px', cursor:'pointer', fontWeight: 'bold', padding: '2px 6px' }}
          >
            Resolver
          </button>
        );
      }
      return null;
    };

    const cellStyleDosis = params => {
      if (!params.data || !params.colDef?.field) return null;
      let style = {};
      const mesesFaltantes = params.data.MESES_FALTANTES?.split(',') || [];
      const mesCol = params.colDef.field.split('_')[0];
      
      if (mesesFaltantes.includes(mesCol)) { style.background = '#fca5a5'; style.border = '2px solid #dc2626'; }
      if (params.value) {
        const val = parseFloat(String(params.value).replace(',', '.'));
        if (!isNaN(val) && val > 1.0) { style.background = '#f87171'; style.color = '#7f1d1d'; style.fontWeight = 'bold'; }
      }
      return Object.keys(style).length > 0 ? style : null;
    };

    const valueFormatterDosis = params => {
      if (!params.data || !params.colDef?.field) return '';
      const mes = params.colDef.field.split('_')[0];
      const obs = String(params.data[`${mes}_OBS`] || '').toLowerCase();
      
      if (obs.includes('no entregado') || obs.includes('baja')) return '';
      if (params.value === null || params.value === undefined || params.value === '') return '';
      
      const num = parseFloat(String(params.value).replace(',', '.'));
      return !isNaN(num) ? num.toFixed(2) : '';
    };

    const cols = [
      { field: 'ESTADO_AVISO', headerName: '⚠️', width: 90, pinned: 'left', cellRenderer: BotonResolverRenderer },
      { field: 'NOMBRE', width: 250, pinned: 'left', filter: true, editable: true },
      { field: 'DNI', width: 110, filter: true, editable: true },
      { field: 'CENTRO', width: 180, filter: true, editable: true },
      { field: 'DOSIMETRO', width: 110, editable: true },
      { field: 'CODIGO', width: 110, editable: false },
      { field: 'FECHA ALTA', width: 110, editable: true },
      { field: 'FECHA BAJA', width: 110, editable: true },
    ];

    const meses = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'];
    meses.forEach(mes => {
      cols.push({
        headerName: mes,
        children: [
          { field: `${mes}_HSM`, headerName: 'HSM', width: 80, editable: true, valueFormatter: valueFormatterDosis, cellStyle: cellStyleDosis },
          { field: `${mes}_HPM`, headerName: 'HPM', width: 80, editable: true, valueFormatter: valueFormatterDosis, cellStyle: cellStyleDosis },
          { field: `${mes}_OBS`, headerName: 'Obs', width: 130, editable: true, cellEditor: 'agSelectCellEditor', cellEditorParams: { values: ['', 'No entregado', 'Baja', 'Alta'] }, cellStyle: p => p.value?.toLowerCase().includes('no entregado') ? { background: '#fee2e2', color: '#991b1b', fontWeight: 'bold'} : null }
        ]
      });
    });

    cols.push({
      headerName: 'ACUMULADO',
      children: [
        { 
          field: 'ACUMULADO_HSM', headerName: 'HSM', width: 90, cellStyle:{background:'#f1f5f9', fontWeight:'bold'}, 
          valueFormatter: p => (p.value === null || p.value === undefined || p.value === '') ? '0.00' : Number(p.value).toFixed(2) 
        },
        { 
          field: 'ACUMULADO_HPM', headerName: 'HPM', width: 90, cellStyle:{background:'#f1f5f9', fontWeight:'bold'}, 
          valueFormatter: p => (p.value === null || p.value === undefined || p.value === '') ? '0.00' : Number(p.value).toFixed(2) 
        }
      ]
    });
    return cols;
  }, []);

  // =====================================================================
  // 2. RENDERIZADOS CONDICIONALES Y LOGICA DE NEGOCIO
  // =====================================================================
  
  const intentarLogin = async () => {
    try {
      await axios.post(`${API_URL}/login`, { password: clave });
      setAutenticado(true);
    } catch (err) {
      alert("❌ Contraseña incorrecta");
    }
  };

  if (!autenticado) {
    return (
      <div style={{ height: '100vh', width: '100vw', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f1f5f9' }}>
        <div style={{ background: '#fff', padding: '40px', borderRadius: '8px', boxShadow: '0 4px 6px rgba(0,0,0,0.1)', textAlign: 'center' }}>
          <h2 style={{ color: '#1e293b', marginTop: 0 }}>🔒 Acceso Restringido</h2>
          <input 
            type="password" 
            value={clave} 
            onChange={e => setClave(e.target.value)} 
            onKeyDown={e => { if (e.key === 'Enter') intentarLogin(); }}
            placeholder="Contraseña" 
            style={{ padding: '10px', width: '220px', marginBottom: '15px', border: '1px solid #cbd5e1', borderRadius: '4px', outline: 'none' }} 
          />
          <br/>
          <button onClick={intentarLogin} style={{ background: '#2563eb', color: '#fff', border: 'none', padding: '10px 20px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold', width: '100%' }}>
            Iniciar Sesión
          </button>
        </div>
      </div>
    );
  }

  const getRowStyle = params => {
    if (!params.data) return null;
    if (params.data['FECHA BAJA']) return { background: '#e2e8f0', color: '#64748b' };
    if (params.data['ESTADO_AVISO'] === '⚠️') return { background: '#fef9c3' };
    return null;
  };

  const guardarEnBD = async () => {
    try {
      const updatedData = [];
      gridRef.current.api.forEachNode(node => updatedData.push(node.data));
      await axios.post(`${API_URL}/dosimetria/guardar`, { datos_antiguos: rowData, datos_nuevos: updatedData });
      alert("✅ Cambios guardados");
      cargarDatos();
    } catch (err) { alert("❌ Error al guardar."); }
  };

  const accionAdminArchivo = async (endpoint, e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData(); formData.append("file", file);
    try {
      await axios.post(`${API_URL}/${endpoint}`, formData);
      alert("✅ Operación completada");
      cargarDatos();
    } catch (err) { alert("❌ Error en la operación."); }
  };

  const borrarDatos = async () => {
    if(!window.confirm("⚠️ ¿Estás seguro de borrar estos datos?")) return;
    try {
      await axios.post(`${API_URL}/admin/borrar`, { mes: mesBorrar, centro: centroBorrar });
      alert("✅ Datos borrados");
      cargarDatos();
    } catch (err) { alert("❌ Error al borrar."); }
  };

  const descargarCSV = () => gridRef.current.api.exportDataAsCsv({ fileName: 'dosimetria.csv' });

  const resolverModal = (accion, payload) => {
    const rowNode = gridRef.current.api.getRowNode(String(modalUser.CODIGO));
    let currentMeses = rowNode.data.MESES_FALTANTES ? rowNode.data.MESES_FALTANTES.split(',') : [];

    if (accion === 'guardar_dni') {
      rowNode.setDataValue('DNI', payload);
    } else if (accion === 'ignorar_alta') {
      rowNode.setDataValue('ESTADO_AVISO', 'IGNORADO');
    } else if (accion === 'no_entregado') {
      rowNode.setDataValue(`${payload}_OBS`, 'No entregado');
      rowNode.setDataValue(`${payload}_HSM`, null);
      rowNode.setDataValue(`${payload}_HPM`, null);
      currentMeses = currentMeses.filter(m => m !== payload);
      rowNode.setDataValue('MESES_FALTANTES', currentMeses.join(','));
    } else if (accion === 'baja') {
      rowNode.setDataValue('FECHA BAJA', payload.fecha);
      rowNode.setDataValue(`${payload.mes}_OBS`, 'Baja');
      rowNode.setDataValue(`${payload.mes}_HSM`, null);
      rowNode.setDataValue(`${payload.mes}_HPM`, null);
      rowNode.setDataValue('MESES_FALTANTES', '');
      currentMeses = [];
    }

    const dniOk = rowNode.data.DNI || rowNode.data.ESTADO_AVISO === 'IGNORADO';
    if (currentMeses.length === 0 && dniOk) rowNode.setDataValue('ESTADO_AVISO', '');
    
    gridRef.current.api.redrawRows({ rowNodes: [rowNode] });
    if(currentMeses.length === 0 && dniOk) setModalUser(null);
  };

  // =====================================================================
  // 3. RENDERIZADO PRINCIPAL DE LA APP
  // =====================================================================
  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden', fontFamily: 'sans-serif', background: '#f8fafc' }}>
      
      {/* SIDEBAR */}
      <aside style={{ width: '300px', background: '#1e293b', color: '#fff', padding: '20px', display: 'flex', flexDirection: 'column', gap: '15px', overflowY: 'auto' }}>
        <h2 style={{ margin: 0, color: '#f8fafc' }}>☢️ DosimetriaDB</h2>

        <div>
          <h4 style={{ margin: '5px 0' }}>📄 Procesar PDFs</h4>
          <input type="file" accept=".pdf" multiple onChange={e => accionAdminArchivo('pdf/procesar', e)} style={{ width: '100%', fontSize: '11px' }} />
        </div>

        <div>
          <h4 style={{ margin: '5px 0' }}>🏢 Filtros</h4>
          <select value={filtroCentro} onChange={e => setFiltroCentro(e.target.value)} style={{ width: '100%', padding: '6px', borderRadius: '4px', marginBottom: '8px' }}>
            <option value="Todos los centros">Todos los centros</option>
            {centrosDisponibles.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <input type="text" placeholder="Buscador..." value={filtroBusqueda} onChange={e => setFiltroBusqueda(e.target.value)} style={{ width: '100%', padding: '6px', borderRadius: '4px', boxSizing: 'border-box' }} />
          <label style={{ fontSize: '12px', display: 'block', marginTop: '8px' }}>
            <input type="checkbox" checked={soloAvisos} onChange={e => setSoloAvisos(e.target.checked)} /> ⚠️ Solo avisos
          </label>
        </div>

        <hr style={{ borderColor: '#334155' }} />

        <div>
          <h4 style={{ margin: '5px 0' }}>🖨️ Exportación</h4>
          <select value={centroExportar} onChange={e => setCentroExportar(e.target.value)} style={{ width: '100%', padding: '6px', borderRadius: '4px', marginBottom: '8px' }}>
            <option value="">Fichas PDF de Centro...</option>
            {centrosDisponibles.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          {centroExportar && <a href={`${API_URL}/fichas/exportar/${encodeURIComponent(centroExportar)}`} target="_blank" rel="noreferrer" style={{ display: 'block', textAlign: 'center', background: '#2563eb', color: '#fff', padding: '6px', borderRadius: '4px', textDecoration: 'none', fontSize: '12px' }}>📄 Descargar PDF</a>}
          <button onClick={descargarCSV} style={{ width: '100%', marginTop: '8px', padding: '6px', background: '#475569', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>📥 Descargar CSV Actual</button>
        </div>

        <hr style={{ borderColor: '#334155' }} />
        
        {/* ADMINISTRACIÓN */}
        <div>
          <h4 style={{ margin: '5px 0' }}>⚙️ Administración</h4>
          
          <label style={{ fontSize: '11px' }}>Subir Maestro (.xlsx)</label>
          <input type="file" accept=".xlsx" onChange={e => accionAdminArchivo('admin/maestro', e)} style={{ width: '100%', fontSize: '10px', marginBottom: '8px' }} />
          
          <label style={{ fontSize: '11px' }}>Subir Centros (.xlsx)</label>
          <input type="file" accept=".xlsx" onChange={e => accionAdminArchivo('admin/centros', e)} style={{ width: '100%', fontSize: '10px', marginBottom: '8px' }} />
          
          <label style={{ fontSize: '11px' }}>Restaurar Backup (.csv)</label>
          <input type="file" accept=".csv" onChange={e => accionAdminArchivo('admin/restore', e)} style={{ width: '100%', fontSize: '10px', marginBottom: '15px' }} />

          <div style={{ background: '#334155', padding: '10px', borderRadius: '4px' }}>
            <h5 style={{ margin: '0 0 5px 0', color: '#fca5a5' }}>🗑️ Zona de Borrado</h5>
            <select value={mesBorrar} onChange={e => setMesBorrar(e.target.value)} style={{ width: '100%', fontSize: '11px', marginBottom: '4px' }}>
              <option value="">Seleccionar mes...</option>
              {mesesDisponibles.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <select value={centroBorrar} onChange={e => setCentroBorrar(e.target.value)} style={{ width: '100%', fontSize: '11px', marginBottom: '4px' }}>
              <option value="Todos los centros">Todos los centros</option>
              {centrosDisponibles.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <button onClick={borrarDatos} style={{ width: '100%', background: '#dc2626', color: 'white', border: 'none', padding: '4px', borderRadius: '4px', cursor: 'pointer', fontSize: '11px' }}>Eliminar Datos</button>
          </div>
        </div>
      </aside>

      {/* MAIN CONTAINER */}
      <main style={{ flex: 1, padding: '15px', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
          <h3 style={{ margin: 0 }}>Registros ({dataFiltrada.length})</h3>
          <button onClick={guardarEnBD} style={{ background: '#16a34a', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer' }}>💾 Guardar BD</button>
        </header>

        {cargando ? (
          <div>⏳ Sincronizando datos...</div>
        ) : (
          <div className="ag-theme-alpine" style={{ flex: 1, width: '100%' }}>
            <AgGridReact
              ref={gridRef}
              rowData={dataFiltrada}
              columnDefs={columnDefs}
              getRowId={p => p.data && p.data.CODIGO ? String(p.data.CODIGO) : String(Math.random())}
              getRowStyle={getRowStyle}
              animateRows={true}
              suppressRowClickSelection={true}
            />
          </div>
        )}
      </main>

      {/* MODAL RESOLUCIÓN COMPLETO */}
      {modalUser && (
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }}>
          <div style={{ background: '#fff', padding: '20px', borderRadius: '8px', width: '450px', maxHeight: '90vh', overflowY: 'auto', boxShadow: '0 20px 25px rgba(0,0,0,0.3)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '15px' }}>
              <h3 style={{ margin: 0 }}>⚙️ {modalUser.NOMBRE}</h3>
              <button onClick={() => setModalUser(null)} style={{ border: 'none', background: 'none', fontSize: '16px', cursor: 'pointer' }}>✖</button>
            </div>
            
            {(!modalUser.DNI || modalUser.ESTADO_AVISO === '⚠️') && (
              <div style={{ background: '#fefce8', padding: '12px', border: '1px solid #fef08a', borderRadius: '6px', marginBottom: '15px' }}>
                <strong>🟡 Alta / Falta DNI</strong>
                <input type="text" id="modal-dni-field" defaultValue={modalUser.DNI || ''} style={{ width: '100%', padding: '6px', margin: '8px 0', border: '1px solid #ccc', borderRadius: '4px' }} />
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button onClick={() => resolverModal('guardar_dni', document.getElementById('modal-dni-field').value)} style={{ flex: 1, background: '#16a34a', color: 'white', border: 'none', padding: '6px', borderRadius: '4px', cursor: 'pointer' }}>✅ Guardar</button>
                  <button onClick={() => resolverModal('ignorar_alta')} style={{ flex: 1, background: '#64748b', color: 'white', border: 'none', padding: '6px', borderRadius: '4px', cursor: 'pointer' }}>🙈 Ignorar</button>
                </div>
              </div>
            )}

            {(modalUser.MESES_FALTANTES?.split(',').filter(Boolean) || []).map(mes => {
              const mesIdx = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'].indexOf(mes) + 1;
              const canBaja = mesIdx >= maxMonthReading;
              
              return (
                <div key={mes} style={{ background: '#fef2f2', border: '1px solid #fca5a5', padding: '12px', borderRadius: '6px', marginBottom: '10px' }}>
                  <strong style={{ color: '#991b1b' }}>🔴 Falta lectura de {mes}</strong>
                  <button onClick={() => resolverModal('no_entregado', mes)} style={{ width: '100%', background: '#dc2626', color: 'white', border: 'none', padding: '6px', borderRadius: '4px', margin: '8px 0', cursor: 'pointer' }}>❌ Marcar No Entregado</button>
                  
                  {canBaja ? (
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <input type="date" id={`date-${mes}`} defaultValue={`2026-${String(mesIdx).padStart(2, '0')}-01`} style={{ flex: 1, padding: '4px', border: '1px solid #ccc', borderRadius: '4px' }} />
                      <button onClick={() => resolverModal('baja', { mes, fecha: document.getElementById(`date-${mes}`).value })} style={{ background: '#475569', color: 'white', border: 'none', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer' }}>🛑 Tramitar Baja</button>
                    </div>
                  ) : (
                    <span style={{ fontSize: '11px', color: '#64748b' }}>*(Baja bloqueada: Hay datos futuros)*</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
