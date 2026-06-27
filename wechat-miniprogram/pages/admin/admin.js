const { request } = require("../../utils/api");
const app = getApp();

function today() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function currentMonth() {
  return today().slice(0, 7);
}

function money(value) {
  const n = Number(value || 0);
  return n.toFixed(2);
}

function studentSearchText(item) {
  if (!item) return "";
  return [
    item.name,
    item.grade,
    item.class_no,
    `${item.grade}-${item.class_no}`,
    `${item.grade}.${item.class_no}`,
    item.permanent_id,
    item.annual_id
  ].join(" ").toLowerCase();
}

function filterStudents(students, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return students;
  return students.filter((item) => studentSearchText(item).includes(q));
}

Page({
  data: {
    month: currentMonth(),
    date: today(),
    loading: false,
    active: "dashboard",
    sections: [
      { key: "dashboard", label: "总览" },
      { key: "students", label: "学生" },
      { key: "payments", label: "充值" },
      { key: "settlement", label: "月结" },
      { key: "costs", label: "成本" },
      { key: "reports", label: "报表" },
      { key: "users", label: "账户" }
    ],
    dashboard: {},
    students: [],
    studentQuery: "",
    selectedStudent: null,
    payments: [],
    settlements: [],
    costs: [],
    costSummary: {},
    settings: {},
    users: [],
    paymentMethods: ["微信", "支付宝", "现金", "其它"],
    paymentMethodIndex: 0,
    paymentForm: {
      amount: "",
      customMethod: "",
      note: ""
    },
    costTypes: [
      { label: "食材", value: "variable" },
      { label: "其它成本", value: "other" }
    ],
    costTypeIndex: 0,
    costForm: {
      item: "",
      amount: "",
      note: ""
    },
    fixedCostForm: {
      amount: ""
    },
    laborForm: {
      teacher: "",
      salary: ""
    },
    userRoles: ["teacher", "admin"],
    userRoleIndex: 0,
    userForm: {
      username: "",
      display_name: "",
      pin: "",
      active: true
    },
    reportStudent: null,
    reportStudentSearchText: "",
    reportStudentOpen: false,
    filteredReportStudents: []
  },

  onLoad() {
    const user = app.globalData.user || wx.getStorageSync("user");
    if (!user || user.role !== "admin") {
      wx.showModal({
        title: "无权限",
        content: "老师账号只能使用手机点名，管理员后台需要 admin 权限。",
        showCancel: false,
        success: () => wx.redirectTo({ url: "/pages/attendance/attendance" })
      });
      return;
    }
    this.refreshAll();
  },

  onShow() {
    const user = app.globalData.user || wx.getStorageSync("user");
    if (user && user.role === "admin") this.refreshAll();
  },

  switchSection(e) {
    this.setData({ active: e.currentTarget.dataset.key });
  },

  onMonthChange(e) {
    this.setData({ month: e.detail.value }, () => this.refreshAll());
  },

  onDateChange(e) {
    this.setData({ date: e.detail.value });
  },

  async refreshAll() {
    this.setData({ loading: true });
    try {
      const month = this.data.month;
      const [dashboard, students, payments, settlements, costs, users, settings] = await Promise.all([
        request(`/api/dashboard?month=${month}`),
        request(`/api/students?q=${encodeURIComponent(this.data.studentQuery || "")}`),
        request(`/api/payments?month=${month}`),
        request(`/api/settlements?month=${month}`),
        request(`/api/costs?month=${month}`),
        request("/api/users"),
        request("/api/settings")
      ]);
      const settingMap = settings.settings || {};
      this.setData({
        dashboard: dashboard.dashboard || {},
        students: students.students || [],
        payments: payments.payments || [],
        settlements: settlements.rows || [],
        costs: costs.rows || [],
        costSummary: costs.summary || {},
        settings: settingMap,
        users: users.users || [],
        fixedCostForm: {
          amount: settingMap.monthly_fixed_cost_amount || ""
        },
        laborForm: {
          teacher: settingMap.monthly_labor_teacher || "",
          salary: settingMap.monthly_labor_salary || ""
        },
        filteredReportStudents: filterStudents(students.students || [], this.data.reportStudentSearchText)
      });
      if (!this.data.selectedStudent && (students.students || []).length) {
        this.setData({
          selectedStudent: students.students[0],
          reportStudent: students.students[0],
          reportStudentSearchText: students.students[0].name
        });
      }
    } catch (err) {
      wx.showToast({ title: err.message || "后台载入失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onStudentSearch(e) {
    this.setData({ studentQuery: e.detail.value });
  },

  async searchStudents() {
    try {
      const data = await request(`/api/students?q=${encodeURIComponent(this.data.studentQuery || "")}`);
      this.setData({ students: data.students || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "学生搜索失败", icon: "none" });
    }
  },

  selectStudent(e) {
    const pid = e.currentTarget.dataset.pid;
    const student = this.data.students.find((item) => item.permanent_id === pid);
    if (!student) return;
    this.setData({ selectedStudent: student, reportStudent: student });
  },

  onPaymentMethod(e) {
    this.setData({ paymentMethodIndex: Number(e.detail.value || 0) });
  },

  onPaymentAmount(e) {
    this.setData({ "paymentForm.amount": e.detail.value });
  },

  onPaymentCustomMethod(e) {
    this.setData({ "paymentForm.customMethod": e.detail.value });
  },

  onPaymentNote(e) {
    this.setData({ "paymentForm.note": e.detail.value });
  },

  async submitPayment() {
    const student = this.data.selectedStudent;
    if (!student) {
      wx.showToast({ title: "先选择学生", icon: "none" });
      return;
    }
    const methodBase = this.data.paymentMethods[this.data.paymentMethodIndex];
    const method = methodBase === "其它" ? this.data.paymentForm.customMethod : methodBase;
    if (!this.data.paymentForm.amount) {
      wx.showToast({ title: "填写充值金额", icon: "none" });
      return;
    }
    try {
      await request("/api/payments", {
        method: "POST",
        data: {
          payment_date: this.data.date,
          permanent_id: student.permanent_id,
          amount: Number(this.data.paymentForm.amount),
          method,
          note: this.data.paymentForm.note
        }
      });
      wx.showToast({ title: "充值已保存", icon: "success" });
      this.setData({ paymentForm: { amount: "", customMethod: "", note: "" } });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "充值失败", icon: "none" });
    }
  },

  onCostType(e) {
    this.setData({ costTypeIndex: Number(e.detail.value || 0) });
  },

  onCostItem(e) {
    this.setData({ "costForm.item": e.detail.value });
  },

  onCostAmount(e) {
    this.setData({ "costForm.amount": e.detail.value });
  },

  onCostNote(e) {
    this.setData({ "costForm.note": e.detail.value });
  },

  async submitCost() {
    const type = this.data.costTypes[this.data.costTypeIndex];
    if (!this.data.costForm.item || !this.data.costForm.amount) {
      wx.showToast({ title: "填写成本项目和金额", icon: "none" });
      return;
    }
    try {
      await request("/api/costs", {
        method: "POST",
        data: {
          cost_date: this.data.date,
          cost_type: type.value,
          item: this.data.costForm.item,
          amount: Number(this.data.costForm.amount),
          note: this.data.costForm.note
        }
      });
      wx.showToast({ title: "成本已保存", icon: "success" });
      this.setData({ costForm: { item: "", amount: "", note: "" } });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "成本保存失败", icon: "none" });
    }
  },

  onFixedCostAmount(e) {
    this.setData({ "fixedCostForm.amount": e.detail.value });
  },

  async saveFixedCost() {
    try {
      await request("/api/settings", {
        method: "POST",
        data: {
          monthly_fixed_cost_amount: Number(this.data.fixedCostForm.amount || 0)
        }
      });
      wx.showToast({ title: "固定成本已保存", icon: "success" });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "固定成本保存失败", icon: "none" });
    }
  },

  onLaborTeacher(e) {
    this.setData({ "laborForm.teacher": e.detail.value });
  },

  onLaborSalary(e) {
    this.setData({ "laborForm.salary": e.detail.value });
  },

  async saveLaborCost() {
    try {
      await request("/api/settings", {
        method: "POST",
        data: {
          monthly_labor_teacher: this.data.laborForm.teacher,
          monthly_labor_salary: Number(this.data.laborForm.salary || 0)
        }
      });
      wx.showToast({ title: "人工成本已保存", icon: "success" });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "人工成本保存失败", icon: "none" });
    }
  },

  async generateSettlement() {
    wx.showLoading({ title: "生成中" });
    try {
      await request("/api/settlements/generate", {
        method: "POST",
        timeout: 30000,
        data: { month: this.data.month }
      });
      wx.showToast({ title: "月结已生成", icon: "success" });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "月结失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },

  async generateReport(e) {
    const type = e.currentTarget.dataset.type;
    const paths = {
      daily: "/api/reports/daily",
      monthly: "/api/reports/monthly",
      costs: "/api/reports/costs",
      student: "/api/reports/student"
    };
    const data = type === "daily"
      ? { date: this.data.date }
      : { month: this.data.month };
    if (type === "student") {
      if (!this.data.reportStudent) {
        wx.showToast({ title: "先选择学生", icon: "none" });
        return;
      }
      data.permanent_id = this.data.reportStudent.permanent_id;
    }
    wx.showLoading({ title: "生成中" });
    try {
      await request(paths[type], { method: "POST", timeout: 30000, data });
      wx.showToast({ title: "报表已生成", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "生成失败", icon: "none" });
    } finally {
      wx.hideLoading();
    }
  },

  onReportStudentFocus() {
    this.setData({
      reportStudentOpen: true,
      reportStudentSearchText: this.data.reportStudent ? this.data.reportStudent.name : "",
      filteredReportStudents: filterStudents(this.data.students, "")
    });
  },

  onReportStudentInput(e) {
    const value = e.detail.value;
    this.setData({
      reportStudentSearchText: value,
      reportStudentOpen: true,
      filteredReportStudents: filterStudents(this.data.students, value)
    });
  },

  selectReportStudent(e) {
    const pid = e.currentTarget.dataset.pid;
    const student = this.data.students.find((item) => item.permanent_id === pid);
    if (!student) return;
    this.setData({
      reportStudent: student,
      selectedStudent: student,
      reportStudentSearchText: student.name,
      reportStudentOpen: false
    });
  },

  closeReportStudentSearch() {
    this.setData({
      reportStudentOpen: false,
      reportStudentSearchText: this.data.reportStudent ? this.data.reportStudent.name : ""
    });
  },

  onUserField(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [`userForm.${field}`]: e.detail.value });
  },

  onUserRole(e) {
    this.setData({ userRoleIndex: Number(e.detail.value || 0) });
  },

  toggleUserActive() {
    this.setData({ "userForm.active": !this.data.userForm.active });
  },

  async submitUser() {
    const form = this.data.userForm;
    if (!form.username || !form.display_name) {
      wx.showToast({ title: "填写账号和姓名", icon: "none" });
      return;
    }
    try {
      await request("/api/users", {
        method: "POST",
        data: {
          username: form.username,
          display_name: form.display_name,
          pin: form.pin,
          role: this.data.userRoles[this.data.userRoleIndex],
          active: form.active
        }
      });
      wx.showToast({ title: "账号已保存", icon: "success" });
      this.setData({ userForm: { username: "", display_name: "", pin: "", active: true }, userRoleIndex: 0 });
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || "账号保存失败", icon: "none" });
    }
  },

  money
});
