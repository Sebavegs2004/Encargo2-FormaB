/* Cliente web de VetClínica: usa únicamente las rutas públicas del api-gateway. */
'use strict';

const CFG = window.CLINICA_CONFIG;
const $ = (selector) => document.querySelector(selector);
const esc = (valor) => String(valor ?? '').replace(/[&<>"']/g, (caracter) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[caracter]));

const PASOS = {
  reservar_hora: 'Reserva de hora',
  cobrar_abono: 'Registro del abono',
  confirmar_cita: 'Confirmación de la cita',
};
const COMPENSACIONES = {
  reservar_hora: 'Liberar la hora reservada',
  cobrar_abono: 'Reembolsar el abono',
  confirmar_cita: 'Anular la cita',
};
const ESTADOS_SAGA = {
  COMPLETADA: { clase: 'completada', titulo: 'Consulta agendada', texto: 'La hora quedó reservada, el abono registrado y la cita confirmada en la ficha.' },
  COMPENSADA: { clase: 'compensada', titulo: 'Reserva revertida', texto: 'No se pudo completar la consulta. Los pasos ya realizados se deshicieron automáticamente y no quedó estado pendiente.' },
  FALLIDA: { clase: 'fallida', titulo: 'No se pudo completar la solicitud', texto: 'La solicitud terminó con una inconsistencia; contacta con recepción para revisarla.' },
  EN_CURSO: { clase: 'encurso', titulo: 'Solicitud en curso', texto: 'La operación todavía se está procesando.' },
};

function nuevaClave() {
  if (window.crypto && crypto.randomUUID) return `web-${crypto.randomUUID()}`;
  return `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const estado = {
  resumenes: new Map(),
  mascotaId: null,
  slotId: null,
  clave: nuevaClave(),
  saga: null,
  enviando: false,
};

function formatearFecha(valor) {
  if (!valor) return '—';
  const fecha = new Date(valor);
  if (Number.isNaN(fecha.getTime())) return String(valor);
  return new Intl.DateTimeFormat('es-CL', {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  }).format(fecha);
}

function formatearHora(valor) {
  const fecha = new Date(valor);
  return Number.isNaN(fecha.getTime())
    ? '—'
    : new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(fecha);
}

function dinero(monto) {
  return new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(monto);
}

function corto(id) {
  if (!id) return '—';
  return id.length > 10 ? `${id.slice(0, 8)}…` : id;
}

function resumenActual() {
  return estado.resumenes.get(estado.mascotaId) || null;
}

async function api(ruta, { metodo = 'GET', cuerpo, clave } = {}) {
  const cabeceras = { Authorization: `Bearer ${CFG.TOKEN}` };
  if (cuerpo !== undefined) cabeceras['Content-Type'] = 'application/json';
  if (clave) cabeceras['Idempotency-Key'] = clave;

  const inicio = performance.now();
  let respuesta;
  try {
    respuesta = await fetch(`${CFG.API_BASE}${ruta}`, {
      method: metodo,
      headers: cabeceras,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
    });
  } catch (error) {
    throw Object.assign(new Error('No se pudo conectar con el servidor'), { tipo: 'red', ms: Math.round(performance.now() - inicio) });
  }
  const ms = Math.round(performance.now() - inicio);

  let datos = null;
  try { datos = await respuesta.json(); } catch (error) { datos = null; }
  if (!respuesta.ok) {
    const mensaje = datos && datos.detail ? datos.detail : `El servidor respondió ${respuesta.status}`;
    throw Object.assign(new Error(mensaje), {
      status: respuesta.status,
      ms,
      reintentarDespues: respuesta.headers.get('Retry-After'),
    });
  }
  return { datos, ms };
}

function avisar(mensaje) {
  const toast = $('#toast');
  toast.textContent = mensaje;
  toast.hidden = false;
  clearTimeout(avisar.temporizador);
  avisar.temporizador = setTimeout(() => { toast.hidden = true; }, 4000);
}

function estadoServicio(texto, clase) {
  const pill = $('#estado-servicio');
  pill.textContent = texto;
  pill.className = `status-pill status-pill--${clase}`;
}

async function verificarSalud() {
  try {
    const respuesta = await fetch(`${CFG.API_BASE}/health`);
    if (!respuesta.ok) throw new Error('health');
    estadoServicio('Servicios disponibles', 'ok');
  } catch (error) {
    estadoServicio('Sin conexión', 'error');
  }
}

/* ------------------------------ datos (BFF) -------------------------------- */

async function cargarResumenes() {
  const ids = CFG.MASCOTA_IDS;
  const respuestas = await Promise.allSettled(ids.map((id) => api(`/api/bff/resumen-mascota/${id}`)));

  estado.resumenes.clear();
  respuestas.forEach((resultado, indice) => {
    if (resultado.status === 'fulfilled' && resultado.value.datos && resultado.value.datos.mascota) {
      estado.resumenes.set(ids[indice], resultado.value.datos);
    }
  });

  if (estado.resumenes.size === 0) {
    $('#mascota-select').innerHTML = '<option value="">Sin mascotas disponibles</option>';
    $('#mascota-info').textContent = '';
    $('#slots-lista').innerHTML = '<p class="vacio">No se pudo cargar la disponibilidad. Inténtalo de nuevo más tarde.</p>';
    $('#mascotas-grid').innerHTML = '<p class="vacio">No se pudieron cargar las mascotas.</p>';
    $('#citas-lista').innerHTML = '<p class="vacio">Sin datos.</p>';
    $('#slots-estado').innerHTML = '<p class="vacio">Sin datos.</p>';
    estadoServicio('Sin conexión', 'error');
    return false;
  }

  if (estado.mascotaId === null || !estado.resumenes.has(estado.mascotaId)) {
    estado.mascotaId = estado.resumenes.keys().next().value;
  }
  renderMascotaSelect();
  renderFormulario();
  renderMascotas();
  return true;
}

async function actualizarResumen(mascotaId) {
  const { datos } = await api(`/api/bff/resumen-mascota/${mascotaId}`);
  estado.resumenes.set(mascotaId, datos);
  return datos;
}

/* ------------------------------- render ------------------------------------ */

function renderMascotaSelect() {
  const selector = $('#mascota-select');
  selector.innerHTML = '<option value="">Selecciona una mascota</option>' +
    [...estado.resumenes.entries()].map(([id, resumen]) => `
      <option value="${id}">${esc(resumen.mascota.nombre)} · ${esc(resumen.mascota.especie)}</option>
    `).join('');
  selector.value = String(estado.mascotaId);

  const mascota = resumenActual()?.mascota;
  const confirmadas = (resumenActual()?.citas || []).filter((cita) => cita.estado === 'CONFIRMADA').length;
  $('#mascota-info').textContent = mascota
    ? `${mascota.especie} · Tutor(a): ${mascota.duenio} · ${confirmadas} cita(s) confirmada(s)`
    : '';
}

function renderSlots() {
  const slots = resumenActual()?.slots || [];
  if (slots.length === 0) {
    $('#slots-lista').innerHTML = '<p class="vacio">No hay horarios disponibles para esta mascota.</p>';
    return;
  }
  const libres = slots.filter((slot) => slot.estado === 'LIBRE');
  if (estado.slotId === null && libres.length > 0) estado.slotId = libres[0].id;
  if (estado.slotId !== null && !libres.some((slot) => slot.id === estado.slotId)) estado.slotId = null;

  $('#slots-lista').innerHTML = slots.map((slot) => {
    const disponible = slot.estado === 'LIBRE';
    return `
      <label class="slot ${disponible ? '' : 'is-ocupado'}">
        <input type="radio" name="slot" value="${slot.id}" ${estado.slotId === slot.id ? 'checked' : ''} ${disponible ? '' : 'disabled'}>
        <strong>${esc(formatearFecha(slot.inicio))}</strong>
        <small>${esc(slot.veterinario)}</small>
        <span class="badge ${disponible ? 'badge--libre' : 'badge--reservado'}">${disponible ? 'Disponible' : 'Reservada'}</span>
      </label>`;
  }).join('');
}

function renderResumen() {
  const mascota = resumenActual()?.mascota;
  const slot = (resumenActual()?.slots || []).find((item) => item.id === estado.slotId);
  const monto = Number($('#monto').value);

  $('#res-mascota').textContent = mascota ? `${mascota.nombre} (${mascota.especie})` : '—';
  $('#res-veterinario').textContent = slot ? slot.veterinario : '—';
  $('#res-horario').textContent = slot ? formatearFecha(slot.inicio) : '—';
  $('#res-abono').textContent = Number.isFinite(monto) && monto > 0 ? dinero(monto) : '—';
}

function renderFormulario() {
  renderSlots();
  renderResumen();
}

function renderMascotas() {
  $('#mascotas-grid').innerHTML = [...estado.resumenes.entries()].map(([id, resumen]) => {
    const mascota = resumen.mascota;
    const confirmadas = (resumen.citas || []).filter((cita) => cita.estado === 'CONFIRMADA').length;
    return `
      <button type="button" class="mascota ${id === estado.mascotaId ? 'is-activa' : ''}" data-mascota="${id}">
        <span class="mascota-avatar">${esc(mascota.nombre.charAt(0))}</span>
        <span><strong>${esc(mascota.nombre)}</strong><small>${esc(mascota.especie)} · ${esc(mascota.duenio)}</small></span>
        <span class="badge">${confirmadas} cita(s)</span>
      </button>`;
  }).join('');

  const resumen = resumenActual();
  const citas = resumen?.citas || [];
  const slots = resumen?.slots || [];
  const porSlot = new Map(slots.map((slot) => [slot.id, slot]));

  $('#citas-lista').innerHTML = citas.length === 0
    ? '<p class="vacio">Esta mascota todavía no tiene citas registradas.</p>'
    : citas.map((cita) => {
      const clase = cita.estado === 'CONFIRMADA' ? 'badge--confirmada' : 'badge--anulada';
      return `
        <div class="lista-fila">
          <div><strong>${esc(formatearFecha(porSlot.get(cita.slot_id)?.inicio))}</strong><small>${esc(porSlot.get(cita.slot_id)?.veterinario || 'Veterinario')} · Solicitud ${esc(corto(cita.saga_id))}</small></div>
          <span class="badge ${clase}">${esc(cita.estado)}</span>
        </div>`;
    }).join('');

  $('#slots-estado').innerHTML = slots.length === 0
    ? '<p class="vacio">No hay horarios cargados.</p>'
    : slots.map((slot) => `
      <div class="lista-fila">
        <div><strong>${esc(formatearFecha(slot.inicio))}</strong><small>${esc(slot.veterinario)}</small></div>
        <span class="badge ${slot.estado === 'LIBRE' ? 'badge--libre' : 'badge--reservado'}">${esc(slot.estado)}</span>
      </div>`).join('');

  const avisos = [...new Set([...estado.resumenes.values()].flatMap((item) => item.advertencias || []))];
  const caja = $('#avisos');
  caja.hidden = avisos.length === 0;
  caja.innerHTML = avisos.length === 0 ? '' : `<strong>Algunos datos no están disponibles:</strong> ${avisos.map(esc).join(' · ')}`;
}

function timelineHTML(pasos = []) {
  if (pasos.length === 0) return '<p class="vacio">La solicitud todavía no tiene pasos registrados.</p>';
  return `<ol class="timeline">${pasos.map((paso) => {
    const compensando = paso.resultado === 'COMPENSADO';
    const titulo = compensando
      ? (COMPENSACIONES[paso.accion] || paso.accion)
      : (PASOS[paso.accion] || paso.accion);
    let nota = paso.detalle || 'Sin detalles adicionales.';
    if (paso.detalle && paso.detalle.includes('PagosNoDisponible')) {
      nota = 'Pagos no disponible: respuesta alternativa (fail-fast) y compensación de los pasos anteriores, sin llamar a la pasarela.';
    } else if (paso.detalle && paso.detalle.includes('Falla inducida en Ficha')) {
      nota = 'La confirmación falló antes de registrar la cita, por lo que no quedó nada que anular en Ficha.';
    } else if (compensando) {
      nota = 'Paso anterior deshecho en orden inverso.';
    }
    return `
      <li class="timeline-item timeline-item--${esc(paso.resultado.toLowerCase())}">
        <div class="timeline-titulo"><strong>${esc(titulo)}</strong><time>${esc(formatearHora(paso.ocurrido_en))}</time></div>
        <p>${esc(nota)}</p>
      </li>`;
  }).join('')}</ol>`;
}

function verificacionHTML(slot, citas, advertencias = []) {
  const confirmadas = (citas || []).filter((cita) => cita.estado === 'CONFIRMADA');
  const claseHora = slot?.estado === 'LIBRE' ? 'badge--libre' : 'badge--reservado';
  const claseCita = confirmadas.length > 0 ? 'badge--confirmada' : 'badge--anulada';
  return `
    <h3>Comprobante en los servicios</h3>
    <div class="verificacion-grid">
      <div><span>Agenda · horario</span><strong class="badge ${claseHora}">${esc(slot?.estado || 'sin dato')}</strong><small>${slot ? `ID ${slot.id} · ${esc(formatearFecha(slot.inicio))}` : 'Sin información'}</small></div>
      <div><span>Ficha · cita</span><strong class="badge ${claseCita}">${confirmadas.length > 0 ? 'CONFIRMADA' : 'SIN CITA CONFIRMADA'}</strong><small>${(citas || []).length} registro(s) para esta solicitud</small></div>
      <div><span>Pagos · abono</span><strong>Consultar bitácora</strong><small>El resultado del cobro consta en los pasos de arriba.</small></div>
    </div>
    ${advertencias.length > 0 ? `<p class="info-suave">Avisos: ${advertencias.map(esc).join(' · ')}</p>` : ''}`;
}

function renderResultado(saga, ms) {
  const info = ESTADOS_SAGA[saga.estado] || ESTADOS_SAGA.EN_CURSO;
  const mascota = [...estado.resumenes.values()].find((item) => item.mascota?.id === saga.mascota_id)?.mascota;
  const slot = (resumenActual()?.slots || []).find((item) => item.id === saga.slot_id);

  $('#resultado').innerHTML = `
    <div class="resultado-cabecera">
      <div>
        <p class="eyebrow">Solicitud ${esc(corto(saga.id))}</p>
        <h2>${esc(info.titulo)}</h2>
        <p>${esc(info.texto)}</p>
      </div>
      <span class="estado estado--${info.clase}">${esc(saga.estado)}</span>
    </div>
    <div class="resultado-meta">
      <div><span>Mascota</span><strong>${esc(mascota?.nombre || saga.mascota_id)}</strong></div>
      <div><span>Horario</span><strong>${esc(formatearFecha(slot?.inicio))}</strong></div>
      <div><span>Abono</span><strong>${esc(dinero(saga.monto))}</strong></div>
      <div><span>Tiempo de respuesta</span><strong>${ms} ms</strong></div>
      <div><span>Clave de operación</span><strong><code>${esc(estado.clave)}</code></strong></div>
    </div>
    <h3>Bitácora de la solicitud</h3>
    ${timelineHTML(saga.pasos)}
    <div id="verificacion" class="verificacion"><p class="vacio">Actualizando comprobante…</p></div>
    <div class="acciones">
      <button type="button" class="btn btn--secundario" id="btn-nueva">Nueva reserva</button>
      <button type="button" class="btn btn--texto" id="btn-ver-solicitud">Ver en “Estado de solicitud”</button>
      <button type="button" class="btn btn--texto" id="btn-actualizar">Actualizar comprobante</button>
    </div>`;
  $('#resultado').hidden = false;
}

async function verificarSaga(saga, destino = '#verificacion') {
  const caja = $(destino);
  if (!caja) return;
  try {
    const { datos } = await api(`/api/bff/resumen-mascota/${saga.mascota_id}`);
    estado.resumenes.set(saga.mascota_id, datos);
    const slot = (datos.slots || []).find((item) => item.id === saga.slot_id);
    const citas = (datos.citas || []).filter((cita) => cita.saga_id === saga.id);
    caja.innerHTML = verificacionHTML(slot, citas, datos.advertencias || []);
  } catch (error) {
    caja.innerHTML = `<p class="alerta alerta--aviso">No se pudo actualizar el comprobante: ${esc(error.message)}</p>`;
  }
}

/* ---------------------------- acciones de usuario -------------------------- */

function mostrarError(mensaje) {
  const caja = $('#form-error');
  const texto = mensaje.reintentarDespues
    ? `${mensaje.message} (intenta de nuevo en ${mensaje.reintentarDespues} s)`
    : mensaje.message;
  caja.textContent = texto;
  caja.hidden = false;
}

function ocultarError() {
  $('#form-error').hidden = true;
}

async function confirmarCita(evento) {
  evento.preventDefault();
  if (estado.enviando) return;

  const mascotaId = Number(estado.mascotaId);
  const slotId = Number(estado.slotId);
  const monto = Number($('#monto').value);

  if (!mascotaId) return mostrarError(new Error('Selecciona una mascota.'));
  if (!slotId) return mostrarError(new Error('Selecciona un horario disponible.'));
  if (!Number.isInteger(monto) || monto <= 0) return mostrarError(new Error('Ingresa un monto válido.'));

  ocultarError();
  estado.enviando = true;
  const boton = $('#btn-confirmar');
  boton.disabled = true;
  boton.textContent = 'Procesando…';

  try {
    const { datos, ms } = await api('/api/citas', {
      metodo: 'POST',
      cuerpo: { mascota_id: mascotaId, slot_id: slotId, monto },
      clave: estado.clave,
    });
    estado.saga = datos;
    $('#btn-reintentar').hidden = true;
    renderResultado(datos, ms);
    $('#resultado').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    await cargarResumenes();
    await verificarSaga(datos);
  } catch (error) {
    mostrarError(error);
    if (error.tipo === 'red' || [500, 502, 503, 504].includes(error.status)) {
      $('#btn-reintentar').hidden = false;
    }
  } finally {
    estado.enviando = false;
    boton.disabled = false;
    boton.textContent = 'Confirmar y agendar';
  }
}

async function buscarSaga(evento) {
  if (evento) evento.preventDefault();
  const id = $('#saga-busqueda').value.trim();
  const destino = $('#saga-detalle');
  if (!id) return;

  destino.innerHTML = '<p class="vacio">Buscando solicitud…</p>';
  destino.hidden = false;
  try {
    const { datos } = await api(`/api/citas/${encodeURIComponent(id)}`);
    const slot = (estado.resumenes.get(datos.mascota_id)?.slots || []).find((item) => item.id === datos.slot_id);
    const info = ESTADOS_SAGA[datos.estado] || ESTADOS_SAGA.EN_CURSO;
    destino.innerHTML = `
      <div class="resultado-cabecera">
        <div><p class="eyebrow">Solicitud ${esc(corto(datos.id))}</p><h2>${esc(info.titulo)}</h2><p>${esc(info.texto)}</p></div>
        <span class="estado estado--${info.clase}">${esc(datos.estado)}</span>
      </div>
      <div class="resultado-meta">
        <div><span>Mascota</span><strong>ID ${datos.mascota_id}</strong></div>
        <div><span>Horario</span><strong>${esc(formatearFecha(slot?.inicio))}</strong></div>
        <div><span>Abono</span><strong>${esc(dinero(datos.monto))}</strong></div>
      </div>
      <h3>Bitácora de la solicitud</h3>
      ${timelineHTML(datos.pasos)}
      <div id="verificacion-saga" class="verificacion"><p class="vacio">Actualizando comprobante…</p></div>
      <div class="acciones"><button type="button" class="btn btn--texto" id="btn-actualizar-saga">Actualizar comprobante</button></div>`;
    await verificarSaga(datos, '#verificacion-saga');
  } catch (error) {
    destino.innerHTML = `<p class="alerta alerta--${error.status === 404 ? 'aviso' : 'error'}">${esc(error.message)}</p>`;
  }
}

function cambiarVista(nombre) {
  document.querySelectorAll('.tab').forEach((boton) => {
    const activo = boton.dataset.vista === nombre;
    boton.classList.toggle('is-active', activo);
    if (activo) boton.setAttribute('aria-current', 'page');
    else boton.removeAttribute('aria-current');
  });
  document.querySelectorAll('.vista').forEach((seccion) => {
    seccion.classList.toggle('is-active', seccion.id === `vista-${nombre}`);
  });
  if (nombre === 'admin') {
    cargarSalud();
    cargarMetricas();
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function seleccionarMascota(id) {
  estado.mascotaId = id;
  estado.slotId = null;
  renderMascotaSelect();
  renderFormulario();
  renderMascotas();
}

/* ---------------------------- panel de administración ----------------------- */

function nota(destino, mensaje, error = false) {
  const caja = $(destino);
  if (!caja) return;
  caja.textContent = mensaje;
  caja.classList.toggle('alerta', error);
  caja.classList.toggle('alerta--error', error);
}

async function postAdmin(ruta, cuerpo) {
  const { datos, ms } = await api(ruta, { metodo: 'POST', cuerpo });
  return { datos, ms };
}

async function cargarSalud() {
  const caja = $('#salud-servicios');
  if (!caja) return;
  caja.innerHTML = '<p class="vacio">Consultando servicios…</p>';
  try {
    const { datos } = await api('/api/admin/salud');
    caja.innerHTML = Object.entries(datos).map(([nombre, info]) => `
      <div class="salud-item ${info.ok ? 'is-ok' : 'is-error'}">
        <strong>${esc(nombre)}</strong>
        <small>${info.ok ? 'OK' : esc(info.error || `HTTP ${info.codigo}`)} · ${info.ms} ms</small>
      </div>`).join('');
  } catch (error) {
    caja.innerHTML = `<p class="alerta alerta--error">${esc(error.message)}</p>`;
  }
}

async function cargarMetricas() {
  const caja = $('#metricas-resiliencia');
  if (!caja) return;
  try {
    const { datos } = await api('/api/admin/metricas');
    const r = datos.resiliencia || {};
    const circuito = r.estado_circuito || '—';
    const claseCircuito = circuito === 'ABIERTO' ? 'is-alerta' : (circuito === 'CERRADO' ? 'is-ok' : '');
    const patrones = [r.breaker_enabled ? 'Circuit Breaker' : '', r.bulkhead_enabled ? 'Bulkhead' : ''].filter(Boolean).join(' + ') || 'desactivados';
    const filas = [
      ['Circuito de Pagos', circuito, 'ABIERTO = fail-fast', claseCircuito],
      ['Fallos consecutivos', r.fallos_consecutivos ?? '—', 'umbral configurable'],
      ['Rechazos del circuito', r.llamadas_rechazadas_circuito ?? '—', 'sin llamar a Pagos'],
      ['Rechazos del bulkhead', r.llamadas_rechazadas_bulkhead ?? '—', 'sin cupo, inmediato'],
      ['Cupos en uso', `${r.cupos_bulkhead_en_uso ?? '—'} / ${r.cupos_bulkhead_max ?? '—'}`, 'concurrencia sobre Pagos'],
      ['Patrones activos', patrones, 'configuración actual'],
    ];
    caja.innerHTML = filas.map(([etiqueta, valor, detalle, clase]) => `
      <div class="metrica ${clase || ''}"><span>${esc(etiqueta)}</span><strong>${esc(valor)}</strong><small>${esc(detalle)}</small></div>`).join('');
    $('#metricas-actualizadas').textContent = `Actualizado: ${new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date())}`;
  } catch (error) {
    caja.innerHTML = `<p class="alerta alerta--error">${esc(error.message)}</p>`;
  }
}

async function configurarFicha(activa) {
  try {
    const { datos } = await postAdmin('/api/admin/ficha/fallo', { activa });
    nota('#estado-ficha', datos.activa
      ? 'Falla activa: confirmar una cita responderá 500.'
      : 'Ficha sana: las confirmaciones vuelven a funcionar.');
    avisar(datos.activa ? 'Falla de Ficha activada.' : 'Falla de Ficha desactivada.');
  } catch (error) {
    nota('#estado-ficha', error.message, true);
  }
}

async function configurarPagos(latencia, falla) {
  try {
    const { datos } = await postAdmin('/api/admin/pagos/chaos', { latencia_ms: Number(latencia), falla: Boolean(falla) });
    $('#chaos-latencia').value = datos.latencia_ms;
    $('#chaos-falla').checked = datos.falla;
    nota('#estado-pagos', `Pasarela: latencia ${datos.latencia_ms} ms · falla ${datos.falla}.`);
    avisar('Configuración de Pagos aplicada.');
  } catch (error) {
    nota('#estado-pagos', error.message, true);
  }
}

async function agregarHorarios() {
  const cantidad = Number($('#slots-cantidad').value);
  if (!Number.isInteger(cantidad) || cantidad < 1 || cantidad > 100) {
    return nota('#estado-slots', 'Indica una cantidad entre 1 y 100.', true);
  }
  try {
    const { datos } = await postAdmin('/api/admin/slots', { cantidad });
    const ids = datos.ids || [];
    nota('#estado-slots', `Se agregaron ${datos.agregados} horarios libres (IDs ${ids.slice(0, 5).join(', ')}${ids.length > 5 ? '…' : ''}).`);
    await cargarResumenes();
  } catch (error) {
    nota('#estado-slots', error.message, true);
  }
}

async function aplicarLimite() {
  const porMinuto = Number($('#limite-valor').value) || 0;
  try {
    const { datos } = await postAdmin('/api/admin/limite', { por_minuto: porMinuto });
    $('#limite-valor').value = datos.limite_por_minuto;
    nota('#estado-limite', `Límite vigente: ${datos.limite_por_minuto} peticiones por minuto.`);
  } catch (error) {
    nota('#estado-limite', error.message, true);
  }
}

async function generarCarga() {
  const cantidad = 12;
  const caja = $('#resultado-carga');
  const boton = $('#btn-carga');
  boton.disabled = true;
  boton.textContent = 'Enviando…';
  caja.innerHTML = '<p class="vacio">Preparando horarios y enviando solicitudes…</p>';
  try {
    let resumen = (await api('/api/bff/resumen-mascota/1')).datos;
    let libres = (resumen.slots || []).filter((slot) => slot.estado === 'LIBRE');
    if (libres.length < cantidad) {
      await postAdmin('/api/admin/slots', { cantidad: cantidad - libres.length });
      resumen = (await api('/api/bff/resumen-mascota/1')).datos;
      libres = (resumen.slots || []).filter((slot) => slot.estado === 'LIBRE');
    }
    if (libres.length < cantidad) throw new Error('No hay suficientes horarios libres para la prueba.');

    const resultados = await Promise.allSettled(libres.slice(0, cantidad).map(async (slot) => {
      const respuesta = await api('/api/citas', {
        metodo: 'POST',
        cuerpo: { mascota_id: 1, slot_id: slot.id, monto: 15000 },
        clave: `carga-${nuevaClave()}`,
      });
      return { ms: respuesta.ms, estado: respuesta.datos.estado };
    }));

    const lista = resultados.map((resultado) => resultado.status === 'fulfilled'
      ? resultado.value
      : { ms: resultado.reason?.ms || 0, estado: `HTTP ${resultado.reason?.status || 'error'}` });
    const tiempos = lista.map((item) => item.ms);
    const promedio = Math.round(tiempos.reduce((suma, ms) => suma + ms, 0) / tiempos.length);
    const conteo = lista.reduce((acc, item) => {
      acc[item.estado] = (acc[item.estado] || 0) + 1;
      return acc;
    }, {});

    caja.innerHTML = `
      <div class="lista-fila"><div><strong>${cantidad} solicitudes simultáneas</strong><small>promedio ${promedio} ms · mín ${Math.min(...tiempos)} ms · máx ${Math.max(...tiempos)} ms</small></div></div>
      ${Object.entries(conteo).map(([estado, veces]) => `<div class="lista-fila"><div><strong>${esc(estado)}</strong></div><span class="badge">${veces}</span></div>`).join('')}
      <button type="button" class="btn btn--texto" id="btn-refrescar-tras-carga">Actualizar métricas y salud</button>`;
    await Promise.allSettled([cargarMetricas(), cargarSalud(), cargarResumenes()]);
  } catch (error) {
    caja.innerHTML = `<p class="alerta alerta--error">${esc(error.message)}</p>`;
  } finally {
    boton.disabled = false;
    boton.textContent = 'Enviar 12 reservas';
  }
}

async function restaurarTodo() {
  const boton = $('#btn-restaurar');
  boton.disabled = true;
  try {
    await postAdmin('/api/admin/ficha/fallo', { activa: false });
    await postAdmin('/api/admin/pagos/chaos', { latencia_ms: 0, falla: false });
    await postAdmin('/api/admin/limite', { por_minuto: 0 });
    $('#chaos-latencia').value = 0;
    $('#chaos-falla').checked = false;
    $('#limite-valor').value = 0;
    nota('#estado-restaurar', 'Ficha y Pagos sanos, límite original. El circuito se cierra solo con la próxima llamada exitosa.');
    await Promise.allSettled([cargarSalud(), cargarMetricas(), cargarResumenes()]);
    avisar('Servicios restaurados.');
  } catch (error) {
    nota('#estado-restaurar', error.message, true);
  } finally {
    boton.disabled = false;
  }
}

let resetArmado = false;
let temporizadorReset = null;

async function resetearDatos() {
  const boton = $('#btn-reset-datos');
  if (!resetArmado) {
    resetArmado = true;
    boton.textContent = 'Confirmar reset (2º clic)';
    nota('#estado-reset', 'Borra citas, pagos, reservas y sagas; vuelven los horarios y mascotas iniciales.');
    temporizadorReset = setTimeout(() => {
      resetArmado = false;
      boton.textContent = 'Resetear datos';
      nota('#estado-reset', '');
    }, 30000);
    return;
  }

  clearTimeout(temporizadorReset);
  resetArmado = false;
  boton.textContent = 'Resetear datos';
  boton.disabled = true;
  try {
    const { datos, ms } = await api('/api/admin/reset', { metodo: 'POST', cuerpo: {} });
    const detalle = Object.entries(datos)
      .filter(([clave]) => clave !== 'indicadores')
      .map(([clave, valor]) => `${clave} ${Object.values(valor).join('/')}`)
      .join(' · ');
    nota('#estado-reset', `Datos reseteados en ${ms} ms (${detalle}).`);
    await Promise.allSettled([cargarSalud(), cargarMetricas(), cargarResumenes()]);
    avisar('Datos reseteados.');
  } catch (error) {
    nota('#estado-reset', error.message, true);
  } finally {
    boton.disabled = false;
  }
}

/* --------------------------------- eventos --------------------------------- */

$('#form-cita').addEventListener('submit', confirmarCita);
$('#btn-reintentar').addEventListener('click', () => $('#form-cita').requestSubmit());
$('#mascota-select').addEventListener('change', (evento) => seleccionarMascota(Number(evento.target.value)));
$('#slots-lista').addEventListener('change', (evento) => {
  if (evento.target.name === 'slot') {
    estado.slotId = Number(evento.target.value);
    renderResumen();
  }
});
$('#monto').addEventListener('input', renderResumen);
$('#form-busqueda').addEventListener('submit', buscarSaga);

$('#mascotas-grid').addEventListener('click', (evento) => {
  const boton = evento.target.closest('[data-mascota]');
  if (!boton) return;
  seleccionarMascota(Number(boton.dataset.mascota));
  cambiarVista('agendar');
});

document.querySelectorAll('.tab').forEach((boton) => {
  boton.addEventListener('click', () => cambiarVista(boton.dataset.vista));
});

$('#resultado').addEventListener('click', async (evento) => {
  if (evento.target.closest('#btn-nueva')) {
    estado.clave = nuevaClave();
    estado.saga = null;
    estado.slotId = null;
    $('#clave-valor').textContent = estado.clave;
    $('#resultado').hidden = true;
    $('#btn-reintentar').hidden = true;
    ocultarError();
    await cargarResumenes();
    avisar('Listo para una nueva solicitud.');
  }
  if (evento.target.closest('#btn-ver-solicitud') && estado.saga) {
    $('#saga-busqueda').value = estado.saga.id;
    cambiarVista('solicitudes');
    await buscarSaga();
  }
  if (evento.target.closest('#btn-actualizar') && estado.saga) {
    await verificarSaga(estado.saga);
    avisar('Comprobante actualizado.');
  }
});

$('#saga-detalle').addEventListener('click', async (evento) => {
  if (!evento.target.closest('#btn-actualizar-saga')) return;
  const id = $('#saga-busqueda').value.trim();
  if (id) await buscarSaga();
});

$('#btn-refrescar-salud').addEventListener('click', cargarSalud);
$('#btn-refrescar-metricas').addEventListener('click', cargarMetricas);
$('#btn-ficha-on').addEventListener('click', () => configurarFicha(true));
$('#btn-ficha-off').addEventListener('click', () => configurarFicha(false));
document.querySelectorAll('[data-chaos]').forEach((boton) => {
  boton.addEventListener('click', () => {
    const valores = {
      sano: [0, false],
      lento: [3000, false],
      caido: [0, true],
    }[boton.dataset.chaos];
    configurarPagos(valores[0], valores[1]);
  });
});
$('#btn-chaos-aplicar').addEventListener('click', () => configurarPagos($('#chaos-latencia').value, $('#chaos-falla').checked));
$('#btn-agregar-slots').addEventListener('click', agregarHorarios);
$('#btn-aplicar-limite').addEventListener('click', aplicarLimite);
$('#btn-carga').addEventListener('click', generarCarga);
$('#btn-restaurar').addEventListener('click', restaurarTodo);
$('#btn-reset-datos').addEventListener('click', resetearDatos);
$('#resultado-carga').addEventListener('click', (evento) => {
  if (evento.target.closest('#btn-refrescar-tras-carga')) {
    cargarMetricas();
    cargarSalud();
  }
});

/* ---------------------------------- inicio --------------------------------- */

async function iniciar() {
  $('#monto').value = CFG.MONTO_DEFAULT;
  $('#clave-valor').textContent = estado.clave;
  await cargarResumenes();
  await verificarSalud();
  setInterval(verificarSalud, 30000);
}

iniciar();
