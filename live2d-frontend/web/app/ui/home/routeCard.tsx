'use client';
/**
 * 路线卡片（批次8 B3：对话中路线展示）。
 *
 * 渲染 plan_route 工具结果（后端 done 事件 route_data 字段透出，契约见
 * digitalHumanApi/doc/amap-gps-api.md）：
 * - 成功信封 {"ok": true, "data": {mode/origin/destination/distance/
 *   duration/steps}}：高德 JS API 地图（起点/终点 Marker + 路径 Polyline）
 *   + 距离/时长摘要 + 步骤列表
 * - 失败信封 {"ok": false, "error": "amap_*", "message"}：错误提示卡片
 *   （如实转达后端错误码，不编造路线）
 * - 地图加载失败（网络/域名白名单/key 未配置）或坐标非法时**降级文字路线**：
 *   步骤列表 + 距离时长照常展示，不阻断对话
 *
 * 高德 JS API 按需加载（web/app/lib/amap.ts），不引入 npm 依赖。
 */
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
    loadAmap,
    formatDistance,
    formatDuration,
    parseLocation,
} from "@/app/lib/amap";

/** plan_route 工具结果信封（后端 route_data 字段，字段名见契约文档）。 */
export interface RouteEnvelope {
    ok?: boolean;
    error?: string;
    message?: string;
    data?: {
        mode?: string;
        origin?: string;
        destination?: string;
        distance?: string | number;
        duration?: string | number;
        steps?: Array<{
            instruction?: string;
            distance?: string | number;
            duration?: string | number;
            road?: string;
        }>;
        strategy?: string;
    };
}

/** 出行方式文案映射（后端 mode 枚举 walking/driving/transit）。 */
const MODE_LABELS: Record<string, string> = {
    walking: "🚶 步行",
    driving: "🚗 驾车",
    transit: "🚌 公交",
};

/** 地图子组件：高德 JS API 渲染起点/终点 Marker + 路径 Polyline。 */
function RouteMap({
    origin,
    destination,
    onMapError,
}: {
    origin: [number, number];
    destination: [number, number];
    onMapError: (message: string) => void;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<any>(null);

    useEffect(() => {
        let cancelled = false;
        // 按需加载高德 JS API（首屏不注入；失败由调用方降级文字路线）
        loadAmap()
            .then((AMap: any) => {
                if (cancelled || !containerRef.current) return;
                const map = new AMap.Map(containerRef.current, {
                    zoom: 14,
                    resizeEnable: true,
                });
                mapRef.current = map;
                const startMarker = new AMap.Marker({
                    position: origin,
                    title: "起点",
                });
                const endMarker = new AMap.Marker({
                    position: destination,
                    title: "终点",
                });
                map.add([startMarker, endMarker]);
                // 路线路径点后端未透出（仅起终点坐标），绘制直线示意；
                // 步骤列表提供详细文字指引
                const polyline = new AMap.Polyline({
                    path: [origin, destination],
                    strokeColor: "#3b82f6",
                    strokeWeight: 5,
                    lineJoin: "round",
                    strokeOpacity: 0.9,
                });
                map.add(polyline);
                // 视野自适应包含两个 Marker 与路线（留边距）
                map.setFitView([startMarker, endMarker, polyline], false, [40, 40, 40, 40]);
            })
            .catch((err: Error) => {
                if (!cancelled) onMapError(err?.message || "地图加载失败");
            });
        return () => {
            cancelled = true;
            if (mapRef.current) {
                mapRef.current.destroy();
                mapRef.current = null;
            }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [origin[0], origin[1], destination[0], destination[1]]);

    return <div ref={containerRef} className="w-full h-48 rounded-lg bg-gray-100" />;
}

export default function RouteCard({ route }: { route: RouteEnvelope }) {
    const [mapError, setMapError] = useState<string | null>(null);

    // 失败信封：如实展示后端错误码，不编造路线
    if (!route || route.ok === false) {
        return (
            <div className="px-4 py-3 rounded-lg rounded-bl-none bg-white border border-gray-200 text-xs text-gray-600 w-72">
                <div className="font-medium text-gray-700 mb-1">🗺️ 路线规划</div>
                <div className="text-red-500">
                    {route?.message || "路线规划暂不可用"}
                    {route?.error ? (
                        <span className="ml-1 text-gray-400">（{route.error}）</span>
                    ) : null}
                </div>
            </div>
        );
    }

    const data = route.data || {};
    const mode = data.mode || "";
    const origin = parseLocation(data.origin || "");
    const destination = parseLocation(data.destination || "");
    const steps = Array.isArray(data.steps) ? data.steps : [];
    const distanceText = formatDistance(data.distance ?? "");
    const durationText = formatDuration(data.duration ?? "");
    // 地图可用条件：起终点坐标均合法（key 未配置/加载失败时 mapError 兜底）
    const canDrawMap = !!origin && !!destination;

    return (
        <div className="px-4 py-3 rounded-lg rounded-bl-none bg-white border border-gray-200 text-xs text-gray-600 w-72 space-y-2">
            <div className="flex items-center justify-between">
                <span className="font-medium text-gray-700">
                    {MODE_LABELS[mode] || "🧭 路线"}
                </span>
                <span className="text-gray-500">
                    {[distanceText, durationText].filter(Boolean).join(" · ") || "距离/时长未知"}
                </span>
            </div>
            {canDrawMap ? (
                <RouteMap
                    origin={origin}
                    destination={destination}
                    onMapError={(msg) => setMapError(msg)}
                />
            ) : null}
            {mapError ? (
                <div className="text-gray-400">
                    🗺️ 地图暂不可用（{mapError}），已为您展示文字路线
                </div>
            ) : null}
            {steps.length > 0 ? (
                <ol className="space-y-1 max-h-40 overflow-y-auto no-scrollbar">
                    {steps.map((step, i) => (
                        <li key={i} className="flex gap-1.5 items-start">
                            <span className="shrink-0 w-4 h-4 rounded-full bg-blue-500 text-white text-[10px] flex items-center justify-center mt-0.5">
                                {i + 1}
                            </span>
                            <span className="leading-snug">{step.instruction || "（无指引）"}</span>
                        </li>
                    ))}
                </ol>
            ) : (
                <div className="text-gray-400">暂无详细步骤指引</div>
            )}
        </div>
    );
}
