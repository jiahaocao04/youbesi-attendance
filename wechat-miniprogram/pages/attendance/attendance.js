const { request } = require("../../utils/api");
const app = getApp();

function today() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function textWidthRpx(value, min = 48, max = 220) {
  const len = Array.from(String(value || "")).length;
  if (!len) return min;
  return Math.min(max, Math.max(min, len * 28 + 28));
}

function normalizeRecord(r) {
  const item = r.shopping_item || "";
  const amountText = Number(r.shopping_amount || 0) ? String(r.shopping_amount) : "";
  return {
    ...r,
    lunch_status: r.lunch_status === "present" ? "present" : "absent",
    care_status: r.care_status === "present" ? "present" : "absent",
    note: r.note || "",
    eventWidth: textWidthRpx(r.note || "", 48, 220),
    shopping_item: item,
    shopping_amount: Number(r.shopping_amount || 0),
    shopping_amount_text: amountText,
    shoppingItemWidth: textWidthRpx(item, 54, 150),
    shoppingAmountWidth: textWidthRpx(amountText, 54, 110),
    shoppingOpen: Boolean(r.shopping_item || Number(r.shopping_amount || 0))
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
  return {
    permanent_id: r.permanent_id,
    lunch_status: r.lunch_status === "present" ? "present" : "absent",
    care_status: r.care_status === "present" ? "present" : "absent",
    bed_status: r.bed_status || "not_used",
    event_mark: r.note ? 1 : 0,
    note: String(r.note || "").trim(),
    shopping_item: String(r.shopping_item || "").trim(),
    shopping_amount: Number(r.shopping_amount || 0)
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
    records: [],
    summary: { a: 0, p: 0, fullDay: 0, absent: 0 },
    absentRecords: [],
    viewMode: "roster",
    loading: false,
    saving: false,
    autoSaveText: ""
  },
  onLoad() {
    this.autoSaveTimers = {};
    this.dirtyPids = new Set();
    this.savingPids = new Set();
    this.autoSaveSeq = 0;
    if (!app.globalData.user && !wx.getStorageSync("user")) {
      wx.redirectTo({ url: "/pages/login/login" });
      return;
    }
    this.loadAttendance();
  },
  onHide() {
    this.flushAutoSaves();
  },
  onUnload() {
    this.flushAutoSaves();
  },
  async loadAttendance() {
    this.setData({ loading: true });
    try {
      const data = await request(`/api/attendance?date=${this.data.date}&grade=__all__&class_no=__all__`);
      const records = (data.records || []).map(normalizeRecord);
      this.setData({ records, autoSaveText: "" }, () => this.refreshSummary());
    } catch (err) {
      wx.showToast({ title: err.message || "载入失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },
  onDateChange(e) {
    this.flushAutoSaves();
    this.autoSaveTimers = {};
    this.dirtyPids = new Set();
    this.setData({ date: e.detail.value, viewMode: "roster", autoSaveText: "" }, () => this.loadAttendance());
  },
  refreshSummary() {
    const summary = summarize(this.data.records);
    const absentRecords = this.data.records.filter((r) => r.lunch_status !== "present" || r.care_status !== "present");
    this.setData({ summary, absentRecords });
  },
  showRoster() {
    this.setData({ viewMode: "roster" });
  },
  showAbsent() {
    this.setData({ viewMode: "absent" });
  },
  toggleAP(e) {
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
    const records = this.data.records.slice();
    records[index] = { ...records[index], note: e.detail.value, eventWidth: textWidthRpx(e.detail.value, 48, 220) };
    this.setData({ records }, () => {
      this.refreshSummary();
      this.queueAutoSave(index, 900);
    });
  },
  onEventBlur(e) {
    const index = Number(e.currentTarget.dataset.index);
    const note = String(e.detail.value || "").trim();
    const records = this.data.records.slice();
    records[index] = { ...records[index], note, eventWidth: textWidthRpx(note, 48, 220) };
    this.setData({ records }, () => {
      this.refreshSummary();
      this.queueAutoSave(index, 120);
    });
  },
  openShopping(e) {
    const index = Number(e.currentTarget.dataset.index);
    const records = this.data.records.slice();
    records[index] = { ...records[index], shoppingOpen: true };
    this.setData({ records });
  },
  onShoppingItem(e) {
    const index = Number(e.currentTarget.dataset.index);
    const records = this.data.records.slice();
    records[index] = { ...records[index], shopping_item: e.detail.value, shoppingItemWidth: textWidthRpx(e.detail.value, 54, 150) };
    this.setData({ records }, () => this.queueAutoSave(index, 900));
  },
  onShoppingAmount(e) {
    const index = Number(e.currentTarget.dataset.index);
    const records = this.data.records.slice();
    records[index] = {
      ...records[index],
      shopping_amount_text: e.detail.value,
      shopping_amount: Number(e.detail.value || 0),
      shoppingAmountWidth: textWidthRpx(e.detail.value, 54, 110)
    };
    this.setData({ records }, () => this.queueAutoSave(index, 900));
  },
  onShoppingBlur(e) {
    const index = Number(e.currentTarget.dataset.index);
    setTimeout(() => {
      const records = this.data.records.slice();
      const rec = { ...records[index] };
      const hasValue = String(rec.shopping_item || "").trim() || Number(rec.shopping_amount || 0);
      if (!hasValue) {
        records[index] = {
          ...rec,
          shoppingOpen: false,
          shopping_item: "",
          shopping_amount: 0,
          shopping_amount_text: "",
          shoppingItemWidth: textWidthRpx("", 54, 150),
          shoppingAmountWidth: textWidthRpx("", 54, 110)
        };
        this.setData({ records }, () => this.queueAutoSave(index, 120));
      } else {
        this.queueAutoSave(index, 120);
      }
    }, 120);
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
    this.setData({ autoSaveText: "自动保存中..." });
    try {
      await request("/api/attendance/bulk", {
        method: "POST",
        data: { date: this.data.date, records: [payload], generate_report: false },
        timeout: 10000
      });
      if (seq === this.autoSaveSeq) {
        this.setData({ autoSaveText: "已自动保存" });
      }
    } catch (err) {
      this.dirtyPids.add(pid);
      if (retry < 2) {
        this.setData({ autoSaveText: "自动保存重试中..." });
        this.queueAutoSave(index, 1600, retry + 1);
      } else {
        this.setData({ autoSaveText: "自动保存失败" });
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
    this.flushAutoSaves();
    const notReady = this.data.records.some((r) => !recordReadyForAutoSave(r));
    if (notReady) {
      wx.showToast({ title: "请补全购物物品和金额", icon: "none" });
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
      wx.showToast({ title: "已保存", icon: "success" });
      this.loadAttendance();
    } catch (err) {
      wx.showToast({ title: err.message || "保存失败", icon: "none" });
    } finally {
      this.setData({ saving: false });
    }
  }
});
