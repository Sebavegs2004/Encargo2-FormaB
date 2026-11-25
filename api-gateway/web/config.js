/* Configuración del cliente web.
 * La web se sirve desde el propio api-gateway (mismo origen), por eso API_BASE
 * queda vacío. Si cambia GATEWAY_TOKEN en .env, cambiar también TOKEN aquí.
 */
window.CLINICA_CONFIG = {
  API_BASE: '',
  TOKEN: 'token-clinica-123',
  MASCOTA_IDS: [1, 2, 3],
  MONTO_DEFAULT: 15000,
};
