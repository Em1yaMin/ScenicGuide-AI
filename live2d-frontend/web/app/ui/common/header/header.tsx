'use client'

import { useEffect, useState } from "react";
import { useHeartbeatStore } from "@/app/lib/store";
import { HeadAlert } from "@/app/ui/common/alert";
import { PROJ_NAME, HEART_BEAT_ALERT, HEART_BEAT_CHECK_1S } from "@/app/lib/constants";
import { WindowMenu, PhoneMenu } from "./menu";
import Github from "./github";
import Link from "next/link";
import useWebSocket, { ReadyState } from 'react-use-websocket';
import { get_heatbeat_wss } from '@/app/lib/api';



export default function Header() {
    const { heartbeat, setHeartbeat } = useHeartbeatStore();
    const { readyState } = useWebSocket(
        get_heatbeat_wss(),
        {
            shouldReconnect: () => true,
            heartbeat: {
                message: 'ping',
                returnMessage: 'pong',
                timeout: 3000, // 3s
                interval: HEART_BEAT_CHECK_1S,
            },
        }
    );

    useEffect(() => {
        if (readyState === ReadyState.OPEN) {
            setHeartbeat(true);
        } else {
            setHeartbeat(false);
        }
    }, [readyState]);

    // hide_menu=1：嵌入模式（如主项目 5173 iframe 内），隐藏菜单/设置/画廊/GitHub/Logo 链接，仅保留标题
    // 注意：window 仅在客户端可用（SSR 阶段 undefined），故在 effect 中读取
    const [hideMenu, setHideMenu] = useState(false);
    useEffect(() => {
        setHideMenu(new URLSearchParams(window.location.search).get('hide_menu') === '1');
    }, []);

    return (
        <header className="text-gray-600 min-w-full h-min z-10">
            {heartbeat ? null : <HeadAlert message={HEART_BEAT_ALERT} />}
            <div className="flex flex-nowrap mx-auto p-1 md:p-5 flex-row items-center">
                {hideMenu ? (
                    <span className="flex title-font font-medium items-center text-gray-900">
                        <img src="/icons/app_icon.svg" className="w-8 h-8 md:w-10 md:h-10 text-white p-2 rounded-full border-2 border-black" />
                        <span className="ml-3 text-sm md:text-xl text-nowrap">{PROJ_NAME}</span>
                    </span>
                ) : (
                    <Link href={"/"} target="_blank" className="flex title-font font-medium items-center text-gray-900 hover:text-gray-600">
                        <img src="/icons/app_icon.svg" className="w-8 h-8 md:w-10 md:h-10 text-white p-2 rounded-full border-2 border-black" />
                        <span className="ml-3 text-sm md:text-xl text-nowrap">{PROJ_NAME}</span>
                    </Link>
                )}

                {!hideMenu && (
                    <>
                        <div className="hidden md:block mr-auto ml-2 md:ml-4 pl-2 md:pl-4 border-l border-gray-400">
                            <WindowMenu />
                        </div>
                        <div className="md:hidden ml-auto">
                            <PhoneMenu />
                        </div>
                        <div className="hidden md:block">
                            <Github />
                        </div>
                    </>
                )}
            </div>
        </header>
    );
}