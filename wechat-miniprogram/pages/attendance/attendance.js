const { request } = require("../../utils/api");
const app = getApp();

function today() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function textWidthRpx(value, min = 54, max = 220) {
  const len = Array.from(String(value || "").trim()).length;
  if (!len) return min;
  return Math.min(max, Math.max(min, len * 26 + 34));
}

function formatClassLabel(grade, classNo) {
  if (grade === "__all__") return "全部";
  if (!classNo || classNo === "0") return `${grade}年级`;
  return `${grade}.${classNo}班`;
}

function classSearchText(item) {
  if (!item) return "";
  const grade = String(item.grade || "");
  const classNo = String(item.class_no || "");
  return [
    item.label,
    `${grade}.${classNo}`,
    `${grade}-${classNo}`,
    `${grade}年级${classNo}班`,
    `${grade}年${classNo}班`
  ].join(" ").toLowerCase();
}

function filterClasses(classes, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return classes;
  return classes.filter((item) => classSearchText(item).includes(q));
}

function normalizeRecord(r) {
  const item = String(r.shopping_item || "").trim();
  const amount = Number(r.shopping_amount || 0);
  const amountText = amount ? String(amount) : "";
  return {
    ...r,
    lunch_status: r.lunch_status === "present" ? "present" : "absent",
    care_status: r.care_status === "present" ? "present" : "absent",
    bed_status: r.bed_status || "not_used",
    note: String(r.note || ""),
    eventWidth: textWidthRpx(r.note || "", 54, 210),
    shopping_item: item,
    shopping_amount: amount,
    shopping_amount_text: amountText,
    shoppingItemWidth: textWidthRpx(item, 58, 150),
    shoppingAmountWidth: textWidthRpx(amountText, 58, 108),
    shoppingOpen: false,
    _shoppingPersisted: Boolean(item || amount)
  };
}

function summarize(records) {
  const summary = { a: 0, p: 0, fullDay: 0, absent: 0 };
  records.forEach((r) => {
    const a = r.lunch_status === "present";
    const p = r.care_status === "present";
    if (a) summary.a += 1;
    if (p) summary.p += 1;
    if (a && p) summary.fullDay += 1;
    if (!a && !p) summary.absent += 1;
  });
  return summary;
}

function buildRecordPayload(r) {
  const item = String(r.shopping_item || "").trim();
  const amount = Number(r.shopping_amount || 0);
  return {
    permanent_id: r.permanent_id,
    lunch_status: r.lunch_status === "present" ? "present" : "absent",
    care_status: r.care_status === "present" ? "present" : "absent",
    bed_status: r.bed_status || "not_used",
    event_mark: String(r.note || "").trim() ? 1 : 0,
    note: String(r.note || "").trim(),
    shopping_item: item,
    shopping_amount: amount
  };
}

function recordReadyForAutoSave(r) {
  const item = String(r.shopping_item || "").trim();
  const amountText = String(r.shopping_amount_text || "").trim();
  const amount = Number(r.shopping_amount || 0);
  if (item && amount <= 0) return false;
  if ((amount > 0 || amountText) && !item) return false;
  return true;
}

Page({
  data: {
    date: today(),
    classes: [{ key: "__all__|__all__", grade: "__all__", class_no: "__all__", label: "全部", count: 0 }],
    classPickerRange: ["全部"],
    classSearchText: "全部",
    classSearchOpen: false,
    filteredClasses: [{ key: "__all__|__all__", grade: "__all__", class_no: "__all__", label: "全部", count: 0 }],
    totalStudentCount: 0,
    selectedClassIndex: 0,
    selectedClassKey: "__all__|__all__",
    selectedGrade: "__all__",
    selectedClassNo: "__all__",
    selectedClassLabel: "全部",
    records: [],
    summary: { a: 0, p: 0, fullDay: 0, absent: 0 },
    absentRecords: [],
    viewMode: "roster",
    isAdmin: false,
    loading: false,
    saving: false,
    autoSaveText: ""
  },

  onLoad() {
    this.autoSaveTimers = {};
    this.dirtyPids = new Set();
    this.savingPids = new Set();
    this.autoSaveSeq = 0;
    const user = app.globalData.user || wx.getStorageSync("user");
    if (!user) {
      wx.redirectTo({ url: "/pages/login/login" });
      return;
    }
    this.setData({ isAdmin: user.role === "admin" });
    this.loadClasses();
    this.loadAttendance();
  },

  onHide() {
    this.cleanupEmptyShoppingEditors(false);
    this.flushAutoSaves();
  },

  onUnload() {
    this.cleanupEmptyShoppingEditors(false);
    this.flushAutoSaves();
  },

  async loadClasses() {
    try {
      const data = await request("/api/classes");
      const raw = (data.classes || []).filter((item) => Number(item.student_count || 0) > 0);
      const total = raw.reduce((sum, item) => sum + Number(item.student_count || 0), 0);
      const classes = [
        { key: "__all__|__all__", grade: "__all__", class_no: "__all__", label: "全部", count: total },
        ...raw.map((item) => {
          const grade = String(item.grade || "");
          const classNo = String(item.class_no || "");
          return {
            key: `${grade}|${classNo}`,
            grade,
            class_no: classNo,
            label: formatClassLabel(grade, classNo),
            count: Number(item.student_count || 0)
          };
        })
      ];
      const selectedClassIndex = Math.max(0, classes.findIndex((item) => item.key === this.data.selectedClassKey));
      const selected = classes[selectedClassIndex] || classes[0];
      this.setData({
        classes,
        classPickerRange: classes.map((item) => `${item.label}（${item.count}）`),
        filteredClasses: filterClasses(classes, this.data.classSearchText === this.data.selectedClassLabel ? "" : this.data.classSearchText),
        selectedClassIndex,
        totalStudentCount: total,
        classSearchText: selected.label
      });
    } catch (err) {
      wx.showToast({ title: err.message || "班级载入失败", icon: "none" });
    }
  },

  async loadAttendance() {
    this.setData({ loading: true });
    const grade = encodeURIComponent(this.data.selectedGrade || "__all__");
    const classNo = encodeURIComponent(this.data.selectedClassNo || "__all__");
    try {
      const data = await request(`/api/attendance?date=${this.data.date}&grade=${grade}&class_no=${classNo}`);
      const records = (data.records || []).map(normalizeRecord);
      this.setData({ records, viewMode: "roster", autoSaveText: "" }, () => this.refreshSummary());
    } catch (err) {
      wx.showToast({ title: err.message || "载入失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onDateChange(e) {
    this.cleanupEmptyShoppingEditors(false);
    this.flushAutoSaves();
    this.autoSaveTimers = {};
    this.dirtyPids = new Set();
    this.setData({ date: e.detail.value, viewMode: "roster", autoSaveText: "" }, () => this.loadAttendance());
  },

  onClassTap(e) {
    const key = e.currentTarget.dataset.key;
    if (!key || key === this.data.selectedClassKey) return;
    const index = this.data.classes.findIndex((item) => item.key === key);
    const selected = this.data.classes[index];
    if (!selected) return;
    this.switchClass(selected, index);
  },

  onClassPickerChange(e) {
    const index = Number(e.detail.value || 0);
    const selected = this.data.classes[index];
    if (!selected) return;
    this.switchClass(selected, index);
  },

  onClassSearchFocus() {
    this.setData({
      classSearchOpen: true,
      classSearchText: this.data.selectedClassLabel === "全部" ? "" : this.data.selectedClassLabel,
      filteredClasses: filterClasses(this.data.classes, "")
    });
  },

  onClassSearchInput(e) {
    const value = e.detail.value;
    this.setData({
      classSearchText: value,
      classSearchOpen: true,
      filteredClasses: filterClasses(this.data.classes, value)
    });
  },

  selectClassFromSearch(e) {
    const key = e.currentTarget.dataset.key;
    const index = this.data.classes.findIndex((item) => item.key === key);
    const selected = this.data.classes[index];
    if (!selected) return;
    this.switchClass(selected, index);
  },

  closeClassSearch() {
    this.setData({
      classSearchOpen: false,
      classSearchText: this.data.selectedClassLabel
    });
  },

  switchClass(selected, index) {
    if (!selected) return;
    const sameClass = selected.key === this.data.selectedClassKey;
    if (sameClass) {
      this.setData({
        selectedClassIndex: index,
        classSearchOpen: false,
        classSearchText: selected.label
      });
      return;
    }
    this.cleanupEmptyShoppingEditors(false);
    this.flushAutoSaves();
    this.autoSaveTimers = {};
    this.dirtyPids = new Set();
    this.setData({
      selectedClassIndex: index,
      selectedClassKey: selected.key,
      selectedGrade: selected.grade,
      selectedClassNo: selected.class_no,
      selectedClassLabel: selected.label,
      classSearchText: selected.label,
      filteredClasses: filterClasses(this.data.classes, ""),
      classSearchOpen: false,
      autoSaveText: "",
    }, () => this.loadAttendance());
  },

  refreshSummary() {
    const summary = summarize(this.data.records);
    const absentRecords = this.data.records.filter((r) => r.lunch_status !== "present" || r.care_status !== "present");
    this.setData({ summary, absentRecords });
  },

  showRoster() {
    this.cleanupEmptyShoppingEditors(false);
    this.setData({ viewMode: "roster", classSearchOpen: false });
  },

  showAbsent() {
    this.cleanupEmptyShoppingEditors(false);
    this.setData({ viewMode: "absent", classSearchOpen: false });
  },

  goAdmin() {
    wx.navigateTo({ url: "/pages/admin/admin" });
  },

  toggleAP(e) {
    this.cleanupEmptyShoppingEditors(false);
    const index = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field;
    const records = this.data.records.slice();
    const rec = { ...records[index] };
    rec[field] = rec[field] === "present" ? "absent" : "present";
    records[index] = rec;
    this.setData({ records }, () => {
      this.refreshSummary();
      this.queueAutoSave(index, 120);
    });
  },

  onEventInput(e) {
    const index = Number(e.currentTarget.dataset.index);
    const value = e.detail.value;
    const records = this.data.records.slice();
    records[index] = { ...records[index], note: value, eventWidth: textWidthRpx(value, 54, 210) };
    this.setData({ records }, () => {
      this.refreshSummary();
      this.queueAutoSave(index, 900);
    });
  },

  onEventFocus() {
    this.cleanupEmptyShoppingEditors(false);
  },

  onEventBlur(e) {
    const index = Number(e.currentTarget.dataset.index);
    const note = String(e.detail.value || "").trim();
    const records = this.data.records.slice();
    records[index] = { ...records[index], note, eventWidth: textWidthRpx(note, 54, 210) };
    this.setData({ records }, () => {
      this.refreshSummary();
      this.queueAutoSave(index, 120);
    });
  },

  openShopping(e) {
    this.cleanupEmptyShoppingEditors(false);
    const index = Number(e.currentTarget.dataset.index);
    const records = this.data.records.slice();
    records[index] = { ...records[index], shoppingOpen: true };
    this.setData({ records });
  },

  onShoppingItem(e) {
    const index = Number(e.currentTarget.dataset.index);
    const value = e.detail.value;
    const records = this.data.records.slice();
    records[index] = { ...records[index], shopping_item: value, shoppingItemWidth: textWidthRpx(value, 58, 150) };
    this.setData({ records }, () => this.queueAutoSave(index, 900));
  },

  onShoppingAmount(e) {
    const index = Number(e.currentTarget.dataset.index);
    const value = e.detail.value;
    const records = this.data.records.slice();
    records[index] = {
      ...records[index],
      shopping_amount_text: value,
      shopping_amount: Number(value || 0),
      shoppingAmountWidth: textWidthRpx(value, 58, 108)
    };
    this.setData({ records }, () => this.queueAutoSave(index, 900));
  },

  onShoppingBlur(e) {
    const index = Number(e.currentTarget.dataset.index);
    setTimeout(() => this.cleanupOneShoppingEditor(index, true), 120);
  },

  cleanupOneShoppingEditor(index, saveIfNeeded) {
    const records = this.data.records.slice();
    const rec = records[index];
    if (!rec) return;
    const item = String(rec.shopping_item || "").trim();
    const amount = Number(rec.shopping_amount || 0);
    const hasValue = Boolean(item || amount);
    if (hasValue) {
      records[index] = {
        ...rec,
        shoppingOpen: true,
        shopping_item: item,
        shopping_amount: amount,
        shopping_amount_text: amount ? String(amount) : "",
        shoppingItemWidth: textWidthRpx(item, 58, 150),
        shoppingAmountWidth: textWidthRpx(amount ? String(amount) : "", 58, 108)
      };
      this.setData({ records }, () => {
        if (saveIfNeeded) this.queueAutoSave(index, 120);
      });
      return;
    }

    const shouldSaveDelete = Boolean(rec._shoppingPersisted) || (this.dirtyPids && this.dirtyPids.has(rec.permanent_id));
    records[index] = {
      ...rec,
      shoppingOpen: false,
      shopping_item: "",
      shopping_amount: 0,
      shopping_amount_text: "",
      shoppingItemWidth: textWidthRpx("", 58, 150),
      shoppingAmountWidth: textWidthRpx("", 58, 108)
    };
    this.setData({ records }, () => {
      if (saveIfNeeded && shouldSaveDelete) this.queueAutoSave(index, 120);
    });
  },

  cleanupEmptyShoppingEditors(saveIfNeeded) {
    const records = this.data.records.slice();
    const saveIndexes = [];
    let changed = false;
    records.forEach((rec, index) => {
      if (!rec || !rec.shoppingOpen) return;
      const item = String(rec.shopping_item || "").trim();
      const amount = Number(rec.shopping_amount || 0);
      if (item || amount) return;
      const shouldSaveDelete = Boolean(rec._shoppingPersisted) || (this.dirtyPids && this.dirtyPids.has(rec.permanent_id));
      records[index] = {
        ...rec,
        shoppingOpen: false,
        shopping_item: "",
        shopping_amount: 0,
        shopping_amount_text: "",
        shoppingItemWidth: textWidthRpx("", 58, 150),
        shoppingAmountWidth: textWidthRpx("", 58, 108)
      };
      changed = true;
      if (saveIfNeeded && shouldSaveDelete) saveIndexes.push(index);
    });
    if (!changed) return;
    this.setData({ records }, () => {
      saveIndexes.forEach((index) => this.queueAutoSave(index, 120));
    });
  },

  queueAutoSave(index, delay = 600, retry = 0) {
    const rec = this.data.records[index];
    if (!rec || !rec.permanent_id) return;
    if (!recordReadyForAutoSave(rec)) {
      this.setData({ autoSaveText: "购物待补全" });
      return;
    }
    const pid = rec.permanent_id;
    this.dirtyPids.add(pid);
    if (this.autoSaveTimers[pid]) {
      clearTimeout(this.autoSaveTimers[pid]);
    }
    this.autoSaveTimers[pid] = setTimeout(() => {
      delete this.autoSaveTimers[pid];
      const currentIndex = this.data.records.findIndex((item) => item.permanent_id === pid);
      this.saveOneRecord(currentIndex, retry);
    }, delay);
  },

  flushAutoSaves() {
    const pids = Array.from(this.dirtyPids || []);
    pids.forEach((pid) => {
      if (this.autoSaveTimers && this.autoSaveTimers[pid]) {
        clearTimeout(this.autoSaveTimers[pid]);
        delete this.autoSaveTimers[pid];
      }
      const index = this.data.records.findIndex((item) => item.permanent_id === pid);
      if (index >= 0) this.saveOneRecord(index, 0);
    });
  },

  async saveOneRecord(index, retry = 0) {
    const rec = this.data.records[index];
    if (!rec || !recordReadyForAutoSave(rec)) return;
    const pid = rec.permanent_id;
    if (this.savingPids.has(pid)) {
      this.dirtyPids.add(pid);
      return;
    }
    const payload = buildRecordPayload(rec);
    const seq = ++this.autoSaveSeq;
    this.dirtyPids.delete(pid);
    this.savingPids.add(pid);
    this.setData({ autoSaveText: "自动保存中" });
    try {
      await request("/api/attendance/bulk", {
        method: "POST",
        data: { date: this.data.date, records: [payload], generate_report: false },
        timeout: 10000
      });
      const latestIndex = this.data.records.findIndex((item) => item.permanent_id === pid);
      if (latestIndex >= 0) {
        const records = this.data.records.slice();
        records[latestIndex] = {
          ...records[latestIndex],
          _shoppingPersisted: Boolean(payload.shopping_item || Number(payload.shopping_amount || 0))
        };
        this.setData({ records });
      }
      if (seq === this.autoSaveSeq) {
        this.setData({ autoSaveText: "已保存" });
      }
    } catch (err) {
      this.dirtyPids.add(pid);
      if (retry < 2) {
        this.setData({ autoSaveText: "保存重试中" });
        this.queueAutoSave(index, 1600, retry + 1);
      } else {
        this.setData({ autoSaveText: "保存失败" });
        wx.showToast({ title: err.message || "自动保存失败", icon: "none" });
      }
    } finally {
      this.savingPids.delete(pid);
      if (this.dirtyPids.has(pid)) {
        const latestIndex = this.data.records.findIndex((item) => item.permanent_id === pid);
        if (latestIndex >= 0) this.queueAutoSave(latestIndex, 180);
      }
    }
  },

  async saveAttendance() {
    this.cleanupEmptyShoppingEditors(true);
    this.flushAutoSaves();
    const notReady = this.data.records.some((r) => !recordReadyForAutoSave(r));
    if (notReady) {
      wx.showToast({ title: "请补全购物", icon: "none" });
      return;
    }
    this.setData({ saving: true });
    try {
      const records = this.data.records.map(buildRecordPayload);
      await request("/api/attendance/bulk", {
        method: "POST",
        data: { date: this.data.date, records, generate_report: true },
        timeout: 15000
      });
      wx.showToast({ title: "日报已生成", icon: "success" });
      this.loadAttendance();
    } catch (err) {
      wx.showToast({ title: err.message || "生成失败", icon: "none" });
    } finally {
      this.setData({ saving: false });
    }
  }
});
