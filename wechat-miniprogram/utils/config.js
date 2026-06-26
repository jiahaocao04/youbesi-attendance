// Cloud-ready runtime configuration.
// Free trial cloud backend:
//   https://youbesi-attendance.onrender.com
//
// For local testing, set CLOUD_BASE_URL back to "".
const CLOUD_BASE_URL = "https://youbesi-attendance.onrender.com";
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
  SERVER_MODE_LABEL: HAS_CLOUD_BACKEND ? "cloud" : "local"
};
