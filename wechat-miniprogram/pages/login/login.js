const { request } = require("../../utils/api");
const config = require("../../utils/config");
const app = getApp();

Page({
  data: {
    baseUrl: "",
    username: "",
    pin: "",
    allowServerEdit: config.ALLOW_SERVER_EDIT,
    serverModeLabel: config.SERVER_MODE_LABEL,
    loading: false
  },
  onLoad() {
    const storedBaseUrl = wx.getStorageSync("baseUrl") || "";
    const baseUrl = config.HAS_CLOUD_BACKEND ? config.DEFAULT_BASE_URL : (storedBaseUrl || app.globalData.baseUrl || config.DEFAULT_BASE_URL);
    this.setData({
      baseUrl,
      allowServerEdit: config.ALLOW_SERVER_EDIT,
      serverModeLabel: config.SERVER_MODE_LABEL,
      username: wx.getStorageSync("lastUsername") || ""
    });
  },
  onBaseUrl(e) {
    if (!config.ALLOW_SERVER_EDIT) return;
    this.setData({ baseUrl: e.detail.value.trim() });
  },
  onUsername(e) {
    this.setData({ username: e.detail.value.trim() });
  },
  onPin(e) {
    this.setData({ pin: e.detail.value });
  },
  async login() {
    const { username, pin } = this.data;
    const baseUrl = config.HAS_CLOUD_BACKEND ? config.DEFAULT_BASE_URL : this.data.baseUrl;
    if (!baseUrl || !username || !pin) {
      wx.showToast({ title: "请填完整", icon: "none" });
      return;
    }
    this.setData({ loading: true });
    try {
      app.globalData.baseUrl = baseUrl.replace(/\/$/, "");
      wx.setStorageSync("baseUrl", app.globalData.baseUrl);
      const data = await request("/api/login", {
        method: "POST",
        data: { username, pin }
      });
      app.globalData.user = data.user;
      wx.setStorageSync("user", data.user);
      wx.setStorageSync("lastUsername", username);
      wx.redirectTo({ url: "/pages/attendance/attendance" });
    } catch (err) {
      wx.showToast({ title: err.message || "登录失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  }
});
