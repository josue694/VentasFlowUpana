/**
 * VentasFlow — script.js v3.0
 * Mejoras: Voucher PDF, Calculadora de cambio, Favoritos, Descuento,
 * Bloqueo por inactividad, Sonido, Confetti, Meta de ventas,
 * Exportar Excel, Filtro por fechas, Iconos de pago
 */

const API = "http://127.0.0.1:8000/api";

let currentUser   = null;
let authToken     = null;
let cart          = [];
let charts        = {};
let searchTimer   = null;
let favorites     = JSON.parse(localStorage.getItem("vf_favorites") || "[]");
let inactivityTimer = null;
let lastSaleData  = null;
let salesGoal     = parseFloat(localStorage.getItem("vf_goal") || "5000");

// ─── ÍCONOS DE MÉTODO DE PAGO ─────────────────────────────────────────────────
const paymentIcons = {
  "EFECTIVO":     { icon: "💵", label: "Efectivo",     color: "#34d399" },
  "TARJETA":      { icon: "💳", label: "Tarjeta",      color: "#818cf8" },
  "QR":           { icon: "📱", label: "QR/Transferencia", color: "#67e8f9" },
};
function paymentBadge(method) {
  const p = paymentIcons[method] || { icon: "💰", label: method, color: "#94a3b8" };
  return `<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:700;background:${p.color}18;border:1px solid ${p.color}44;color:${p.color};">
    ${p.icon} ${p.label}
  </span>`;
}

// ─── UTILIDADES ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function toast(msg, type = "info", duration = 3500) {
  const icons = { success: "✓", error: "✗", info: "ℹ", warning: "⚠" };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || "ℹ"}</span><span style="margin-left:8px">${msg}</span>`;
  $("toast-container").appendChild(el);
  setTimeout(() => el.remove(), duration);
}

async function apiFetch(endpoint, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  try {
    const res = await fetch(`${API}${endpoint}`, { ...options, headers });
    if (res.status === 401) { doLogout(); return null; }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Error en la solicitud");
    return data;
  } catch (e) {
    toast(e.message, "error");
    return null;
  }
}

function fmtCurrency(n) {
  return "Q " + Number(n || 0).toLocaleString("es-GT", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDate(str) {
  if (!str) return "—";
  return new Date(str).toLocaleString("es-GT", { dateStyle: "short", timeStyle: "short" });
}
function roleBadgeClass(role) {
  return { SUPERADMIN: "role-admin", ADMIN: "role-admin", GERENTE: "role-gerente", CAJERO: "role-cajero" }[role] || "role-cajero";
}
function licenseBadgeClass(lic) {
  return { BASIC: "badge-trial", TRIAL: "badge-trial", PRO: "badge-pro", ENTERPRISE: "badge-enterprise" }[lic] || "badge-pro";
}

// ─── SONIDO BEEP ──────────────────────────────────────────────────────────────
function playBeep(freq = 880, duration = 80) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration / 1000);
    osc.start(); osc.stop(ctx.currentTime + duration / 1000);
  } catch(e) {}
}
function playSuccess() { playBeep(880, 80); setTimeout(() => playBeep(1100, 120), 100); }
function playError()   { playBeep(220, 200); }

// ─── CONFETTI ─────────────────────────────────────────────────────────────────
function launchConfetti() {
  const colors = ["#6366f1","#22d3ee","#ec4899","#10b981","#f59e0b","#ffffff"];
  for (let i = 0; i < 80; i++) {
    const el = document.createElement("div");
    el.style.cssText = `
      position:fixed; z-index:9999; pointer-events:none;
      width:${Math.random()*10+5}px; height:${Math.random()*10+5}px;
      background:${colors[Math.floor(Math.random()*colors.length)]};
      left:${Math.random()*100}vw; top:-10px;
      border-radius:${Math.random()>0.5?"50%":"2px"};
      animation: confettiFall ${Math.random()*2+1.5}s ease-in forwards;
      opacity:${Math.random()*0.8+0.2};
    `;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }
}
// inyectar keyframes de confetti una sola vez
if (!document.getElementById("confetti-style")) {
  const s = document.createElement("style");
  s.id = "confetti-style";
  s.textContent = `@keyframes confettiFall {
    0%   { transform: translateY(0) rotate(0deg); }
    100% { transform: translateY(100vh) rotate(${Math.random()*720}deg); opacity:0; }
  }`;
  document.head.appendChild(s);
}

// ─── BLOQUEO POR INACTIVIDAD ─────────────────────────────────────────────────
const INACTIVITY_MS = 10 * 60 * 1000; // 10 minutos

function resetInactivityTimer() {
  clearTimeout(inactivityTimer);
  inactivityTimer = setTimeout(lockScreen, INACTIVITY_MS);
}

function lockScreen() {
  if (!currentUser) return;
  $("lock-username") && ($("lock-username").textContent = currentUser.username);
  const lock = $("modal-lock");
  if (lock) { lock.style.display = "flex"; lock.classList.remove("hidden"); }
}

async function unlockScreen() {
  const pass = $("lock-password")?.value;
  if (!pass) return toast("Ingresa tu contraseña", "error");
  const form = new URLSearchParams({ username: currentUser.username, password: pass });
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: form
    });
    if (res.ok) {
      closeModal("modal-lock");
      if ($("lock-password")) $("lock-password").value = "";
      resetInactivityTimer();
      toast("Sesión desbloqueada", "success");
    } else {
      toast("Contraseña incorrecta", "error");
      playError();
    }
  } catch(e) { toast("Error al verificar", "error"); }
}

function setupInactivityWatcher() {
  ["mousemove","keydown","click","touchstart"].forEach(ev =>
    document.addEventListener(ev, resetInactivityTimer, { passive: true })
  );
  resetInactivityTimer();
}

// ─── TEMA ─────────────────────────────────────────────────────────────────────
let isDarkMode = true;
function toggleTheme() {
  isDarkMode = !isDarkMode;
  document.documentElement.classList.toggle("dark", isDarkMode);
  document.body.classList.toggle("light-mode", !isDarkMode);
  if ($("theme-toggle")) $("theme-toggle").textContent = isDarkMode ? "🌙" : "☀️";
  localStorage.setItem("vf_theme", isDarkMode ? "dark" : "light");
}
function initTheme() {
  const saved = localStorage.getItem("vf_theme");
  if (saved === "light") {
    isDarkMode = false;
    document.documentElement.classList.remove("dark");
    document.body.classList.add("light-mode");
    if ($("theme-toggle")) $("theme-toggle").textContent = "☀️";
  }
}

// ─── RELOJ ────────────────────────────────────────────────────────────────────
function startClock() {
  const update = () => { if ($("topbar-time")) $("topbar-time").textContent = new Date().toLocaleTimeString("es-GT"); };
  update(); setInterval(update, 1000);
}

// ─── LOGIN TABS ───────────────────────────────────────────────────────────────
function switchLoginTab(tab) {
  ["signin","request","forgot"].forEach(t => {
    $(`pane-${t}`)?.classList.add("hidden");
    $(`tab-${t}`)?.classList.remove("active");
  });
  $(`pane-${tab}`)?.classList.remove("hidden");
  $(`tab-${tab}`)?.classList.add("active");
}

// ─── LOGIN ────────────────────────────────────────────────────────────────────
async function doLogin() {
  const user = $("login-user").value.trim();
  const pass = $("login-pass").value;
  if (!user || !pass) return toast("Ingresa usuario y contraseña", "error");
  if ($("login-btn-txt")) $("login-btn-txt").innerHTML = `<div class="spinner" style="margin:0 auto;"></div>`;
  const form = new URLSearchParams({ username: user, password: pass });
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: form
    });
    const data = await res.json();
    if (!res.ok) {
      toast(data.detail || "Credenciales incorrectas", "error"); playError();
      if ($("login-btn-txt")) $("login-btn-txt").textContent = "Ingresar al Sistema";
      return;
    }
    authToken = data.access_token; currentUser = data.user;
    localStorage.setItem("vf_token", authToken);
    localStorage.setItem("vf_user", JSON.stringify(currentUser));
    launchApp();
  } catch(e) {
    toast("No se pudo conectar al servidor", "error");
    if ($("login-btn-txt")) $("login-btn-txt").textContent = "Ingresar al Sistema";
  }
}

async function doRequestAccess() {
  const email = $("req-email").value.trim();
  if (!email) return toast("Ingresa tu correo", "error");
  const res = await apiFetch("/auth/request-access", { method:"POST", body: JSON.stringify({ email }) });
  if (res) { toast(res.message, "success"); $("req-email").value = ""; }
}
async function doForgotPassword() {
  const email = $("forgot-email").value.trim();
  if (!email) return toast("Ingresa tu correo", "error");
  const res = await apiFetch("/auth/forgot-password", { method:"POST", body: JSON.stringify({ email }) });
  if (res) { toast("Código enviado (ver consola backend)", "success"); $("forgot-step1")?.classList.add("hidden"); $("forgot-step2")?.classList.remove("hidden"); }
}
async function doResetPassword() {
  const email = $("forgot-email").value.trim();
  const code  = $("reset-code").value.trim();
  const newPass = $("new-password").value;
  if (!code || !newPass) return toast("Completa todos los campos", "error");
  const res = await apiFetch("/auth/reset-password", { method:"POST", body: JSON.stringify({ email, code, new_password: newPass }) });
  if (res) { toast("Contraseña actualizada", "success"); switchLoginTab("signin"); $("forgot-step1")?.classList.remove("hidden"); $("forgot-step2")?.classList.add("hidden"); }
}

function doLogout() {
  clearTimeout(inactivityTimer);
  authToken = currentUser = null; cart = [];
  localStorage.removeItem("vf_token"); localStorage.removeItem("vf_user");
  Object.values(charts).forEach(c => c?.destroy?.()); charts = {};
  $("app")?.classList.add("hidden");
  if ($("login-screen")) $("login-screen").style.display = "flex";
  if ($("login-pass")) $("login-pass").value = "";
  if ($("login-btn-txt")) $("login-btn-txt").textContent = "Ingresar al Sistema";
  switchLoginTab("signin");
}

// ─── AUTO-LOGIN ───────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
  initTheme();
  try {
    const savedToken = localStorage.getItem("vf_token");
    const savedUser  = localStorage.getItem("vf_user");
    if (savedToken && savedUser) {
      const parsed = JSON.parse(savedUser);
      if (parsed && parsed.id && parsed.username && parsed.role) {
        authToken = savedToken; currentUser = parsed; launchApp(); return;
      }
    }
  } catch(e) { console.warn("Sesión inválida:", e); }
  localStorage.removeItem("vf_token"); localStorage.removeItem("vf_user");
});

// ─── LAUNCH APP ───────────────────────────────────────────────────────────────
function launchApp() {
  if ($("login-screen")) $("login-screen").style.display = "none";
  $("app")?.classList.remove("hidden");
  initTheme(); buildSidebar(); updateSidebarUser(); startClock(); setupInactivityWatcher();
  const licEl = $("topbar-license");
  if (licEl) { licEl.className = licenseBadgeClass(currentUser.license); licEl.textContent = currentUser.license; }
  const defaultView = { SUPERADMIN:"license", ADMIN:"admin", GERENTE:"manager", CAJERO:"pos" }[currentUser.role] || "pos";
  navigate(defaultView);
}

// ─── SIDEBAR ──────────────────────────────────────────────────────────────────
function buildSidebar() {
  const menus = {
    SUPERADMIN: [{ id:"license", icon:"🛡️", label:"Licencias SaaS" }, { id:"admin", icon:"⚡", label:"Panel Admin" }, { id:"profile", icon:"👤", label:"Mi Perfil" }],
    ADMIN:      [{ id:"admin",   icon:"⚡", label:"Panel de Control" }, { id:"profile", icon:"👤", label:"Mi Perfil" }],
    GERENTE:    [{ id:"manager", icon:"📊", label:"Analytics" }, { id:"profile", icon:"👤", label:"Mi Perfil" }],
    CAJERO:     [{ id:"pos",     icon:"🛒", label:"Terminal POS" }, { id:"profile", icon:"👤", label:"Mi Perfil" }],
  };
  const items = menus[currentUser.role] || menus.CAJERO;
  $("nav-items").innerHTML = items.map(m => `
    <div class="nav-item" id="nav-${m.id}" onclick="navigate('${m.id}')">
      <span class="icon">${m.icon}</span><span>${m.label}</span>
    </div>`).join("");
}
function updateSidebarUser() {
  const avatar = currentUser.photo_url || `https://api.dicebear.com/7.x/shapes/svg?seed=${currentUser.username}`;
  if ($("sidebar-avatar")) $("sidebar-avatar").src = avatar;
  if ($("sidebar-username")) $("sidebar-username").textContent = currentUser.username;
  if ($("sidebar-role")) $("sidebar-role").textContent = `${currentUser.role} · ${currentUser.license}`;
}

// ─── NAVEGACIÓN ───────────────────────────────────────────────────────────────
const viewMeta = {
  license: { title:"Gestión de Licencias",   subtitle:"Administración del plan SaaS" },
  admin:   { title:"Panel de Control",        subtitle:"Gestión de usuarios y métricas SaaS" },
  manager: { title:"Dashboard Analítico",     subtitle:"KPIs y reportes en tiempo real" },
  pos:     { title:"Terminal POS",            subtitle:"Punto de venta — Estación activa" },
  profile: { title:"Mi Perfil",               subtitle:"Configuración de cuenta y licencia" },
};
function navigate(view) {
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  $(`nav-${view}`)?.classList.add("active");
  document.querySelectorAll(".section-view").forEach(el => el.classList.remove("active"));
  $(`view-${view}`)?.classList.add("active");
  const meta = viewMeta[view] || {};
  if ($("page-title"))    $("page-title").textContent    = meta.title || "";
  if ($("page-subtitle")) $("page-subtitle").textContent = meta.subtitle || "";
  if (view === "license") loadLicensePanel();
  if (view === "admin")   loadAdminMetrics();
  if (view === "manager") loadManagerKPIs();
  if (view === "pos")     initPOS();
  if (view === "profile") loadProfile();
}

// ─── SUPERADMIN / LICENCIAS ───────────────────────────────────────────────────
async function loadLicensePanel() {
  const lic = await apiFetch("/superadmin/license");
  if (!lic) return;
  if ($("license-type")) $("license-type").value = lic.license_type || "PRO";
  if ($("license-start")) $("license-start").value = lic.start_date || "";
  if ($("license-end")) $("license-end").value = lic.end_date || "";
  if ($("license-status")) $("license-status").value = String(lic.status ?? 1);
  if ($("license-current-type")) $("license-current-type").textContent = lic.license_type || "—";
  if ($("license-current-users")) $("license-current-users").textContent = lic.max_users ?? "—";
  if ($("license-current-dashboard")) $("license-current-dashboard").textContent = lic.dashboard_enabled ? "✓ Sí" : "✗ No";
  if ($("license-current-reports")) $("license-current-reports").textContent = lic.reports_enabled ? "✓ Sí" : "✗ No";
  if ($("license-current-validity")) $("license-current-validity").textContent = `${lic.start_date || "—"} → ${lic.end_date || "—"}`;
  if ($("license-current-status")) $("license-current-status").textContent = lic.status === 1 ? "✓ Activa" : "✗ Inactiva";
}
async function updateLicense() {
  const license_type = $("license-type")?.value;
  const start_date   = $("license-start")?.value;
  const end_date     = $("license-end")?.value;
  const status       = Number($("license-status")?.value ?? 1);
  if (!license_type || !start_date || !end_date) return toast("Completa todos los campos", "error");
  const res = await apiFetch("/superadmin/license", { method:"PUT", body: JSON.stringify({ license_type, start_date, end_date, status }) });
  if (res) {
    currentUser.license = license_type;
    localStorage.setItem("vf_user", JSON.stringify(currentUser));
    const licEl = $("topbar-license");
    if (licEl) { licEl.className = licenseBadgeClass(license_type); licEl.textContent = license_type; }
    updateSidebarUser(); loadProfile(); loadLicensePanel();
    toast("Licencia actualizada correctamente", "success");
  }
}

// ─── ADMIN ────────────────────────────────────────────────────────────────────
async function loadAdminMetrics() {
  const [metrics, users] = await Promise.all([apiFetch("/admin/metrics"), apiFetch("/admin/users")]);
  if (!metrics || !users) return;
  if ($("kpi-users")) $("kpi-users").textContent = metrics.total_users;
  if ($("kpi-prods")) $("kpi-prods").textContent = metrics.total_products.toLocaleString();
  if ($("kpi-sales")) $("kpi-sales").textContent = fmtCurrency(metrics.total_sales);
  if ($("kpi-license")) $("kpi-license").textContent = metrics.system_license?.license_type || currentUser.license;
  if ($("chart-roles")) renderDonut("chart-roles", metrics.roles.map(r=>r.role), metrics.roles.map(r=>r.cnt), ["#818cf8","#67e8f9","#34d399","#f472b6"]);
  if ($("chart-licenses")) renderDonut("chart-licenses", metrics.licenses.map(l=>l.license), metrics.licenses.map(l=>l.cnt), ["#fbbf24","#818cf8","#f472b6"]);
  if ($("users-table-body")) {
    $("users-table-body").innerHTML = users.map(u => `
      <tr>
        <td><div style="display:flex;align-items:center;gap:8px;">
          <img src="${u.photo_url || `https://api.dicebear.com/7.x/shapes/svg?seed=${u.username}`}" style="width:28px;height:28px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);object-fit:cover;" />
          <span style="color:#e2e8f0;font-weight:500;">${u.username}</span>
        </div></td>
        <td>${u.email}</td>
        <td><span class="${roleBadgeClass(u.role)}">${u.role}</span></td>
        <td><span class="${licenseBadgeClass(u.license)}">${u.license}</span></td>
        <td><span style="font-size:12px;font-weight:600;${u.active?'color:#34d399':'color:#f87171'}">${u.active?"✓ Activo":"✗ Inactivo"}</span></td>
        <td>${u.created_at?.slice(0,10)||"—"}</td>
        <td>${u.id !== currentUser.id && u.active
          ? `<button class="btn-danger" onclick="adminDeactivateUser(${u.id},'${u.username}')">Expulsar</button>`
          : `<span style="color:#334155;font-size:12px;">—</span>`}</td>
      </tr>`).join("");
  }
}
async function adminCreateUser() {
  const username = $("new-username").value.trim();
  const email    = $("new-email").value.trim();
  const role     = $("new-role").value;
  if (!username || !email) return toast("Completa todos los campos", "error");
  const res = await apiFetch("/admin/users", { method:"POST", body: JSON.stringify({ username, email, role }) });
  if (res) { toast(res.message, "success"); $("new-username").value = $("new-email").value = ""; loadAdminMetrics(); }
}
async function adminDeactivateUser(id, username) {
  if (!confirm(`¿Expulsar a "${username}"?`)) return;
  const res = await apiFetch(`/admin/users/${id}`, { method:"DELETE" });
  if (res) { toast(`${username} ha sido removido`, "success"); loadAdminMetrics(); }
}

// ─── GERENTE ──────────────────────────────────────────────────────────────────
async function loadManagerKPIs() {
  const data = await apiFetch("/manager/kpis");
  if (!data) return;
  if ($("mgr-revenue")) $("mgr-revenue").textContent = fmtCurrency(data.total_revenue);
  if ($("mgr-day"))     $("mgr-day").textContent     = fmtCurrency(data.day_sales);
  if ($("mgr-avg"))     $("mgr-avg").textContent     = fmtCurrency(data.avg_ticket);
  if ($("mgr-txns"))    $("mgr-txns").textContent    = data.total_transactions.toLocaleString();

  // Meta de ventas diaria
  const goalPct = Math.min(Math.round((data.day_sales / salesGoal) * 100), 100);
  if ($("goal-bar"))  $("goal-bar").style.width  = goalPct + "%";
  if ($("goal-text")) $("goal-text").textContent = `${fmtCurrency(data.day_sales)} / ${fmtCurrency(salesGoal)} (${goalPct}%)`;

  const labels = data.weekly_sales.map(d => new Date(d.date + "T12:00:00").toLocaleDateString("es-GT", { weekday:"short", day:"numeric" }));
  if ($("chart-weekly")) renderLineChart("chart-weekly", labels, data.weekly_sales.map(d => d.total));
  const paymentCounts = { EFECTIVO:0, TARJETA:0, QR:0 };
  data.last_transactions.forEach(t => { paymentCounts[t.payment_method] = (paymentCounts[t.payment_method]||0) + 1; });
  if ($("chart-payment")) renderDonut("chart-payment", ["💵 Efectivo","💳 Tarjeta","📱 QR"], Object.values(paymentCounts), ["#34d399","#818cf8","#67e8f9"]);

  if ($("transactions-table")) {
    $("transactions-table").innerHTML = data.last_transactions.map(t => `
      <tr>
        <td style="color:#818cf8;font-family:monospace;">#${t.id}</td>
        <td>${t.cashier_name}</td>
        <td>${t.customer_name}</td>
        <td>${paymentBadge(t.payment_method)}</td>
        <td style="font-weight:600;color:#e2e8f0;">${fmtCurrency(t.total)}</td>
        <td style="color:#475569;">${fmtDate(t.created_at)}</td>
      </tr>`).join("");
  }
}

// Exportar a Excel (CSV descargable)
async function exportToExcel() {
  const data = await apiFetch("/manager/kpis");
  if (!data) return;
  const rows = [["#","Cajero","Cliente","Método","Total","Fecha"]];
  data.last_transactions.forEach(t => rows.push([t.id, t.cashier_name, t.customer_name, t.payment_method, t.total.toFixed(2), t.created_at]));
  const csv = rows.map(r => r.map(c => `"${c}"`).join(",")).join("\n");
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = `ventas_ventasflow_${new Date().toISOString().slice(0,10)}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
  toast("Exportado a CSV/Excel exitosamente", "success");
}

// Filtrar ventas por fecha
async function filterByDate() {
  const from = $("filter-from")?.value;
  const to   = $("filter-to")?.value;
  if (!from || !to) return toast("Selecciona ambas fechas", "error");
  const data = await apiFetch("/manager/kpis");
  if (!data) return;
  const filtered = data.last_transactions.filter(t => {
    const d = t.created_at.slice(0,10);
    return d >= from && d <= to;
  });
  if ($("transactions-table")) {
    $("transactions-table").innerHTML = filtered.length === 0
      ? `<tr><td colspan="6" style="text-align:center;padding:20px;color:#475569;">Sin transacciones en ese rango</td></tr>`
      : filtered.map(t => `
          <tr>
            <td style="color:#818cf8;font-family:monospace;">#${t.id}</td>
            <td>${t.cashier_name}</td><td>${t.customer_name}</td>
            <td>${paymentBadge(t.payment_method)}</td>
            <td style="font-weight:600;color:#e2e8f0;">${fmtCurrency(t.total)}</td>
            <td style="color:#475569;">${fmtDate(t.created_at)}</td>
          </tr>`).join("");
    toast(`${filtered.length} transacciones encontradas`, "info");
  }
}

// Meta de ventas
function saveGoal() {
  const val = parseFloat($("goal-input")?.value);
  if (isNaN(val) || val <= 0) return toast("Ingresa un monto válido", "error");
  salesGoal = val;
  localStorage.setItem("vf_goal", val);
  toast(`Meta actualizada: ${fmtCurrency(val)}`, "success");
  loadManagerKPIs();
}

async function downloadPDF() {
  toast("Generando reporte PDF...", "info");
  try {
    const res = await fetch(`${API}/manager/report-pdf`, { headers: { Authorization: `Bearer ${authToken}` } });
    if (!res.ok) { const err = await res.json(); return toast(err.detail || "Error PDF", "error"); }
    const blob = await res.blob(); const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "reporte_ventasflow.pdf"; a.click();
    URL.revokeObjectURL(url); toast("PDF descargado", "success");
  } catch(e) { toast("Error al generar el PDF", "error"); }
}

// ─── CHARTS ───────────────────────────────────────────────────────────────────
function destroyChart(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }
function renderDonut(canvasId, labels, data, colors) {
  destroyChart(canvasId);
  const ctx = $(canvasId)?.getContext("2d"); if (!ctx) return;
  charts[canvasId] = new Chart(ctx, {
    type:"doughnut",
    data:{ labels, datasets:[{ data, backgroundColor:colors.map(c=>c+"33"), borderColor:colors, borderWidth:2 }] },
    options:{ responsive:true, plugins:{ legend:{ position:"bottom", labels:{ color:"#94a3b8", font:{ family:"DM Sans", size:12 }, padding:12 }}}, cutout:"65%" }
  });
}
function renderLineChart(canvasId, labels, data) {
  destroyChart(canvasId);
  const ctx = $(canvasId)?.getContext("2d"); if (!ctx) return;
  const gradient = ctx.createLinearGradient(0,0,0,220);
  gradient.addColorStop(0,"rgba(99,102,241,0.35)"); gradient.addColorStop(1,"rgba(99,102,241,0.0)");
  charts[canvasId] = new Chart(ctx, {
    type:"line",
    data:{ labels, datasets:[{ label:"Ventas (Q)", data, borderColor:"#818cf8", borderWidth:2.5, backgroundColor:gradient, fill:true, tension:0.45, pointBackgroundColor:"#6366f1", pointBorderColor:"#e2e8f0", pointBorderWidth:2, pointRadius:5, pointHoverRadius:7 }] },
    options:{
      responsive:true, interaction:{ mode:"index", intersect:false },
      plugins:{ legend:{ display:false }, tooltip:{ backgroundColor:"rgba(13,17,23,0.95)", borderColor:"rgba(99,102,241,0.4)", borderWidth:1, titleColor:"#818cf8", bodyColor:"#e2e8f0", padding:12, callbacks:{ label:ctx=>` Q ${ctx.parsed.y.toLocaleString("es-GT",{minimumFractionDigits:2})}` }}},
      scales:{
        x:{ grid:{ color:"rgba(255,255,255,0.04)" }, ticks:{ color:"#475569", font:{ family:"DM Sans", size:11 }}},
        y:{ grid:{ color:"rgba(255,255,255,0.04)" }, ticks:{ color:"#475569", font:{ family:"DM Sans", size:11 }, callback:v=>`Q${(v/1000).toFixed(0)}k` }}
      }
    }
  });
}

// ─── POS ──────────────────────────────────────────────────────────────────────
async function initPOS() {
  await loadCategories(); await loadPOSProducts(); renderCart();
  if ($("goal-input")) $("goal-input").value = salesGoal;
}
async function loadCategories() {
  const cats = await apiFetch("/products/categories"); if (!cats) return;
  $("pos-category").innerHTML = `<option value="">Todas las categorías</option>` + cats.map(c=>`<option value="${c}">${c}</option>`).join("");
}
async function loadPOSProducts() {
  const search   = $("pos-search")?.value.trim() || "";
  const category = $("pos-category")?.value || "";
  if ($("products-loading")) $("products-loading").classList.remove("hidden");
  if ($("products-grid")) $("products-grid").innerHTML = "";
  let url = `/products?limit=80`;
  if (search)   url += `&search=${encodeURIComponent(search)}`;
  if (category) url += `&category=${encodeURIComponent(category)}`;
  const prods = await apiFetch(url);
  if ($("products-loading")) $("products-loading").classList.add("hidden");
  if (!prods) return;

  // Mostrar favoritos primero
  const favProds = prods.filter(p => favorites.includes(p.id));
  const restProds = prods.filter(p => !favorites.includes(p.id));
  const sorted = [...favProds, ...restProds];

  if (sorted.length === 0) {
    if ($("products-grid")) $("products-grid").innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px;color:#334155;"><p style="font-size:2rem;margin-bottom:8px;">🔍</p><p>Sin resultados</p></div>`;
    return;
  }
  if ($("products-grid")) {
    $("products-grid").innerHTML = sorted.map(p => `
      <div class="product-card" onclick='addToCart(${JSON.stringify(p)})' style="position:relative;">
        <button onclick="event.stopPropagation();toggleFavorite(${p.id})" style="position:absolute;top:6px;right:6px;background:none;border:none;cursor:pointer;font-size:14px;opacity:0.7;" title="Favorito">
          ${favorites.includes(p.id) ? "⭐" : "☆"}
        </button>
        <div style="width:40px;height:40px;border-radius:12px;background:rgba(99,102,241,0.1);border:1px solid rgba(99,102,241,0.2);display:flex;align-items:center;justify-content:center;font-size:18px;margin:0 auto 8px;">${getCategoryEmoji(p.category)}</div>
        <p style="font-size:12px;font-weight:600;color:#e2e8f0;line-height:1.3;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">${p.name}</p>
        <p style="font-size:11px;color:#334155;margin-bottom:6px;">${p.sku}</p>
        <p style="font-family:'Syne',sans-serif;font-weight:700;font-size:14px;color:#818cf8;">${fmtCurrency(p.sale_price)}</p>
        <p style="font-size:11px;margin-top:4px;${p.stock < 20 ? 'color:#fbbf24;' : 'color:#334155;'}">Stock: ${p.stock}</p>
      </div>`).join("");
  }
}

const categoryEmojis = {"Lácteos":"🥛","Carnes":"🥩","Abarrotes":"🌾","Bebidas":"🧃","Panadería":"🍞","Frutas":"🍎","Verduras":"🥦","Limpieza":"🧹","Higiene":"🧴","Congelados":"🧊","Snacks":"🍿","Cereales":"🥣","Condimentos":"🧂","Mascotas":"🐾","Bebé":"👶"};
function getCategoryEmoji(cat) { return categoryEmojis[cat] || "📦"; }

function debouncedSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(loadPOSProducts, 300); }

// Favoritos
function toggleFavorite(id) {
  const idx = favorites.indexOf(id);
  if (idx === -1) favorites.push(id); else favorites.splice(idx, 1);
  localStorage.setItem("vf_favorites", JSON.stringify(favorites));
  loadPOSProducts();
}

// Scan por SKU
async function scanBySKU() {
  const input = $("pos-search"); const sku = input?.value.trim();
  if (!sku || sku.length < 3) return;
  const prods = await apiFetch(`/products?search=${encodeURIComponent(sku)}&limit=1`);
  if (prods && prods.length > 0) {
    const exact = prods.find(p => p.sku.toLowerCase() === sku.toLowerCase()) || prods[0];
    addToCart(exact); playBeep(1000, 60);
    if (input) input.value = "";
    toast(`✓ ${exact.name.substring(0,30)}`, "success", 1500);
  } else { toast("Producto no encontrado", "error", 1500); playError(); }
}

// ─── CARRITO ──────────────────────────────────────────────────────────────────
function addToCart(product) {
  const existing = cart.find(i => i.product_id === product.id);
  if (existing) {
    if (existing.quantity >= product.stock) { toast(`Stock máximo: ${product.stock}`, "error"); playError(); return; }
    existing.quantity++; existing.subtotal = existing.quantity * existing.unit_price;
  } else {
    cart.push({ product_id:product.id, product_name:product.name, quantity:1, unit_price:product.sale_price, subtotal:product.sale_price, max_stock:product.stock });
    playBeep(880, 60);
  }
  renderCart();
}
function removeFromCart(productId) { cart = cart.filter(i => i.product_id !== productId); renderCart(); }
function updateCartQty(productId, delta) {
  const item = cart.find(i => i.product_id === productId); if (!item) return;
  item.quantity += delta;
  if (item.quantity <= 0) { removeFromCart(productId); return; }
  if (item.quantity > item.max_stock) { item.quantity = item.max_stock; toast(`Máximo: ${item.max_stock}`, "error"); }
  item.subtotal = item.quantity * item.unit_price; renderCart();
}
function clearCart() { cart = []; renderCart(); }
function getDiscount() {
  const d = parseFloat($("cart-discount")?.value || 0);
  return isNaN(d) ? 0 : Math.min(Math.max(d, 0), 50);
}

function renderCart() {
  if (!$("cart-items") || !$("cart-total")) return;
  if (cart.length === 0) {
    $("cart-items").innerHTML = `<div style="text-align:center;padding:32px;color:#334155;"><p style="font-size:2rem;margin-bottom:8px;">🛒</p><p style="font-size:13px;">El carrito está vacío</p></div>`;
  } else {
    $("cart-items").innerHTML = cart.map(item => `
      <div class="cart-item">
        <div style="flex:1;min-width:0;">
          <p style="font-size:12px;font-weight:600;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${item.product_name}</p>
          <p style="font-size:11px;color:#818cf8;margin-top:2px;">${fmtCurrency(item.unit_price)} c/u</p>
        </div>
        <div style="display:flex;align-items:center;gap:4px;">
          <button onclick="updateCartQty(${item.product_id},-1)" style="width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:#94a3b8;cursor:pointer;font-size:13px;">−</button>
          <span style="font-size:12px;font-weight:700;color:#e2e8f0;width:20px;text-align:center;">${item.quantity}</span>
          <button onclick="updateCartQty(${item.product_id},1)" style="width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:#94a3b8;cursor:pointer;font-size:13px;">+</button>
        </div>
        <div style="text-align:right;margin-left:8px;">
          <p style="font-size:12px;font-weight:700;color:#e2e8f0;">${fmtCurrency(item.subtotal)}</p>
          <button onclick="removeFromCart(${item.product_id})" style="font-size:11px;color:rgba(248,113,113,0.5);background:none;border:none;cursor:pointer;" onmouseover="this.style.color='#f87171'" onmouseout="this.style.color='rgba(248,113,113,0.5)'">✕</button>
        </div>
      </div>`).join("");
  }
  const subtotal = cart.reduce((s,i) => s+i.subtotal, 0);
  const discount = getDiscount();
  const total    = subtotal * (1 - discount/100);
  $("cart-total").textContent = fmtCurrency(total);
  // Calculadora de cambio
  updateChange();
}

// ─── CALCULADORA DE CAMBIO ────────────────────────────────────────────────────
function updateChange() {
  const paid = parseFloat($("cart-paid")?.value || 0);
  const subtotal = cart.reduce((s,i) => s+i.subtotal, 0);
  const total    = subtotal * (1 - getDiscount()/100);
  const change   = paid - total;
  if ($("cart-change")) {
    $("cart-change").textContent = change >= 0 ? `Vuelto: ${fmtCurrency(change)}` : "";
    $("cart-change").style.color = change >= 0 ? "#34d399" : "#f87171";
  }
}

// ─── PROCESAR VENTA ───────────────────────────────────────────────────────────
async function processSale() {
  if (cart.length === 0) return toast("Agrega productos al carrito primero", "error");
  const subtotal = cart.reduce((s,i) => s+i.subtotal, 0);
  const discount = getDiscount();
  const total    = subtotal * (1 - discount/100);

  // Validar pago en efectivo
  const method = $("cart-payment")?.value;
  if (method === "EFECTIVO") {
    const paid = parseFloat($("cart-paid")?.value || 0);
    if (paid > 0 && paid < total) return toast(`El monto pagado (${fmtCurrency(paid)}) es menor al total`, "error");
  }

  const btn = $("btn-pay");
  if (btn) { btn.disabled = true; btn.innerHTML = `<div class="spinner" style="margin:0 auto;"></div>`; }

  const payload = {
    customer_name:  $("cart-customer")?.value || "Consumidor Final",
    customer_nit:   $("cart-nit")?.value || "CF",
    payment_method: method || "EFECTIVO",
    total, items: cart.map(i => ({ product_id:i.product_id, product_name:i.product_name, quantity:i.quantity, unit_price:i.unit_price, subtotal:i.subtotal }))
  };

  const res = await apiFetch("/sales", { method:"POST", body: JSON.stringify(payload) });
  if (btn) { btn.disabled = false; btn.textContent = "✓ Procesar Pago"; }

  if (res) {
    lastSaleData = { ...payload, sale_id:res.sale_id, discount, paid: parseFloat($("cart-paid")?.value || 0) };
    playSuccess();
    launchConfetti();
    toast(`🎉 Venta #${res.sale_id} — ${fmtCurrency(total)}`, "success", 5000);
    showVoucherModal(lastSaleData);
    clearCart();
    if ($("cart-customer")) $("cart-customer").value = "Consumidor Final";
    if ($("cart-nit"))      $("cart-nit").value = "CF";
    if ($("cart-discount")) $("cart-discount").value = "";
    if ($("cart-paid"))     $("cart-paid").value = "";
    loadPOSProducts();
  }
}

// ─── VOUCHER / TICKET ─────────────────────────────────────────────────────────
function buildVoucherHTML(saleData, forPrint = false) {
  const subtotal     = saleData.items.reduce((s,i) => s+i.subtotal, 0);
  const discountAmt  = subtotal * ((saleData.discount||0)/100);
  const change       = (saleData.paid||0) > 0 ? (saleData.paid - saleData.total) : 0;
  const p            = paymentIcons[saleData.payment_method] || { icon:"💰", label:saleData.payment_method };
  const now          = new Date().toLocaleString("es-GT");

  return `
  <div style="font-family:'Courier New',monospace;background:#fff;color:#000;padding:20px;max-width:320px;margin:0 auto;">
    <div style="text-align:center;border-bottom:2px dashed #000;padding-bottom:12px;margin-bottom:12px;">
      <p style="font-size:20px;font-weight:700;letter-spacing:2px;">🛒 VENTASFLOW</p>
      <p style="font-size:11px;">Supermercado · Sistema POS v2.0</p>
      <p style="font-size:10px;color:#666;">${now}</p>
    </div>
    <div style="margin-bottom:10px;font-size:12px;">
      <p><b>No. Venta:</b> #${String(saleData.sale_id).padStart(6,"0")}</p>
      <p><b>Cajero:</b> ${currentUser?.username || "—"}</p>
      <p><b>Cliente:</b> ${saleData.customer_name}</p>
      <p><b>NIT:</b> ${saleData.customer_nit}</p>
    </div>
    <div style="border-top:1px dashed #000;border-bottom:1px dashed #000;padding:8px 0;margin-bottom:10px;">
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        <tr style="font-weight:700;border-bottom:1px solid #ccc;">
          <td style="padding:3px 0;">Descripción</td>
          <td style="text-align:center;padding:3px 4px;">Cant</td>
          <td style="text-align:right;padding:3px 0;">Total</td>
        </tr>
        ${saleData.items.map(i=>`
        <tr>
          <td style="padding:3px 0;max-width:160px;">${i.product_name.substring(0,20)}</td>
          <td style="text-align:center;padding:3px 4px;">${i.quantity}</td>
          <td style="text-align:right;padding:3px 0;">Q${i.subtotal.toFixed(2)}</td>
        </tr>`).join("")}
      </table>
    </div>
    <div style="font-size:12px;margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;"><span>Subtotal:</span><span>Q${subtotal.toFixed(2)}</span></div>
      ${saleData.discount > 0 ? `<div style="display:flex;justify-content:space-between;color:green;"><span>Descuento (${saleData.discount}%):</span><span>-Q${discountAmt.toFixed(2)}</span></div>` : ""}
      <div style="display:flex;justify-content:space-between;font-weight:700;font-size:15px;border-top:2px solid #000;margin-top:6px;padding-top:4px;"><span>TOTAL:</span><span>Q${saleData.total.toFixed(2)}</span></div>
      <div style="display:flex;justify-content:space-between;margin-top:4px;"><span>Pago ${p.icon} ${p.label}:</span><span>${saleData.paid > 0 ? `Q${saleData.paid.toFixed(2)}` : "—"}</span></div>
      ${change > 0 ? `<div style="display:flex;justify-content:space-between;color:green;font-weight:700;"><span>Vuelto:</span><span>Q${change.toFixed(2)}</span></div>` : ""}
    </div>
    <div style="border-top:2px dashed #000;padding-top:10px;text-align:center;font-size:11px;color:#555;">
      <p style="font-weight:700;">¡Gracias por su compra!</p>
      <p>Conserve este comprobante</p>
      <p style="margin-top:4px;font-size:10px;">VentasFlow SaaS · ventasflow.com</p>
    </div>
  </div>`;
}

function showVoucherModal(saleData) {
  const tc = $("ticket-content");
  if (tc) tc.innerHTML = buildVoucherHTML(saleData);
  openModal("modal-ticket");
}

function printTicket() {
  if (!lastSaleData) return;
  const w = window.open("","_blank","width=420,height=680");
  if (!w) return;
  w.document.write(`<html><head><title>Voucher #${lastSaleData.sale_id}</title></head><body>${buildVoucherHTML(lastSaleData, true)}<script>window.print();setTimeout(()=>window.close(),1200);<\/script></body></html>`);
  w.document.close();
}

function downloadVoucherPDF() {
  if (!lastSaleData) return;
  // Crear ventana de impresión con instrucciones para guardar como PDF
  const w = window.open("","_blank","width=420,height=680");
  if (!w) return;
  w.document.write(`<html><head><title>Voucher #${lastSaleData.sale_id}</title>
  <style>@media print{body{margin:0;}.no-print{display:none;}}</style>
  </head><body>
  <div class="no-print" style="background:#6366f1;color:white;padding:10px;text-align:center;font-family:sans-serif;font-size:13px;">
    💡 Presiona Ctrl+P → Guardar como PDF para descargar el voucher
  </div>
  ${buildVoucherHTML(lastSaleData, true)}</body></html>`);
  w.document.close();
}

// ─── MODALES ──────────────────────────────────────────────────────────────────
function openModal(id) { const el=$(id); if(el){el.classList.remove("hidden");el.style.display="flex";} }
function closeModal(id) { const el=$(id); if(el){el.style.display="none";el.classList.add("hidden");} }
document.addEventListener("click", e => {
  if (e.target.classList.contains("modal-overlay")) { e.target.style.display="none"; e.target.classList.add("hidden"); }
});

// ─── PERFIL ───────────────────────────────────────────────────────────────────
function loadProfile() {
  const u = currentUser; if (!u) return;
  const avatar = u.photo_url || `https://api.dicebear.com/7.x/shapes/svg?seed=${u.username}`;
  if ($("profile-avatar")) { $("profile-avatar").src = avatar; $("profile-avatar").onerror = ()=>{ $("profile-avatar").src=`https://api.dicebear.com/7.x/shapes/svg?seed=${u.username}`; }; }
  if ($("profile-name"))      $("profile-name").textContent  = u.username;
  if ($("profile-email"))     $("profile-email").textContent = u.email || "—";
  if ($("profile-photo-url")) $("profile-photo-url").value   = u.photo_url || "";
  const rb = $("profile-role-badge");    if (rb) { rb.className=roleBadgeClass(u.role);    rb.textContent=u.role; }
  const lb = $("profile-license-badge"); if (lb) { lb.className=licenseBadgeClass(u.license); lb.textContent=u.license; }
}
async function updatePhoto() {
  const url = $("profile-photo-url").value.trim();
  const res = await apiFetch("/users/me/profile", { method:"PUT", body: JSON.stringify({ photo_url:url }) });
  if (res) { currentUser.photo_url=url; localStorage.setItem("vf_user",JSON.stringify(currentUser)); updateSidebarUser(); loadProfile(); toast("Foto actualizada","success"); }
}
async function changePassword() {
  const curr=$("curr-pass").value, nw=$("new-pass").value, conf=$("conf-pass").value;
  if (!curr||!nw||!conf) return toast("Completa todos los campos","error");
  if (nw!==conf) return toast("Las contraseñas no coinciden","error");
  if (nw.length<6) return toast("Mínimo 6 caracteres","error");
  const res = await apiFetch("/users/me/password",{method:"PUT",body:JSON.stringify({current_password:curr,new_password:nw})});
  if (res) { toast("Contraseña actualizada","success"); $("curr-pass").value=$("new-pass").value=$("conf-pass").value=""; }
}
