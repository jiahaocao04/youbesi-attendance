# 优贝思小程序公网多设备部署说明

目标：不同校区、不同 Wi-Fi、手机流量都能访问同一套账本和考勤数据。

## 必要条件

1. 一个长期运行的云端后端服务。
2. 一个公网 HTTPS 地址，例如 `https://api.youbesi.example.com`。
3. 数据库或持久化磁盘，确保重启/升级后数据不丢。
4. 微信小程序后台配置合法 request 域名。

## 免费试用版

免费试用可以先用 Render Free Web Service，不绑定持久磁盘、不绑定独立域名，直接使用 Render 自动提供的 HTTPS 地址，例如：

```text
https://youbesi-attendance-api.onrender.com
```

限制：

- 空闲一段时间后服务会休眠，第一次打开可能要等约一分钟。
- 本地 SQLite 数据库写在临时文件系统里，服务重启、重新部署或休眠后可能丢失。
- 所以免费版只适合界面和流程试用，不适合真实记账。

## 当前代码已经准备好的部分

- 后端支持从环境变量指定数据目录：
  - `JY_DATA_DIR`
  - `JY_DB_PATH`
  - `JY_REPORTS_DIR`
  - `JY_HOST`
  - `JY_PORT`
- 已提供 Dockerfile，可部署到支持容器的云平台。
- 小程序已新增 `utils/config.js`：
  - 本地测试时保持 `CLOUD_BASE_URL = ""`
  - 云端上线时改为 `CLOUD_BASE_URL = "https://你的后端域名"`

## 上线时要改的地方

打开小程序文件：

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

然后重新预览/上传小程序。

## 重要提醒

- 正式公网版本不能继续使用 `http://192.168.x.x`，那只能同 Wi-Fi 内部访问。
- 正式小程序请求域名必须是 HTTPS。
- AppSecret 不要放进小程序前端，只能放服务器端。
- 正式使用前请把默认 `admin / 123456` 改成强 PIN。
