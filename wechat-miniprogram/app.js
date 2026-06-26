const config = require("./utils/config");

App({
  globalData: {
    user: null,
    baseUrl: "",
    sessionCookie: "",
    serverModeLabel: config.SERVER_MODE_LABEL
  },
  onLaunch() {
    const storedBaseUrl = wx.getStorageSync("baseUrl") || "";
    const baseUrl = config.HAS_CLOUD_BACKEND ? config.DEFAULT_BASE_URL : (storedBaseUrl || config.DEFAULT_BASE_URL);
    const sessionCookie = wx.getStorageSync("sessionCookie") || "";
    const user = wx.getStorageSync("user") || null;
    this.globalData.baseUrl = baseUrl;
    wx.setStorageSync("baseUrl", baseUrl);
    this.globalData.sessionCookie = sessionCookie;
    this.globalData.user = user;
  }
});
