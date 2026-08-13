/**
 * 嵌入模式会话级身份透传（实机测试问题4 修复，长期记忆功能根因）：
 *
 * 主站 5173（digitalHumanVue）ChatView.vue 在 iframe URL 中携带
 * auth_token（登录用户/游客 JWT，来自主站 auth store）与 visitor_id
 * （见 ChatView.vue iframeSrc 注释「供数字人端透传对话接口（Bearer）」）。
 * 本模块在首次访问时解析一次并缓存为会话级状态；对话请求将 auth_token
 * 注入 LLM_API_TOKEN → ADH 后端 langchainAgent._build_headers 自动加
 * `Bearer ` 前缀 → 主后端 security.resolve_dialogue_user 解析出真实用户
 * 身份（对话落库 visitor_id=用户 id，长期记忆 save/extract/recall 按
 * user 隔离生效）。
 *
 * 未登录（URL 无 auth_token 参数）时返回空串：不注入任何 token，对话
 * 请求与现状完全一致（主后端回退请求体 user=visitor 兜底，无回归）。
 */

export interface EmbedAuth {
    /** 登录用户/游客 JWT（主站 auth store 的 token），空串表示未登录 */
    authToken: string;
    /** 主站 visitor id（仅会话级状态存储；主后端无对应请求字段，暂不消费） */
    visitorId: string;
}

const EMPTY_AUTH: EmbedAuth = { authToken: "", visitorId: "" };

/**
 * 从 URL search 字符串解析嵌入身份参数（纯函数，不依赖 DOM，
 * 便于无浏览器环境单测）。search 形如 "?visitor_id=xxx&auth_token=yyy"。
 */
export function parseEmbedAuth(search: string): EmbedAuth {
    const params = new URLSearchParams(search);
    return {
        authToken: params.get("auth_token") || "",
        visitorId: params.get("visitor_id") || "",
    };
}

let cached: EmbedAuth | null = null;

/**
 * 会话级嵌入身份：惰性解析 window.location.search 并缓存（iframe URL
 * 由主站一次生成，页面生命周期内参数不变）。SSR/无 window 环境返回空身份。
 */
export function getEmbedAuth(): EmbedAuth {
    if (cached) return cached;
    if (typeof window === "undefined") {
        cached = EMPTY_AUTH;
        return cached;
    }
    cached = parseEmbedAuth(window.location.search);
    return cached;
}

/** 仅测试用：清空缓存，便于模拟不同 URL 参数场景。 */
export function _resetEmbedAuthCache(): void {
    cached = null;
}
