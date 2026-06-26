# 优贝思小程序云端连接说明

当前小程序已支持两种模式：

- 本地测试版：连接 `http://192.168.1.16:8766`
- 云端正式版：连接公网 HTTPS 后端，例如 `https://api.youbesi.example.com`

真正实现“不同校区、不同 Wi-Fi、手机流量都能用”，必须使用云端正式版。

## 切换到云端正式版

打开：

```text
C:\Users\jhjhjh\youbesi-miniprogram\utils\config.js
```

把：

```js
const CLOUD_BASE_URL = "";
```

改成：

```js
const CLOUD_BASE_URL = "https://你的后端域名";
```

保存后重新预览/上传小程序。

## 注意

- 小程序正式版不能依赖 `192.168.x.x`。
- 公网后端必须是 HTTPS。
- AppSecret 不要放进小程序代码。
