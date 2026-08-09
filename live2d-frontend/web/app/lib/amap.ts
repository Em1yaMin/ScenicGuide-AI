/**
 * 高德 JS API 按需加载器（批次8 B3：对话中路线展示）。
 *
 * - 首次需要地图时注入 <script>，避免拖慢首屏（后续复用加载完成的 Promise）
 * - key/安全密钥来自环境变量（Next.js 约定 NEXT_PUBLIC_ 前缀，对应任务
 *   约定的 VITE_AMAP_JS_KEY/VITE_AMAP_SECURITY_CODE，部署时写入 web/.env，
 *   不入库）：
 *     NEXT_PUBLIC_AMAP_JS_KEY / NEXT_PUBLIC_AMAP_SECURITY_CODE
 * - 未配置 key 或加载失败（网络/域名白名单）时 reject，调用方降级文字路线，
 *   不阻断对话
 */

// 高德 JS API v2.0 入口地址（key 经 URL 注入；plugin 按需声明）
const AMAP_SCRIPT_URL = "https://webapi.amap.com/maps";
const AMAP_VERSION = "2.0";

// 全局安全密钥配置键（高德 v2.0 约定：挂到 window._AMapSecurityConfig）
const SECURITY_CONFIG_KEY = "_AMapSecurityConfig";

interface AmapGlobal {
    AMap?: any;
    _AMapSecurityConfig?: { securityJsCode: string };
}

declare global {
    interface Window {
        AMap?: any;
        _AMapSecurityConfig?: { securityJsCode: string };
    }
}

let amapPromise: Promise<any> | null = null;

/** 读取高德 JS key（Next.js 客户端仅 NEXT_PUBLIC_ 前缀可用）。 */
export function getAmapKeys(): { key: string; securityCode: string } {
    const key =
        process.env.NEXT_PUBLIC_AMAP_JS_KEY ||
        (process.env.VITE_AMAP_JS_KEY as string | undefined) ||
        "";
    const securityCode =
        process.env.NEXT_PUBLIC_AMAP_SECURITY_CODE ||
        (process.env.VITE_AMAP_SECURITY_CODE as string | undefined) ||
        "";
    return { key, securityCode };
}

/**
 * 加载高德 JS API（幂等：并发调用共享同一个 Promise）。
 *
 * 成功 resolve AMap 全局对象；失败 reject（Error 带中文 message，供调用方
 * 展示/降级）。失败后允许重试（重置缓存 Promise）。
 */
export function loadAmap(): Promise<any> {
    if (amapPromise) return amapPromise;
    if (typeof window === "undefined") {
        amapPromise = Promise.reject(new Error("非浏览器环境，无法加载地图"));
        return amapPromise;
    }
    const win = window as unknown as AmapGlobal;
    if (win.AMap) {
        amapPromise = Promise.resolve(win.AMap);
        return amapPromise;
    }
    const { key, securityCode } = getAmapKeys();
    if (!key) {
        amapPromise = Promise.reject(new Error("高德 JS key 未配置（NEXT_PUBLIC_AMAP_JS_KEY）"));
        return amapPromise;
    }
    // 安全密钥：v2.0 要求 script 加载前设置（可缺省——部分 key 未绑定安全密钥）
    if (securityCode) {
        win._AMapSecurityConfig = { securityJsCode: securityCode };
    }
    amapPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src =
            `${AMAP_SCRIPT_URL}?v=${AMAP_VERSION}` +
            `&key=${encodeURIComponent(key)}` +
            `&plugin=AMap.Scale,AMap.ToolBar`;
        script.async = true;
        script.onload = () => {
            if (win.AMap) {
                resolve(win.AMap);
            } else {
                // 脚本加载完成但全局对象缺失（白名单/版本异常等）
                amapPromise = null;
                reject(new Error("高德地图脚本加载异常（AMap 对象未就绪，请检查 key 与域名白名单）"));
            }
        };
        script.onerror = () => {
            amapPromise = null;
            reject(new Error("高德地图脚本加载失败（网络不可达或域名未在白名单）"));
        };
        document.head.appendChild(script);
    });
    return amapPromise;
}

/** 格式化距离：米 → 可读文本（<1000 显示米，否则显示公里）。 */
export function formatDistance(meters: string | number): string {
    const m = Number(meters);
    if (!Number.isFinite(m) || m <= 0) return "";
    if (m < 1000) return `${Math.round(m)} 米`;
    return `${(m / 1000).toFixed(1)} 公里`;
}

/** 格式化时长：秒 → 「X 分钟」/「X 小时 Y 分钟」。 */
export function formatDuration(seconds: string | number): string {
    const s = Number(seconds);
    if (!Number.isFinite(s) || s <= 0) return "";
    const totalMin = Math.round(s / 60);
    if (totalMin < 60) return `${totalMin} 分钟`;
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return m > 0 ? `${h} 小时 ${m} 分钟` : `${h} 小时`;
}

/** 解析 "经度,纬度" 坐标 → [lng, lat]；非法返回 null（供地图组件降级）。 */
export function parseLocation(location: string): [number, number] | null {
    if (!location) return null;
    const parts = location.split(",").map((p) => Number(p.trim()));
    if (parts.length !== 2 || !parts.every((v) => Number.isFinite(v))) return null;
    const [lng, lat] = parts;
    if (lng < -180 || lng > 180 || lat < -90 || lat > 90) return null;
    return [lng, lat];
}
