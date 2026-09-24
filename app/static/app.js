// Utilitários compartilhados pelas telas. Toda regra de negócio fica na API.
const App = (() => {
  const MONTHS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];

  function qs(params) {
    if (!params) return "";
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== "") p.append(k, v);
    }
    const s = p.toString();
    return s ? `?${s}` : "";
  }

  // Mensagens comuns do Pydantic -> português. Tipo primeiro (estável); mensagem como reserva.
  function humanizeValidationEntry(e) {
    const ctx = e.ctx || {};
    switch (e.type) {
      case "missing": return "Este campo é obrigatório.";
      case "string_too_short": return `Use pelo menos ${ctx.min_length} caractere${ctx.min_length === 1 ? "" : "s"}.`;
      case "string_too_long": return `Use no máximo ${ctx.max_length} caracteres.`;
      case "int_parsing": case "int_type": return "Informe um número válido.";
      case "float_parsing": case "float_type": return "Informe um número válido.";
      case "date_parsing": case "date_from_datetime_parsing": case "date_type":
        return "Informe uma data válida.";
      case "bool_parsing": case "bool_type": return "Valor inválido para este campo.";
      case "literal_error": case "enum": return "Valor inválido para este campo.";
      case "value_error": return e.msg.replace(/^Value error,\s*/, "");
      default: return e.msg;
    }
  }

  // Uma entrada de erro do Pydantic tem loc = ["body", "campo"] (ou aninhado). O
  // último segmento costuma bater com o name="" do form correspondente.
  function fieldFromLoc(loc) {
    const rest = (loc || []).slice(1);
    return rest.length ? String(rest[rest.length - 1]) : null;
  }

  const NETWORK_ERROR_MESSAGE = "Não foi possível conectar ao sistema. Verifique sua conexão e tente novamente.";
  const SESSION_EXPIRED_MESSAGE = "Sua sessão expirou. Faça login novamente.";

  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body instanceof FormData) {
      opts.body = body; // o navegador define o Content-Type (multipart + boundary) sozinho
    } else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch (_networkErr) {
      const err = new Error(NETWORK_ERROR_MESSAGE);
      err.isNetworkError = true;
      throw err;
    }
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? await res.json() : null;
    if (!res.ok) {
      if (res.status === 401) {
        toast(SESSION_EXPIRED_MESSAGE, "error");
        const next = encodeURIComponent(location.pathname + location.search);
        setTimeout(() => { location.href = `/login?next=${next}`; }, 900);
        const err = new Error(SESSION_EXPIRED_MESSAGE);
        err.status = 401;
        err.handled = true;
        throw err;
      }
      let msg = data && data.detail;
      let fieldErrors = [];
      if (Array.isArray(msg)) {
        fieldErrors = msg.map((e) => ({ field: fieldFromLoc(e.loc), message: humanizeValidationEntry(e) }));
        msg = fieldErrors.map((f) => (f.field ? `${f.field}: ${f.message}` : f.message)).join("; ");
      }
      const err = new Error(msg || `Erro ${res.status}`);
      err.status = res.status;
      err.details = (data && data.details) || {};
      err.fieldErrors = fieldErrors;
      throw err;
    }
    return data;
  }

  const get = (url, params) => api("GET", url + qs(params));

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const [y, m, d] = String(iso).slice(0, 10).split("-");
    return `${d}/${m}/${y}`;
  }

  const fmtNum = (v, digits = 1) => (v == null ? "—" : Number(v).toFixed(digits).replace(".", ","));
  const pct = (v) => (v == null ? "—" : `${fmtNum(v)}%`);

  function todayISO() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  }

  function fillMonthYear(monthSel, yearSel, month, year) {
    const now = new Date();
    monthSel.innerHTML = MONTHS.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");
    const years = [];
    for (let y = now.getFullYear() + 1; y >= 2025; y--) years.push(y);
    yearSel.innerHTML = years.map((y) => `<option value="${y}">${y}</option>`).join("");
    monthSel.value = month || now.getMonth() + 1;
    yearSel.value = year || now.getFullYear();
  }

  async function fillWorkshops(select, { allLabel = null, selected = null, activeOnly = true } = {}) {
    const items = await get("/api/workshops", activeOnly ? { active: true } : null);
    select.innerHTML = (allLabel ? `<option value="">${esc(allLabel)}</option>` : "") +
      items.map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join("");
    if (selected && items.some((w) => String(w.id) === String(selected))) select.value = selected;
    return items;
  }

  async function fillClasses(select, workshopId, { allLabel = null, selected = null, activeOnly = true } = {}) {
    const params = { workshop_id: workshopId || null };
    if (activeOnly) params.active = true;
    const items = await get("/api/classes", params);
    select.innerHTML = (allLabel ? `<option value="">${esc(allLabel)}</option>` : "") +
      items.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    if (selected && items.some((c) => String(c.id) === String(selected))) select.value = selected;
    return items;
  }

  let toastTimer;
  function toast(message, type = "ok") {
    const el = document.getElementById("toast");
    el.textContent = message;
    const isError = type === "error";
    el.className = `toast show ${isError ? "error" : ""}`;
    el.setAttribute("role", isError ? "alert" : "status");
    el.setAttribute("aria-live", isError ? "assertive" : "polite");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.className = "toast"), isError ? 6000 : 3500);
  }

  // Estado de "não carregou" com botão de tentar de novo. `retryFn` é o nome de
  // uma função global (cada página já expõe load()/init() no escopo do <script>).
  function errorState(message, retryFn = "load") {
    return `<div class="empty error-state" role="alert">
      <p>${esc(message)}</p>
      <button class="btn btn-sm" type="button" onclick="${esc(retryFn)}()">Tentar novamente</button>
    </div>`;
  }

  // ---- erros inline em formulários/modais (nunca só toast atrás de <dialog>)
  function clearFormErrors(form) {
    form.querySelectorAll(".field-error").forEach((el) => el.remove());
    form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
    const banner = form.querySelector(".form-error");
    if (banner) { banner.hidden = true; banner.textContent = ""; }
  }

  function showFormErrors(form, err) {
    clearFormErrors(form);
    const fieldErrors = (err.fieldErrors || []).filter((f) => f.field && form.elements[f.field]);
    if (fieldErrors.length) {
      let first = null;
      for (const { field, message } of fieldErrors) {
        const input = form.elements[field];
        input.setAttribute("aria-invalid", "true");
        const errId = `${field}-error`;
        input.setAttribute("aria-describedby", errId);
        const small = document.createElement("small");
        small.className = "field-error";
        small.id = errId;
        small.setAttribute("role", "alert");
        small.textContent = message;
        (input.closest(".field") || input.parentElement).appendChild(small);
        first = first || input;
      }
      first?.focus();
      return;
    }
    const banner = form.querySelector(".form-error");
    if (banner) {
      banner.textContent = err.message;
      banner.hidden = false;
    } else {
      toast(err.message, "error");
    }
  }

  // Lê campos [name] de um formulário. data-type="int" -> número ou null; checkbox -> boolean; vazio -> null.
  function formData(form) {
    const out = {};
    for (const el of form.querySelectorAll("[name]")) {
      if (el.type === "checkbox") out[el.name] = el.checked;
      else if (el.dataset.type === "int") out[el.name] = el.value ? Number(el.value) : null;
      else out[el.name] = el.value.trim() === "" ? null : el.value.trim();
    }
    return out;
  }

  function fillForm(form, values) {
    for (const el of form.querySelectorAll("[name]")) {
      const v = values[el.name];
      if (el.type === "checkbox") el.checked = v ?? true;
      else el.value = v ?? "";
    }
  }

  const params = () => new URLSearchParams(location.search);
  function store(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* sem storage */ } }
  function load(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }

  return {
    MONTHS, api, get, qs, esc, fmtDate, fmtNum, pct, todayISO, fillMonthYear, fillWorkshops, fillClasses,
    toast, errorState, clearFormErrors, showFormErrors, formData, fillForm, params, store, load,
  };
})();

// Toast: clicar dispensa antes do tempo (fechar "se simples", sem markup extra).
document.addEventListener("DOMContentLoaded", () => {
  const toastEl = document.getElementById("toast");
  toastEl?.addEventListener("click", () => (toastEl.className = "toast"));
});
