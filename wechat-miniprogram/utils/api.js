const app = getApp();
const config = require("./config");

function request(path, options = {}) {
  const baseUrl = config.HAS_CLOUD_BACKEND
    ? config.DEFAULT_BASE_URL
    : (app.globalData.baseUrl || wx.getStorageSync("baseUrl") || config.DEFAULT_BASE_URL);
  const cookie = app.globalData.sessionCookie || wx.getStorageSync("sessionCookie") || "";
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl}${path}`,
      method: options.method || "GET",
      data: options.data || {},
      timeout: options.timeout || 12000,
      header: {
        "content-type": "application/json",
        ...(cookie ? { Cookie: cookie } : {})
      },
      success(res) {
        const setCookie = res.header["Set-Cookie"] || res.header["set-cookie"];
        if (setCookie) {
          const sessionCookie = String(setCookie).split(";")[0];
          app.globalData.sessionCookie = sessionCookie;
          wx.setStorageSync("sessionCookie", sessionCookie);
        }
        const data = res.data || {};
        if (res.statusCode >= 400 || data.ok === false) {
          reject(new Error(data.error || `请求失败 ${res.statusCode}`));
          return;
        }
        resolve(data);
      },
      fail(err) {
        reject(new Error(err.errMsg || "网络请求失败"));
      }
    });
  });
}

module.exports = { request };
