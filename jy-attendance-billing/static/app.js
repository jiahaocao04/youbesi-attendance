const $ = (id) => document.getElementById(id);

const state = {
  user: null,
  students: [],
  classes: [],
  attendance: [],
  users: [],
  changed: new Set(),
  attendanceKey: "",
  dirty: false,
  saving: false,
  draftTimer: null,
  lastSavedAt: "",
  networkUrls: [],
  attendanceView: "roster",
};

function currentMonth() {
  return new Date().toISOString().slice(0, 7);
}

function currentDate() {
  return new Date().toISOString().slice(0, 10);
}

function isAdmin() {
  return state.user && state.user.role === "admin";
}

function operatorName() {
  return state.user?.display_name || state.user?.username || "unknown";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || 12000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...options,
    }).catch((err) => {
      if (err.name === "AbortError") throw new Error("网络超时，请检查 Wi-Fi 或电脑服务是否开启");
      throw err;
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok || data.ok === false) {
      if (res.status === 401 && path !== "/api/me" && path !== "/api/login") {
        showLogin();
      }
      throw new Error(data.error || "请求失败");
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function money(value) {
  const num = Number(value || 0);
  return num.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function setTable(table, headers, rows) {
  table.innerHTML = "";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      if (cell instanceof Node) td.appendChild(cell);
      else td.innerHTML = cell ?? "";
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
}

function card(label, value) {
  return `<div class="card"><div>${label}</div><div class="value">${value}</div></div>`;
}

function renderCards(container, items) {
  container.innerHTML = items.map(([label, value]) => card(label, value)).join("");
}

function selectedAttendanceKey() {
  const dt = $("attendanceDate").value || currentDate();
  const klass = $("attendanceClass").value || "";
  return `${dt}|${klass}`;
}

function draftKey() {
  return `jy_attendance_draft:${state.user?.username || "user"}:${state.attendanceKey}`;
}

function statusLabel(value) {
  return { present: "到", leave: "请假", absent: "未到", unmarked: "未点名" }[value] || value;
}

function cssId(value) {
  return window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/"/g, '\\"');
}

function summarizeAttendance() {
  const summary = {
    a: 0,
    p: 0,
    fullDay: 0,
    absent: 0,
    shoppingTotal: 0,
    events: 0,
  };
  state.attendance.forEach((r) => {
    const lunch = r.lunch_status || "unmarked";
    const care = r.care_status || "unmarked";
    if (lunch === "present") summary.a += 1;
    if (care === "present") summary.p += 1;
    if (lunch === "present" && care === "present") summary.fullDay += 1;
    if (lunch !== "present" && care !== "present") summary.absent += 1;
    if ((r.note || "").trim()) summary.events += 1;
    summary.shoppingTotal += Number(r.shopping_amount || 0);
  });
  summary.shoppingTotal = Number(summary.shoppingTotal.toFixed(2));
  return summary;
}

function updateAttendanceSummary() {
  const box = $("attendanceSummary");
  const status = $("attendanceStatus");
  if (!box || !status) return;
  const s = summarizeAttendance();
  box.innerHTML = `
    <div><strong>A</strong> ${s.a} 人｜<strong>P</strong> ${s.p} 人｜<strong>全天</strong> ${s.fullDay} 人</div>
    <div><strong>未出勤</strong> ${s.absent} 人｜<strong>事件</strong> ${s.events}｜<strong>购物</strong> ${money(s.shoppingTotal)}</div>
  `;
  const unsaved = state.changed.size;
  if (state.saving) {
    status.textContent = "正在保存，请不要关闭页面……";
    status.className = "save-status saving";
  } else if (unsaved > 0) {
    status.textContent = `有 ${unsaved} 名学生未保存，已自动保存在本机草稿`;
    status.className = "save-status dirty";
  } else if (state.lastSavedAt) {
    status.textContent = `已保存｜${state.lastSavedAt}`;
    status.className = "save-status saved";
  } else {
    status.textContent = "点名表已载入";
    status.className = "save-status";
  }
}

function saveAttendanceDraftSoon() {
  clearTimeout(state.draftTimer);
  state.draftTimer = setTimeout(() => {
    if (!state.attendanceKey || !state.changed.size) return;
    const draft = {
      savedAt: new Date().toISOString(),
      changed: Array.from(state.changed),
      records: state.attendance.map((r) => ({
        permanent_id: r.permanent_id,
        lunch_status: r.lunch_status,
        care_status: r.care_status,
        bed_status: r.bed_status,
        event_mark: Number(r.event_mark || 0),
        note: r.note || "",
        shopping_item: r.shopping_item || "",
        shopping_amount: Number(r.shopping_amount || 0),
      })),
    };
    localStorage.setItem(draftKey(), JSON.stringify(draft));
  }, 120);
}

function restoreAttendanceDraft() {
  if (!state.attendanceKey) return;
  const raw = localStorage.getItem(draftKey());
  if (!raw) return;
  try {
    const draft = JSON.parse(raw);
    const byId = new Map((draft.records || []).map((r) => [r.permanent_id, r]));
    state.attendance.forEach((r) => {
      const d = byId.get(r.permanent_id);
      if (!d) return;
      r.lunch_status = d.lunch_status || r.lunch_status;
      r.care_status = d.care_status || r.care_status;
      r.bed_status = d.bed_status || r.bed_status;
      r.event_mark = Number(d.event_mark || 0);
      r.note = d.note || r.note || "";
      r.shopping_item = d.shopping_item || r.shopping_item || "";
      r.shopping_amount = Number(d.shopping_amount || r.shopping_amount || 0);
    });
    state.changed = new Set(draft.changed || []);
    if (state.changed.size) {
      state.dirty = true;
      toast("已恢复上次未保存的点名草稿");
    }
  } catch {
    localStorage.removeItem(draftKey());
  }
}

function clearAttendanceDraft() {
  if (state.attendanceKey) localStorage.removeItem(draftKey());
}

function markAttendanceChanged(pid) {
  state.changed.add(pid);
  state.dirty = true;
  saveAttendanceDraftSoon();
  updateAttendanceSummary();
  updateAbsentReminder();
}

function absentReasons(rec) {
  const reasons = [];
  if (rec.lunch_status !== "present") reasons.push("A");
  if (rec.care_status !== "present") reasons.push("P");
  return reasons;
}

function absentItems() {
  return state.attendance
    .map((r) => ({ rec: r, reasons: absentReasons(r) }))
    .filter((x) => x.reasons.length);
}

function updateAbsentReminder() {
  const box = $("absentReminder");
  const count = $("absentCount");
  if (!box || !isAdmin()) return;
  const absent = absentItems();
  if (count) count.textContent = absent.length;
  if (!absent.length) {
    box.innerHTML = `<div class="notice">今天没有未到提醒。</div>`;
  } else {
    const rows = absent.map(({ rec, reasons }) => [
      rec.name,
      `${rec.grade}-${rec.class_no}`,
      reasons.includes("A") ? "未到" : "到",
      reasons.includes("P") ? "未到" : "到",
      rec.note || "",
    ]);
    box.innerHTML = `<div class="table-wrap"><table id="absentTable"></table></div>`;
    setTable($("absentTable"), ["学生", "班级", "A午餐", "P下午", "事件提醒"], rows);
  }
  box.classList.toggle("hidden", state.attendanceView !== "absent");
}

function setAttendanceView(view) {
  state.attendanceView = view;
  $("showAttendanceRoster")?.classList.toggle("active", view === "roster");
  $("showAbsentReminder")?.classList.toggle("active", view === "absent");
  $("attendanceList")?.classList.toggle("hidden", view !== "roster");
  $("absentReminder")?.classList.toggle("hidden", view !== "absent");
  updateAbsentReminder();
}

function showLogin() {
  state.user = null;
  $("loginScreen").classList.remove("hidden");
  $("appShell").classList.add("hidden");
}

function showApp() {
  $("loginScreen").classList.add("hidden");
  $("appShell").classList.remove("hidden");
  $("currentUser").textContent = `${operatorName()}｜${state.user.role === "admin" ? "管理员" : "老师"}`;
  document.querySelectorAll("[data-admin-only='1']").forEach((el) => {
    el.classList.toggle("hidden", !isAdmin());
  });
  activateTab("attendance");
}

function activateTab(tab) {
  document.querySelectorAll(".tabs button").forEach((btn) => {
    const active = btn.dataset.tab === tab;
    btn.classList.toggle("active", active);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.remove("active");
  });
  const target = $(`tab-${tab}`);
  if (target) target.classList.add("active");
}

async function login() {
  const username = $("loginUsername").value.trim();
  const pin = $("loginPin").value;
  const data = await api("/api/login", {
    method: "POST",
    body: JSON.stringify({ username, pin }),
  });
  state.user = data.user;
  $("loginPin").value = "";
  await afterLoginLoad();
}

async function logout() {
  await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
  showLogin();
}

async function loadMe() {
  const data = await api("/api/me");
  state.user = data.user;
  if (!state.user) {
    showLogin();
    return;
  }
  await afterLoginLoad();
}

async function afterLoginLoad() {
  showApp();
  await loadClasses();
  await loadNetworkInfo();
  if (isAdmin()) {
    await loadSettings();
    await loadStudents();
    await refreshDashboard();
    await loadPayments();
    await loadSettlement();
    await loadUsers();
    await loadCosts();
  }
  if (state.classes.length) {
    await loadAttendance();
  }
}

async function loadNetworkInfo() {
  try {
    const data = await api("/api/network", { timeoutMs: 4000 });
    state.networkUrls = data.urls || [];
    const box = $("networkInfo");
    if (box) {
      box.innerHTML = `
        <strong>多设备访问：</strong>
        老师手机和这台电脑连同一个 Wi-Fi 后，可打开：
        ${state.networkUrls.map((url) => `<code>${url}</code>`).join("　")}
      `;
    }
  } catch {
    const box = $("networkInfo");
    if (box) box.textContent = "暂时无法读取局域网地址；本机仍可用 http://127.0.0.1:8766";
  }
}

function classLabel(c) {
  return `${c.grade}年级 ${c.class_no}班`;
}

async function loadClasses() {
  const data = await api("/api/classes");
  state.classes = data.classes;
  const select = $("attendanceClass");
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "__all__|__all__";
  all.textContent = `统一点名册（全部在读学生）`;
  select.appendChild(all);
  state.classes.forEach((c) => {
    const option = document.createElement("option");
    option.value = `${c.grade}|${c.class_no}`;
    option.textContent = `${classLabel(c)}（${c.student_count}人）`;
    select.appendChild(option);
  });
}

async function loadStudents() {
  if (!isAdmin()) return;
  const q = encodeURIComponent($("studentSearch")?.value || "");
  const month = $("studentMonth")?.value || currentMonth();
  const data = await api(`/api/students?q=${q}&month=${month}`);
  state.students = data.students;
  renderStudents();
  fillStudentSelects();
}

function fillStudentSelects() {
  const selects = [$("paymentStudent"), $("exportStudent")];
  selects.forEach((select) => {
    select.innerHTML = "";
    state.students.forEach((s) => {
      const option = document.createElement("option");
      option.value = s.permanent_id;
      option.textContent = `${s.name}｜${s.annual_id}｜${s.grade}-${s.class_no}`;
      select.appendChild(option);
    });
  });
}

function renderStudents() {
  const rows = state.students.map((s) => [
    s.permanent_id,
    s.annual_id,
    s.name,
    `${s.grade}年级 ${s.class_no}班`,
    s.status,
    Number(s.bed_fee_exempt) ? "免床位费" : "不免",
    `${s.lunch_present_days || 0}/${s.lunch_recorded_days || 0}`,
    `${s.care_present_days || 0}/${s.care_recorded_days || 0}`,
    money(s.opening_balance),
  ]);
  setTable($("studentsTable"), ["永久ID", "学年编号", "姓名", "班级", "状态", "床位费", "本月午餐到/记录", "本月晚托到/记录", "期初余额"], rows);
}

async function seedImport() {
  const result = await api("/api/seed/import-students", {
    method: "POST",
    body: JSON.stringify({}),
  });
  toast(`导入完成：新增 ${result.result.inserted}，更新 ${result.result.updated}`);
  await loadStudents();
  await loadClasses();
  await refreshDashboard();
}

async function refreshDashboard() {
  if (!isAdmin()) return;
  const month = $("homeMonth").value || currentMonth();
  const data = await api(`/api/dashboard?month=${month}`);
  const d = data.dashboard;
  renderCards($("dashboardCards"), [
    ["学生数", d.students_count],
    ["本月考勤记录", d.attendance_records],
    ["本月充值", money(d.payment_sum)],
    ["已月结人数", d.settled_count],
    ["本月应缴", money(d.total_due)],
    ["购物金额", money(d.shopping_total)],
    ["欠费人数", d.debt_count],
    ["欠费合计", money(d.debt_total)],
    ["未点名异常", d.unmarked_total],
  ]);
}

function setAttendanceField(pid, field, value, silent = false) {
  const rec = state.attendance.find((r) => r.permanent_id === pid);
  if (!rec || rec[field] === value) return;
  rec[field] = value;
  const row = document.querySelector(`[data-pid="${cssId(pid)}"]`);
  if (row) {
    row.querySelectorAll(`[data-field="${field}"]`).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.present === "1");
    });
  }
  if (silent) {
    state.changed.add(pid);
    state.dirty = true;
  } else {
    markAttendanceChanged(pid);
  }
}

function setAttendancePresence(pid, field, present) {
  const rec = state.attendance.find((r) => r.permanent_id === pid);
  if (!rec) return;
  const next = present ? "present" : "absent";
  if (rec[field] === next) return;
  rec[field] = next;
  document.querySelectorAll(`[data-pid="${cssId(pid)}"] [data-field="${field}"]`).forEach((btn) => {
    btn.classList.toggle("active", rec[field] === "present");
    btn.dataset.present = rec[field] === "present" ? "1" : "0";
  });
  markAttendanceChanged(pid);
}

function apButton(rec, field, label) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ap-btn";
  btn.textContent = label;
  btn.dataset.field = field;
  btn.dataset.present = rec[field] === "present" ? "1" : "0";
  btn.classList.toggle("active", rec[field] === "present");
  btn.onclick = () => setAttendancePresence(rec.permanent_id, field, rec[field] !== "present");
  return btn;
}

function refreshAttendanceRow(tr, rec) {
  const hasEvent = Boolean((rec.note || "").trim());
  rec.event_mark = hasEvent ? 1 : 0;
  tr.classList.toggle("has-event", hasEvent);
}

function fitTextInput(input, minCh = 1, maxCh = 16) {
  const text = input.value || input.placeholder || "";
  const ch = Math.min(maxCh, Math.max(minCh, Array.from(text).length + 1));
  input.style.width = `${ch}em`;
}

function purchaseEditor(rec, tr) {
  const wrap = document.createElement("div");
  wrap.className = "purchase-cell";
  const hasPurchase = Boolean((rec.shopping_item || "").trim() || Number(rec.shopping_amount || 0));
  const blank = document.createElement("button");
  blank.type = "button";
  blank.className = "purchase-blank";
  blank.setAttribute("aria-label", "添加购物");
  const editor = document.createElement("div");
  editor.className = hasPurchase ? "purchase-editor" : "purchase-editor hidden";
  blank.classList.toggle("hidden", hasPurchase);
  const item = document.createElement("input");
  item.placeholder = "物品";
  item.value = rec.shopping_item || "";
  item.className = "purchase-item";
  const amount = document.createElement("input");
  amount.placeholder = "金额";
  amount.type = "number";
  amount.step = "0.01";
  amount.value = Number(rec.shopping_amount || 0) ? String(rec.shopping_amount) : "";
  amount.className = "purchase-amount";
  fitTextInput(item, 2, 8);
  fitTextInput(amount, 2, 6);
  blank.onclick = () => {
    blank.classList.add("hidden");
    editor.classList.remove("hidden");
    item.focus();
  };
  const collapseIfEmpty = () => {
    setTimeout(() => {
      const hasFocusInside = editor.contains(document.activeElement);
      const hasValue = item.value.trim() || Number(amount.value || 0);
      if (!hasFocusInside && !hasValue) {
        editor.classList.add("hidden");
        blank.classList.remove("hidden");
        rec.shopping_item = "";
        rec.shopping_amount = 0;
        item.value = "";
        amount.value = "";
        fitTextInput(item, 2, 8);
        fitTextInput(amount, 2, 6);
        markAttendanceChanged(rec.permanent_id);
      }
    }, 80);
  };
  const sync = () => {
    rec.shopping_item = item.value.trim();
    rec.shopping_amount = Number(amount.value || 0);
    fitTextInput(item, 2, 8);
    fitTextInput(amount, 2, 6);
    markAttendanceChanged(rec.permanent_id);
    refreshAttendanceRow(tr, rec);
  };
  item.oninput = sync;
  amount.oninput = sync;
  item.onblur = collapseIfEmpty;
  amount.onblur = collapseIfEmpty;
  editor.append(item, amount);
  wrap.append(blank, editor);
  return wrap;
}

function renderAttendance() {
  const list = $("attendanceList");
  list.innerHTML = "";
  if (!state.attendance.length) {
    list.innerHTML = `<div class="notice">暂无点名学生。管理员需要先导入学生名单。</div>`;
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap attendance-table-wrap";
  const table = document.createElement("table");
  table.className = "attendance-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th class="col-name">学生姓名</th>
        <th class="col-event">事件</th>
        <th class="col-shopping">购物</th>
        <th class="col-ap">考勤</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement("tbody");
  state.attendance.forEach((rec) => {
    const tr = document.createElement("tr");
    tr.className = "att-row";
    tr.dataset.pid = rec.permanent_id;

    const nameTd = document.createElement("td");
    nameTd.className = "student-cell";
    const name = document.createElement("div");
    name.className = "student-name compact";
    name.textContent = `${rec.seq_in_class || ""}. ${rec.name}`;
    const code = document.createElement("div");
    code.className = "student-code";
    code.textContent = `${rec.annual_id}｜${rec.grade}-${rec.class_no}`;
    nameTd.append(name, code);

    const eventTd = document.createElement("td");
    eventTd.className = "event-cell";
    const eventInput = document.createElement("input");
    eventInput.className = "event-input";
    eventInput.value = rec.note || "";
    eventInput.placeholder = "";
    fitTextInput(eventInput, 1, 12);
    eventInput.oninput = () => {
      rec.note = eventInput.value;
      fitTextInput(eventInput, 1, 12);
      refreshAttendanceRow(tr, rec);
      markAttendanceChanged(rec.permanent_id);
    };
    eventInput.onblur = () => {
      rec.note = eventInput.value.trim();
      eventInput.value = rec.note;
      fitTextInput(eventInput, 1, 12);
      refreshAttendanceRow(tr, rec);
    };
    eventTd.appendChild(eventInput);

    const shoppingTd = document.createElement("td");
    shoppingTd.className = "shopping-cell";
    shoppingTd.appendChild(purchaseEditor(rec, tr));

    const apTd = document.createElement("td");
    apTd.className = "ap-cell";
    apTd.append(apButton(rec, "lunch_status", "A"), apButton(rec, "care_status", "P"));

    tr.append(nameTd, eventTd, shoppingTd, apTd);
    refreshAttendanceRow(tr, rec);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  list.appendChild(wrap);
  updateAttendanceSummary();
  updateAbsentReminder();
  setAttendanceView(state.attendanceView);
}

async function loadAttendance() {
  if (state.changed.size && !confirm("当前点名有未保存内容，确定要切换/重新载入吗？")) {
    return;
  }
  const value = $("attendanceClass").value;
  if (!value) {
    toast("暂无班级，请管理员先导入学生名单");
    return;
  }
  const [grade, classNo] = value.split("|");
  const dt = $("attendanceDate").value || currentDate();
  state.attendanceKey = selectedAttendanceKey();
  state.changed = new Set();
  state.dirty = false;
  state.lastSavedAt = "";
  $("attendanceStatus").textContent = "正在载入点名表……";
  const data = await api(`/api/attendance?date=${dt}&grade=${encodeURIComponent(grade)}&class_no=${encodeURIComponent(classNo)}`);
  state.attendance = data.records.map((r) => ({
    ...r,
    shopping_item: r.shopping_item || "",
    shopping_amount: Number(r.shopping_amount || 0),
    note: r.note || "",
    event_mark: Number(r.event_mark || 0),
  }));
  restoreAttendanceDraft();
  renderAttendance();
}

function markAll(field, value) {
  state.attendance.forEach((r) => {
    if (r[field] === "unmarked") setAttendanceField(r.permanent_id, field, value, true);
  });
  saveAttendanceDraftSoon();
  updateAttendanceSummary();
  updateAbsentReminder();
}

async function saveAttendance() {
  if (!state.attendance.length) {
    toast("没有可保存的点名记录");
    return;
  }
  if (state.saving) return;
  const dt = $("attendanceDate").value || currentDate();
  const records = state.attendance.map((r) => ({
    permanent_id: r.permanent_id,
    lunch_status: r.lunch_status === "present" ? "present" : "absent",
    care_status: r.care_status === "present" ? "present" : "absent",
    bed_status: r.bed_status,
    event_mark: (r.note || "").trim() ? 1 : Number(r.event_mark || 0),
    note: r.note || "",
    shopping_item: r.shopping_item || "",
    shopping_amount: Number(r.shopping_amount || 0),
  }));
  const btn = $("saveAttendance");
  state.saving = true;
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "保存中…";
  updateAttendanceSummary();
  try {
    const data = await api("/api/attendance/bulk", {
      method: "POST",
      body: JSON.stringify({ date: dt, records }),
      timeoutMs: 15000,
    });
    const savedAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    state.attendance.forEach((r) => {
      r.lunch_status = r.lunch_status === "present" ? "present" : "absent";
      r.care_status = r.care_status === "present" ? "present" : "absent";
      r.event_mark = (r.note || "").trim() ? 1 : Number(r.event_mark || 0);
      r.operator = operatorName();
      r.updated_at = savedAt;
    });
    state.changed = new Set();
    state.dirty = false;
    state.lastSavedAt = savedAt;
    clearAttendanceDraft();
    if (data.result.report_warning) {
      toast(`已保存 ${data.result.saved} 条考勤，但日报生成失败：${data.result.report_warning}`);
    } else {
      toast(`已保存 ${data.result.saved} 条考勤，已自动生成当日日报`);
      setReportHint(data.result.daily_report?.file_path);
    }
  } finally {
    state.saving = false;
    btn.disabled = false;
    btn.textContent = oldText;
    updateAttendanceSummary();
  }
}

async function savePayment() {
  const method = $("paymentMethod").value === "其他" ? $("paymentMethodOther").value.trim() : $("paymentMethod").value;
  const payload = {
    payment_date: $("paymentDate").value || currentDate(),
    permanent_id: $("paymentStudent").value,
    amount: $("paymentAmount").value,
    method,
    note: $("paymentNote").value,
  };
  if (!payload.method) {
    toast("请填写支付方式");
    return;
  }
  await api("/api/payments", { method: "POST", body: JSON.stringify(payload) });
  $("paymentAmount").value = "";
  $("paymentNote").value = "";
  toast("充值/退款已保存");
  await loadPayments();
  await refreshDashboard();
}

async function loadPayments() {
  if (!isAdmin()) return;
  const month = $("paymentsMonth").value || currentMonth();
  const data = await api(`/api/payments?month=${month}`);
  const rows = data.payments.map((p) => [
    p.payment_date,
    p.name,
    p.annual_id,
    money(p.amount),
    p.method,
    p.note,
    p.operator,
    p.created_at,
  ]);
  setTable($("paymentsTable"), ["日期", "学生", "学年编号", "金额", "方式", "备注", "操作人", "录入时间"], rows);
}

async function loadSettings() {
  if (!isAdmin()) return;
  const data = await api("/api/settings");
  $("lunchRate").value = data.settings.lunch_rate || "30";
  $("fullDayRate").value = data.settings.full_day_rate || "50";
  $("eveningOnlyRate").value = data.settings.evening_only_rate || data.settings.care_rate || "25";
  $("bedMonthlyFee").value = data.settings.bed_monthly_fee || data.settings.bed_daily_rate || "50";
}

async function saveSettings() {
  await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      lunch_rate: $("lunchRate").value,
      full_day_rate: $("fullDayRate").value,
      evening_only_rate: $("eveningOnlyRate").value,
      bed_monthly_fee: $("bedMonthlyFee").value,
    }),
  });
  toast("收费规则已保存");
}

async function generateSettlement() {
  const month = $("settlementMonth").value || currentMonth();
  const data = await api("/api/settlements/generate", {
    method: "POST",
    body: JSON.stringify({ month }),
  });
  if (data.result.report_warning) {
    toast(`月结完成：${data.result.generated} 人，但月报PDF生成失败`);
  } else {
    toast(`月结完成：${data.result.generated} 人，月报PDF已保存`);
    setReportHint(data.result.monthly_report?.file_path);
  }
  await loadSettlement();
  await refreshDashboard();
}

async function loadSettlement() {
  if (!isAdmin()) return;
  const month = $("settlementMonth").value || currentMonth();
  const data = await api(`/api/settlements?month=${month}`);
  const s = data.summary;
  renderCards($("settlementSummary"), [
    ["已月结人数", s.settled_count],
    ["本月应缴", money(s.total_due)],
    ["购物金额", money(s.shopping_total)],
    ["本月充值", money(s.recharge_total)],
    ["欠费人数", s.debt_count],
    ["欠费合计", money(s.debt_total)],
    ["有余额人数", s.positive_count],
    ["余额合计", money(s.positive_total)],
    ["未点名异常", s.unmarked_total],
  ]);
  const rows = data.rows.map((r) => [
    r.name,
    r.annual_id,
    `${r.grade}-${r.class_no}`,
    money(r.opening_balance),
    money(r.recharge_amount),
    `${r.lunch_days} × ${money(r.lunch_rate)} = ${money(r.lunch_fee)}`,
    `${r.full_day_days || 0} × ${money(r.full_day_rate || 50)} = ${money(r.full_day_fee || 0)}`,
    `${r.evening_only_days || r.care_days || 0} × ${money(r.evening_only_rate || r.care_rate || 25)} = ${money(r.evening_only_fee || r.care_fee || 0)}`,
    `${r.bed_days}天使用｜${Number(r.bed_fee_exempt) ? "免" : money(r.bed_monthly_fee || r.bed_rate)} = ${money(r.bed_fee)}`,
    money(r.shopping_fee),
    money(r.total_due),
    `<span class="${Number(r.ending_balance) < 0 ? "debt" : "positive"}">${money(r.ending_balance)}</span>`,
    r.balance_status,
    r.unmarked_count,
  ]);
  setTable($("settlementTable"), ["姓名", "学年编号", "班级", "期初", "充值", "单独午餐", "全天托管", "单独晚托", "床位", "购物", "应缴", "期末", "状态", "未点名"], rows);
}

async function loadUsers() {
  if (!isAdmin()) return;
  const data = await api("/api/users");
  state.users = data.users;
  const rows = state.users.map((u) => {
    const edit = document.createElement("button");
    edit.textContent = "编辑";
    edit.onclick = () => fillAccountForm(u);
    return [
      u.username,
      u.display_name,
      u.role === "admin" ? "管理员" : "老师",
      u.active ? "启用" : "停用",
      u.updated_at,
      edit,
    ];
  });
  setTable($("accountsTable"), ["用户名", "显示姓名", "角色", "状态", "更新时间", "操作"], rows);
}

function costTypeLabel(value) {
  return { fixed: "固定成本", variable: "变动成本", labor: "人工成本", other: "其他成本" }[value] || value;
}

function costItemOptions(type) {
  return {
    fixed: ["房租", "水电", "其他"],
    variable: ["菜", "肉", "水果", "牛奶", "其他"],
    labor: ["人工", "其他"],
    other: ["其他"],
  }[type] || ["其他"];
}

function toggleCostItemOther() {
  const wrap = $("costItemOtherWrap");
  if (!wrap) return;
  wrap.classList.toggle("hidden", $("costItem").value !== "其他");
}

function syncCostItemOptions() {
  const select = $("costItem");
  if (!select) return;
  const oldValue = select.value;
  const items = costItemOptions($("costType").value);
  select.innerHTML = "";
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item;
    option.textContent = item;
    select.appendChild(option);
  });
  if (items.includes(oldValue)) select.value = oldValue;
  toggleCostItemOther();
}

async function saveCost() {
  const item = $("costItem").value === "其他" ? $("costItemOther").value.trim() : $("costItem").value;
  const payload = {
    cost_date: $("costDate").value || currentDate(),
    cost_type: $("costType").value,
    item,
    amount: $("costAmount").value,
    note: $("costNote").value,
  };
  if (!payload.item) return toast("请填写成本项目");
  await api("/api/costs", { method: "POST", body: JSON.stringify(payload) });
  $("costAmount").value = "";
  $("costNote").value = "";
  toast("成本已保存");
  await loadCosts();
}

async function loadCosts() {
  if (!isAdmin()) return;
  const month = $("costMonth").value || currentMonth();
  const data = await api(`/api/costs?month=${month}`);
  const s = data.summary;
  renderCards($("costSummary"), [
    ["固定成本", money(s.fixed_total)],
    ["变动成本", money(s.variable_total)],
    ["人工成本", money(s.labor_total)],
    ["其他成本", money(s.other_total)],
    ["成本合计", money(s.grand_total)],
    ["记录条数", s.record_count],
  ]);
  const rows = data.rows.map((r) => [
    r.cost_date,
    r.cost_type_label || costTypeLabel(r.cost_type),
    r.item,
    money(r.amount),
    r.note,
    r.operator,
    r.created_at,
  ]);
  setTable($("costsTable"), ["日期", "类型", "项目", "金额", "备注", "登记人", "登记时间"], rows);
}

function fillAccountForm(u) {
  $("accountUsername").value = u.username;
  $("accountDisplayName").value = u.display_name;
  $("accountRole").value = u.role;
  $("accountActive").value = String(u.active ? 1 : 0);
  $("accountPin").value = "";
  toast("已填入账户，修改 PIN 时再输入新 PIN");
}

async function saveAccount() {
  const payload = {
    username: $("accountUsername").value.trim(),
    display_name: $("accountDisplayName").value.trim(),
    role: $("accountRole").value,
    pin: $("accountPin").value,
    active: $("accountActive").value === "1",
  };
  await api("/api/users", { method: "POST", body: JSON.stringify(payload) });
  $("accountPin").value = "";
  toast("账户已保存");
  await loadUsers();
}

function download(url) {
  window.location.href = url;
}

function setReportHint(path) {
  const hint = $("reportPathHint");
  if (hint && path) hint.textContent = `已保存到本地：${path}`;
}

async function generateDailyReport(downloadFile = false) {
  const date = $("dailyReportDate")?.value || $("attendanceDate").value || currentDate();
  const data = await api("/api/reports/daily", {
    method: "POST",
    body: JSON.stringify({ date }),
  });
  setReportHint(data.report.file_path);
  toast("每日考勤 PDF 已生成");
  if (downloadFile) download(`/api/reports/daily?date=${date}&download=1`);
}

async function generateMonthlyPdf(downloadFile = true) {
  const month = $("exportMonth").value || currentMonth();
  const data = await api("/api/reports/monthly", {
    method: "POST",
    body: JSON.stringify({ month }),
    timeoutMs: 20000,
  });
  setReportHint(data.report.file_path);
  toast("月报 PDF 已生成");
  if (downloadFile) download(`/api/reports/monthly?month=${month}&download=1`);
}

async function generateCostPdf(downloadFile = true) {
  const month = $("costMonth").value || currentMonth();
  const data = await api("/api/reports/costs", {
    method: "POST",
    body: JSON.stringify({ month }),
    timeoutMs: 20000,
  });
  setReportHint(data.report.file_path);
  toast("成本 PDF 已生成");
  if (downloadFile) download(`/api/reports/costs?month=${month}&download=1`);
}

async function generateStudentPdf(downloadFile = true) {
  const month = $("exportMonth").value || currentMonth();
  const pid = $("exportStudent").value;
  if (!pid) return toast("请选择学生");
  const data = await api("/api/reports/student", {
    method: "POST",
    body: JSON.stringify({ month, permanent_id: pid }),
    timeoutMs: 20000,
  });
  setReportHint(data.report.file_path);
  toast("学生出勤 PDF 已生成");
  if (downloadFile) download(`/api/reports/student?month=${month}&permanent_id=${encodeURIComponent(pid)}&download=1`);
}

function bindTabs() {
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.adminOnly === "1" && !isAdmin()) {
        toast("该页面仅管理员可访问");
        return;
      }
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activateTab(btn.dataset.tab);
    };
  });
}

function bindEvents() {
  $("loginBtn").onclick = () => login().catch((e) => toast(e.message));
  $("loginPin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") login().catch((err) => toast(err.message));
  });
  $("logoutBtn").onclick = () => logout().catch((e) => toast(e.message));
  $("seedImport").onclick = () => seedImport().catch((e) => toast(e.message));
  $("refreshDashboard").onclick = () => refreshDashboard().catch((e) => toast(e.message));
  $("loadStudents").onclick = () => loadStudents().catch((e) => toast(e.message));
  $("attendanceDate").onchange = () => loadAttendance().catch((e) => toast(e.message));
  $("attendanceClass").onchange = () => loadAttendance().catch((e) => toast(e.message));
  $("showAttendanceRoster").onclick = () => setAttendanceView("roster");
  $("showAbsentReminder").onclick = () => setAttendanceView("absent");
  $("saveAttendance").onclick = () => saveAttendance().catch((e) => toast(e.message));
  $("savePayment").onclick = () => savePayment().catch((e) => toast(e.message));
  $("loadPayments").onclick = () => loadPayments().catch((e) => toast(e.message));
  $("saveSettings").onclick = () => saveSettings().catch((e) => toast(e.message));
  $("generateSettlement").onclick = () => generateSettlement().catch((e) => toast(e.message));
  $("settlementMonth").onchange = () => loadSettlement().catch((e) => toast(e.message));
  $("paymentMethod").onchange = () => {
    $("paymentMethodOtherWrap").classList.toggle("hidden", $("paymentMethod").value !== "其他");
  };
  $("costType").onchange = syncCostItemOptions;
  $("costItem").onchange = toggleCostItemOther;
  $("saveCost").onclick = () => saveCost().catch((e) => toast(e.message));
  $("loadCosts").onclick = () => loadCosts().catch((e) => toast(e.message));
  $("exportCostPdf").onclick = () => generateCostPdf(true).catch((e) => toast(e.message));
  $("saveAccount").onclick = () => saveAccount().catch((e) => toast(e.message));
  $("generateDailyReport").onclick = () => generateDailyReport(false).catch((e) => toast(e.message));
  $("exportDailyPdf").onclick = () => generateDailyReport(true).catch((e) => toast(e.message));
  $("exportMonthlyPdf").onclick = () => generateMonthlyPdf(true).catch((e) => toast(e.message));
  $("exportStudentPdfBtn").onclick = () => generateStudentPdf(true).catch((e) => toast(e.message));
  $("exportMonthly").onclick = () => {
    const month = $("exportMonth").value || currentMonth();
    download(`/api/export/monthly?month=${month}`);
  };
  $("exportStudentBtn").onclick = () => {
    const month = $("exportMonth").value || currentMonth();
    const pid = $("exportStudent").value;
    if (!pid) return toast("请选择学生");
    download(`/api/export/student?month=${month}&permanent_id=${encodeURIComponent(pid)}`);
  };
  window.addEventListener("beforeunload", (e) => {
    if (!state.changed.size) return;
    e.preventDefault();
    e.returnValue = "当前点名有未保存内容";
  });
  window.addEventListener("online", () => toast("网络已恢复，可以保存考勤"));
  window.addEventListener("offline", () => toast("当前网络断开，点名会暂存在本机"));
}

async function init() {
  bindTabs();
  bindEvents();
  ["homeMonth", "studentMonth", "paymentsMonth", "settlementMonth", "exportMonth", "costMonth"].forEach((id) => ($(id).value = currentMonth()));
  $("attendanceDate").value = currentDate();
  $("paymentDate").value = currentDate();
  $("dailyReportDate").value = currentDate();
  $("costDate").value = currentDate();
  syncCostItemOptions();
  await loadMe();
}

init().catch((e) => {
  showLogin();
  toast(e.message);
});
