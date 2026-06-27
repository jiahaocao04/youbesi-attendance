const { request } = require("../../utils/api");

function currentMonth() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

Page({
  data: {
    month: currentMonth(),
    loading: false,
    dashboard: {
      students_count: 0,
      attendance_records: 0,
      payment_sum: 0,
      total_due: 0,
      debt_count: 0,
      debt_total: 0,
      shopping_total: 0,
      settled_count: 0
    },
    modules: [
      { key: "students", title: "学生名单", desc: "查看学生、班级、余额", status: "已接入" },
      { key: "payments", title: "充值登记", desc: "微信 / 支付宝 / 现金 / 其它", status: "待做页面" },
      { key: "settlement", title: "月底结算", desc: "生成应缴、欠费、余额", status: "接口已接入" },
      { key: "reports", title: "导出报表", desc: "日报 / 月报 / 学生明细", status: "接口已接入" },
      { key: "costs", title: "成本记录", desc: "房租水电、食材、人工", status: "接口已接入" },
      { key: "users", title: "账户管理", desc: "老师账户、PIN、权限", status: "接口已接入" }
    ]
  },

  onLoad() {
    this.loadDashboard();
  },

  onShow() {
    this.loadDashboard();
  },

  onMonthChange(e) {
    this.setData({ month: e.detail.value }, () => this.loadDashboard());
  },

  async loadDashboard() {
    this.setData({ loading: true });
    try {
      const data = await request(`/api/dashboard?month=${this.data.month}`);
      this.setData({ dashboard: data.dashboard || this.data.dashboard });
    } catch (err) {
      wx.showToast({ title: err.message || "管理数据载入失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onModuleTap(e) {
    const key = e.currentTarget.dataset.key;
    const labels = {
      students: "学生名单会做成可搜索表格",
      payments: "充值登记下一步做表单",
      settlement: "月底结算下一步做生成按钮",
      reports: "报表导出下一步做下载入口",
      costs: "成本记录下一步做录入表单",
      users: "账户管理下一步做老师账号维护"
    };
    wx.showModal({
      title: "管理员功能",
      content: labels[key] || "这个功能下一步细化",
      showCancel: false
    });
  }
});
