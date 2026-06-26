// Cloud-ready runtime configuration.
//
// For real multi-network use, set CLOUD_BASE_URL to the public HTTPS backend:
//   const CLOUD_BASE_URL = "https://api.example.com";
//
// Keep it empty during local/LAN testing so the current preview flow still works.
const CLOUD_BASE_URL = "";
const LOCAL_BASE_URL = "http://192.168.1.16:8766";

function normalizeBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

const HAS_CLOUD_BACKEND = Boolean(normalizeBaseUrl(CLOUD_BASE_URL));
const DEFAULT_BASE_URL = normalizeBaseUrl(HAS_CLOUD_BACKEND ? CLOUD_BASE_URL : LOCAL_BASE_URL);

module.exports = {
  CLOUD_BASE_URL: normalizeBaseUrl(CLOUD_BASE_URL),
  LOCAL_BASE_URL: normalizeBaseUrl(LOCAL_BASE_URL),
  DEFAULT_BASE_URL,
  HAS_CLOUD_BACKEND,
  ALLOW_SERVER_EDIT: !HAS_CLOUD_BACKEND,
  SERVER_MODE_LABEL: HAS_CLOUD_BACKEND ? "云端正式版" : "本地测试版"
};
