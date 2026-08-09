'use client';
// import Image from "next/image";
import clsx from "clsx";
import { debounce } from 'lodash';
import { useEffect, useRef, useState } from "react";
import { useChatRecordStore, ChatRole, ChatMessage, useAgentEngineSettingsStore, useAgentModeStore, useMuteStore, useInteractionModeStore, InteractionMode, useAudioAutoStopStore } from "@/app/lib/store";
import { ConfirmAlert } from "@/app/ui/common/alert";
import { AUDIO_SUPPORT_ALERT, AI_THINK_MESSAGE } from "@/app/lib/constants";
import { Comm } from "@/app/lib/comm";
import { CharacterManager } from "@/app/lib/character";
import Markdown from 'react-markdown';
import RouteCard, { RouteEnvelope } from "@/app/ui/home/routeCard";

let mediaStream: MediaStream | null = null;
let mediaRecorder: MediaRecorder | null = null;
let recordedChunks: Blob[] = [];
let isRecording: boolean = false;

/**
 * 将浏览器录制的音频 Blob（webm/opus 等）解码并转为 16kHz 16bit 单声道 WAV
 */
async function convertToWav(audioBlob: Blob): Promise<Blob> {
    const audioCtx = new AudioContext();
    const arrayBuffer = await audioBlob.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    
    const origSampleRate = audioBuffer.sampleRate;
    const origData = audioBuffer.getChannelData(0);
    const origSamples = origData.length;
    
    console.log("🎤 原始音频: sampleRate=" + origSampleRate + ", duration=" + audioBuffer.duration.toFixed(2) + "s");
    
    // 重采样到 16kHz
    const targetSampleRate = 16000;
    const targetSamples = Math.floor(origSamples * targetSampleRate / origSampleRate);
    const resampled = new Float32Array(targetSamples);
    for (let i = 0; i < targetSamples; i++) {
        const srcIdx = i * origSampleRate / targetSampleRate;
        const srcFloor = Math.floor(srcIdx);
        const srcCeil = Math.min(srcFloor + 1, origSamples - 1);
        const frac = srcIdx - srcFloor;
        resampled[i] = origData[srcFloor] * (1 - frac) + origData[srcCeil] * frac;
    }
    
    console.log("🎤 重采样: " + origSamples + " → " + targetSamples + " samples @ 16kHz");
    
    // 编码为 16-bit PCM WAV
    const wavBuffer = new ArrayBuffer(44 + targetSamples * 2);
    const view = new DataView(wavBuffer);
    
    function writeString(offset: number, str: string) {
        for (let i = 0; i < str.length; i++) {
            view.setUint8(offset + i, str.charCodeAt(i));
        }
    }
    
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + targetSamples * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);       // PCM
    view.setUint16(22, 1, true);       // mono
    view.setUint32(24, targetSampleRate, true);
    view.setUint32(28, targetSampleRate * 2, true);
    view.setUint16(32, 2, true);       // block align
    view.setUint16(34, 16, true);      // bits per sample
    writeString(36, 'data');
    view.setUint32(40, targetSamples * 2, true);
    
    // 写入 PCM 数据（Float32 → Int16）
    let offset = 44;
    for (let i = 0; i < targetSamples; i++) {
        const s = Math.max(-1, Math.min(1, resampled[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
    }
    
    audioCtx.close();
    return new Blob([wavBuffer], { type: 'audio/wav' });
}


export default function Chatbot(props: { showChatHistory: boolean }) {
    const { showChatHistory } = props;
    const { chatRecord, addChatRecord, updateLastRecord, clearChatRecord } = useChatRecordStore();
    const { mute } = useMuteStore();
    const { agentEngine } = useAgentModeStore();
    const { mode } = useInteractionModeStore();
    const { agentSettings } = useAgentEngineSettingsStore();
    const { audioAutoStop } = useAudioAutoStopStore();
    const [settings, setSettings] = useState<{[key: string]: string}>({});
    const [conversationId, setConversationId] = useState("");
    const [micRecording, setMicRecording] = useState(false);
    const [micRecordAlert, setmicRecordAlert] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    // Agent 工具调用步骤（LangChainAgent 步骤帧，如"正在检索知识库…"）
    const [agentSteps, setAgentSteps] = useState<{ tool: string; label: string }[]>([]);
    // 路线卡片数据（批次8 B3：后端 done 事件 route_data 透出，本轮的
    // plan_route 工具结果；新一轮对话清空）
    const [routeData, setRouteData] = useState<RouteEnvelope | null>(null);
    // 浏览器定位可用性（需 secure context：HTTPS 或 localhost；演示环境
    // 经局域网 IP 访问不可用则隐藏 📍 按钮）
    const [geoSupported, setGeoSupported] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const chatbotRef = useRef<HTMLDivElement>(null);
    
    useEffect(() => {
        let newSettings: {[key: string]: string} = {}
        if (agentEngine in agentSettings) {
            for (let setting of agentSettings[agentEngine]){
                newSettings[setting.NAME] = setting.DEFAULT;
            }
            setSettings(newSettings);
        }
        Comm.getInstance().getConversionId(agentEngine, newSettings).then((id) => {
            console.log("conversationId: ", id);
            setConversationId(id);
        });
        clearChatRecord();
    }, [agentEngine, agentSettings]);

    useEffect(() => {
        // 浏览器定位能力探测：secure context（HTTPS/localhost）才可用，
        // 否则隐藏 📍 按钮（navigator.geolocation 在非安全上下文不可用）
        const supported =
            typeof navigator !== "undefined" &&
            "geolocation" in navigator &&
            typeof window !== "undefined" &&
            (window.isSecureContext ||
                window.location.hostname === "localhost" ||
                window.location.hostname === "127.0.0.1");
        setGeoSupported(supported);
    }, []);

    const chatWithAI = (message: string) => {
        console.log("chatWithAI: ", message);
        // 新一轮对话：清空上一轮步骤提示与路线卡片
        setAgentSteps([]);
        setRouteData(null);
        addChatRecord({ role: ChatRole.HUMAN, content: message });
        // 请求AI
        let responseText = "";
        let audioText = "";
        // 保证顺序执行
        let audioRecorderIndex = 0;
        let audioRecorderDict = new Map<number, ArrayBuffer>();
        addChatRecord({ role: ChatRole.AI, content: AI_THINK_MESSAGE });
        if (audioAutoStop) {
            CharacterManager.getInstance().clearAudioQueue();
        }
        Comm.getInstance().streamingChat(message, agentEngine, conversationId, settings, (index: number, data: string) => {
            responseText += data;
            updateLastRecord({ role: ChatRole.AI, content: responseText });
            if (!mute && mode != InteractionMode.CHATBOT) {
                // 按照标点符号断句处理
                audioText += data;
                // 断句判断符号
                // let punc = ["。", ".", "！", "!", "？", "?", "；", ";", "，", ",", "(", ")", "（", "）"];
                let punc = ["。", ".", "？", "?", "；", ";", "，", ","];
                // 找到最后一个包含这些符号的位置
                let lastPuncIndex = -1;
                for (let i = 0; i < punc.length; i++) {
                    let index = audioText.lastIndexOf(punc[i]);
                    if (index > lastPuncIndex) {
                        // 防止需要连续的符号断句
                        let firstPart = audioText.slice(0, index + 1);
                        if (firstPart.split("(").length - firstPart.split(")").length != 0) {
                            break;
                        }
                        if (firstPart.split("[").length - firstPart.split("]").length != 0) {
                            break;
                        }
                        lastPuncIndex = index;
                        break;
                    }
                }
                if (lastPuncIndex !== -1) {
                    let firstPart = audioText.slice(0, lastPuncIndex + 1);
                    let secondPart = audioText.slice(lastPuncIndex + 1);
                    console.log("tts:", firstPart);
                    Comm.getInstance().tts(firstPart, settings).then(
                        (data: ArrayBuffer) => {
                            if (data) {
                                audioRecorderDict.set(index, data);
                                while (true) {
                                    if (!audioRecorderDict.has(audioRecorderIndex)) break;
                                    CharacterManager.getInstance().pushAudioQueue(audioRecorderDict.get(audioRecorderIndex)!);
                                    audioRecorderIndex++;
                                }
                            }
                        }
                    )
                    audioText = secondPart;
                } else {
                    audioRecorderDict.set(index, null)
                }
            }
        }, (index: number) => {
            // 处理剩余tts
            if (!mute && audioText) {
                console.log("tts:", audioText);
                Comm.getInstance().tts(audioText, settings).then(
                    (data: ArrayBuffer) => {
                        if (data) {
                            audioRecorderDict.set(index, data);
                            while (true) {
                                if (!audioRecorderDict.has(audioRecorderIndex)) break;
                                CharacterManager.getInstance().pushAudioQueue(audioRecorderDict.get(audioRecorderIndex)!);
                                audioRecorderIndex++;
                            }
                        }
                    }
                )
            }
            setIsProcessing(false);
        }, (step: { tool: string; label: string }) => {
            // 工具调用步骤：追加到步骤提示列表（连续重复步骤去重，避免同一
            // 工具多次调用时刷屏），按序显示直到下一轮对话
            console.log("agent step: ", step);
            setAgentSteps((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.tool === step.tool && last.label === step.label) {
                    return prev;
                }
                return [...prev, step];
            });
        }, (route: any) => {
            // 路线帧（批次8 B3）：plan_route 工具结果，渲染路线卡片
            console.log("route data: ", route);
            setRouteData(route || null);
        });
    }


    const micClick = () => {
        if (isProcessing) return;
        
        if (!mediaStream) {
            // 首次点击：请求麦克风并开始录音
            navigator.mediaDevices.getUserMedia({ audio: true })
                .then((stream) => {
                    mediaStream = stream;
                    console.log("🎤 麦克风已连接, tracks:", stream.getAudioTracks().map(t => t.label + ' (' + (t.readyState) + ')'));
                    
                    // 诊断：检测麦克风音量
                    const testCtx = new AudioContext();
                    const testSrc = testCtx.createMediaStreamSource(stream);
                    const analyser = testCtx.createAnalyser();
                    analyser.fftSize = 256;
                    testSrc.connect(analyser);
                    const dataArray = new Uint8Array(analyser.frequencyBinCount);
                    
                    setTimeout(() => {
                        analyser.getByteTimeDomainData(dataArray);
                        let maxVal = 0;
                        for (let i = 0; i < dataArray.length; i++) {
                            const v = Math.abs(dataArray[i] - 128);
                            if (v > maxVal) maxVal = v;
                        }
                        console.log("🎤 麦克风音量峰值: " + maxVal + "/128 " + (maxVal < 5 ? '❌ 几乎无声！请检查Windows麦克风设置' : '✅ 正常'));
                        testSrc.disconnect();
                        testCtx.close();
                    }, 500);
                    
                    // 使用 MediaRecorder（现代 API，不依赖废弃的 ScriptProcessorNode）
                    let mimeType = '';
                    // 优先选择 Tencent ASR 支持的格式
                    const supportedTypes = [
                        'audio/wav',
                        'audio/webm;codecs=opus',
                        'audio/webm',
                        'audio/ogg;codecs=opus',
                    ];
                    for (const t of supportedTypes) {
                        if (MediaRecorder.isTypeSupported(t)) {
                            mimeType = t;
                            break;
                        }
                    }
                    console.log("🎤 MediaRecorder mimeType:", mimeType || 'default');
                    
                    recordedChunks = [];
                    mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
                    mediaRecorder.ondataavailable = (e) => {
                        if (e.data.size > 0) recordedChunks.push(e.data);
                    };
                    mediaRecorder.onstop = () => {
                        const blob = new Blob(recordedChunks);
                        console.log("🎤 录音完成, size:", blob.size, "bytes, type:", blob.type);
                        
                        if (blob.size === 0) {
                            console.error("🎤 录音为空！请检查麦克风是否正常工作");
                            setIsProcessing(false);
                            return;
                        }
                        
                        // 将 webm/opus 转为 WAV
                        convertToWav(blob).then((wavBlob) => {
                            console.log("🎤 WAV 转换完成, size:", wavBlob.size);
                            Comm.getInstance().asr(wavBlob, settings).then(
                                (res) => {
                                    console.log("asr: ", res);
                                    if (res) {
                                        chatWithAI(res);
                                    } else {
                                        setIsProcessing(false);
                                    }
                                }
                            ).catch(() => setIsProcessing(false));
                        }).catch((err) => {
                            console.error("🎤 WAV 转换失败:", err);
                            // 降级：直接发送原始 blob
                            Comm.getInstance().asr(blob, settings).then(
                                (res) => {
                                    console.log("asr(raw): ", res);
                                    if (res) chatWithAI(res);
                                    else setIsProcessing(false);
                                }
                            ).catch(() => setIsProcessing(false));
                        });
                    };
                    
                    mediaRecorder.start();
                    isRecording = true;
                    setMicRecording(true);
                    console.log("🎤 开始录音...");
                })
                .catch((err) => {
                    console.error("🎤 麦克风访问失败:", err);
                    setmicRecordAlert(true);
                });
        } else if (isRecording) {
            // 再次点击：停止录音
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                mediaRecorder.stop();
            }
            isRecording = false;
            setMicRecording(false);
            setIsProcessing(true);
            // 释放麦克风
            mediaStream.getTracks().forEach(t => t.stop());
            mediaStream = null;
            mediaRecorder = null;
            console.log("🎤 停止录音");
        }
    }

    const fileClick = () => {
        console.log("file clicked");
    }

    const sendClick = () => {
        if (inputRef.current.value === "") return;
        setIsProcessing(true);
        chatWithAI(inputRef.current.value);
        inputRef.current.value = "";
    }

    // 📍 定位辅助（批次8 B3，可选）：获取浏览器位置并填入输入框
    // （navigator.geolocation → 坐标文本，agent 可据此做路径规划起点；
    // 非 secure context / 用户拒绝时提示，不阻断对话）
    const locateClick = () => {
        if (!navigator.geolocation || isProcessing) return;
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const { longitude, latitude } = pos.coords;
                if (inputRef.current) {
                    inputRef.current.value =
                        `我的位置：${longitude.toFixed(6)},${latitude.toFixed(6)}`;
                    inputRef.current.focus();
                }
            },
            (err) => {
                console.error("📍 定位失败: ", err);
                alert("定位失败：" + (err.message || "无法获取当前位置"));
            },
            { timeout: 10000, maximumAge: 60000 }
        );
    }

    const enterPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            sendClick();
        }
    }

    // 定义一个防抖函数，用于处理 Ctrl + M 的按键组合  
    const handleCtrlM = debounce(() => {
        console.log('Ctrl + M was pressed!');
        micClick();
    }, 500); // 1000 毫秒内多次触发只执行一次   

    useEffect(() => {
        // 聊天滚动条到底部
        chatbotRef.current.scrollTop = chatbotRef.current.scrollHeight + 100;
        // 添加事件监听器  
        const handleKeyDown = (event: KeyboardEvent) => {
            // 检查是否按下了 Ctrl + M
            if (event.ctrlKey && event.key === 'm') {
                handleCtrlM();
            }
        };

        // 绑定事件监听器到 document 或其他适当的 DOM 元素  
        document.addEventListener('keydown', handleKeyDown);
        // 清理函数，用于移除事件监听器  
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
        };
    })

    // Listen for postMessage from parent (Vue ChatView) to fill input
    useEffect(() => {
        const handler = (event: MessageEvent) => {
            if (
                event.data?.type === 'SET_INPUT'
                && typeof event.data?.text === 'string'
                && inputRef.current
            ) {
                inputRef.current.value = event.data.text
                inputRef.current.focus()
            }
        }
        window.addEventListener('message', handler)
        return () => window.removeEventListener('message', handler)
    }, [])

    return (
        <div className="p-2 sm:p-6 justify-between flex flex-col h-full">
            {micRecordAlert ? <ConfirmAlert message={AUDIO_SUPPORT_ALERT} /> : null}
            <div id="messages" ref={chatbotRef} className="flex flex-col space-y-4 p-3 overflow-y-auto no-scrollbar z-10">
                {
                    showChatHistory ?
                        chatRecord.map((chat: ChatMessage, index: number) => (
                            <div className="chat-message" key={index}>
                                <div className={clsx(
                                    "flex items-end",
                                    chat.role == ChatRole.AI ? "" : "justify-end"
                                )}>
                                    <div className={clsx(
                                        "flex flex-col space-y-2 text-xs max-w-xs mx-2",
                                        chat.role == ChatRole.AI ? "order-2 items-start" : "order-1 items-end"
                                    )}>
                                        <div><Markdown className="px-4 py-2 rounded-lg inline-block rounded-bl-none bg-gray-300 text-gray-600">{chat.content}</Markdown></div>
                                    </div>
                                    <img src={chat.role == ChatRole.HUMAN ? "/icons/human_icon.svg" : "/icons/ai_icon.svg"} className="w-6 h-6 rounded-full order-1 self-start" />
                                </div>
                            </div>
                        ))
                        :
                        <></>
                }
                {
                    // 工具调用步骤提示：随回答进度按序追加显示（下一轮对话时清空）
                    agentSteps.length > 0 ?
                        <div className="chat-message">
                            <div className="flex items-end">
                                <div className="flex flex-col space-y-1 text-xs max-w-xs mx-2 order-2 items-start">
                                    {agentSteps.map((step, i) => (
                                        <div key={`${step.tool}-${i}`} className="px-3 py-1.5 rounded-lg rounded-bl-none bg-gray-200/80 text-gray-500">
                                            <span className="inline-flex items-center gap-1.5">
                                                <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                                                <span>🔍 {step.label || step.tool}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                                <img src="/icons/ai_icon.svg" alt="" className="w-6 h-6 rounded-full order-1 self-start" />
                            </div>
                        </div>
                        :
                        <></>
                }
                {
                    // 路线卡片（批次8 B3）：本轮对话规划了路线时渲染
                    // （地图渲染失败自动降级文字路线，见 RouteCard）
                    routeData ?
                        <div className="chat-message">
                            <div className="flex items-end">
                                <div className="flex flex-col space-y-1 text-xs max-w-xs mx-2 order-2 items-start">
                                    <RouteCard route={routeData} />
                                </div>
                                <img src="/icons/ai_icon.svg" alt="" className="w-6 h-6 rounded-full order-1 self-start" />
                            </div>
                        </div>
                        :
                        <></>
                }
            </div>

            <div className="px-4 pt-4 mb-2 sm:mb-0 z-10 w-full">
                <div className="relative flex">
                    <div className="absolute inset-y-0 flex items-center">
                        <button type="button" onClick={micClick} disabled={isProcessing} className={clsx(
                            "inline-flex items-center justify-center rounded-full h-12 w-12 transition duration-500 ease-in-out hover:bg-gray-300 focus:outline-none",
                            micRecording ? "text-red-600" : "text-green-600",
                        )}>
                            {
                                micRecording ?
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor" className="size-6">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 9.563C9 9.252 9.252 9 9.563 9h4.874c.311 0 .563.252.563.563v4.874c0 .311-.252.563-.563.563H9.564A.562.562 0 0 1 9 14.437V9.564Z" />
                                    </svg>
                                    :
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor" className="size-6">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z" />
                                    </svg>
                            }
                        </button>
                        {
                            // 📍 定位按钮（可选，批次8 B3）：仅 secure context
                            // （HTTPS/localhost）下可用，否则隐藏
                            geoSupported ?
                                <button type="button" onClick={locateClick} disabled={isProcessing}
                                    title="填入当前位置（供路线规划起点）"
                                    className="inline-flex items-center justify-center rounded-full h-12 w-12 transition duration-500 ease-in-out hover:bg-gray-300 focus:outline-none text-blue-600">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor" className="size-6">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z" />
                                    </svg>
                                </button>
                                :
                                <></>
                        }
                    </div>
                    <input enterKeyHint="send" type="text" disabled={isProcessing} placeholder="Write your message!" ref={inputRef} onKeyDown={enterPress} className={clsx("w-full focus:outline-none focus:placeholder-gray-400 text-gray-600 placeholder-gray-600 bg-gray-200 rounded-md py-3", geoSupported ? "pl-24" : "pl-12")} />
                    <div className="absolute right-0 items-center inset-y-0 hidden sm:flex">
                        <button type="button" className="inline-flex items-center justify-center rounded-full h-10 w-10 transition duration-500 ease-in-out text-gray-500 hover:bg-gray-300 focus:outline-none">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" className="h-6 w-6 text-gray-600">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path>
                            </svg>
                        </button>
                        <button type="button" onClick={sendClick} disabled={isProcessing} className="inline-flex items-center justify-center rounded-lg px-4 py-3 transition duration-500 ease-in-out text-white bg-blue-500 hover:bg-blue-400 focus:outline-none">
                            <span className="font-bold">Send</span>
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-6 w-6 ml-2 transform rotate-90">
                                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}