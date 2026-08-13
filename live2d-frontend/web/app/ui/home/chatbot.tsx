'use client';
// import Image from "next/image";
import clsx from "clsx";
import { debounce } from 'lodash';
import { useEffect, useRef, useState } from "react";
import { useChatRecordStore, ChatRole, ChatMessage, ChatMessageType, useAgentEngineSettingsStore, useAgentModeStore, useMuteStore, useInteractionModeStore, InteractionMode, useAudioAutoStopStore } from "@/app/lib/store";
import { ConfirmAlert } from "@/app/ui/common/alert";
import { AUDIO_SUPPORT_ALERT } from "@/app/lib/constants";
import { Comm } from "@/app/lib/comm";
import { CharacterManager } from "@/app/lib/character";
import { getEmbedAuth } from "@/app/lib/embedAuth";
import { nextNewChatAction } from "@/app/lib/newChat";
import Markdown from 'react-markdown';
import RouteCard from "@/app/ui/home/routeCard";

let mediaStream: MediaStream | null = null;
let mediaRecorder: MediaRecorder | null = null;
let recordedChunks: Blob[] = [];
let isRecording: boolean = false;
// TTS 会话代际计数：新开会话/新一轮对话开始时递增，使旧会话仍在后台
// 合成的 TTS 入队全部作废（丢弃不入队，不跨会话/跨轮播放旧语音）
let ttsSession = 0;

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
    const { chatRecord, addChatRecord, clearChatRecord } = useChatRecordStore();
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
    // 当前 AI 流式文本片段（按 step/route 事件边界切分）：实时逐字渲染，
    // step/route 回调或流完成时固化进 chatRecord 历史（文本片段/步骤/路线
    // 各自独立气泡按发生顺序入列，多轮保留不清空）
    const [streamingText, setStreamingText] = useState("");
    // 浏览器定位可用性（需 secure context：HTTPS 或 localhost；演示环境
    // 经局域网 IP 访问不可用则隐藏 📍 按钮）
    const [geoSupported, setGeoSupported] = useState(false);
    // 新开会话轻量确认态（两段式：首次点击进入确认态，再次点击才执行，
    // 防误触丢失当前聊天记录；3 秒未确认自动复位）
    const [confirmingNewChat, setConfirmingNewChat] = useState(false);
    const newChatConfirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // 新会话 id 在途标记（取回后清除）：期间禁用输入/发送/麦克风/新开按钮，
    // 保证新会话的第一条消息一定携带新 conversation_id（防取回前发送导致
    // 会话分裂——旧 id 已置空、后端对空 id 每次新建会话）
    const [newChatPending, setNewChatPending] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const chatbotRef = useRef<HTMLDivElement>(null);
    
    useEffect(() => {
        let newSettings: {[key: string]: string} = {}
        if (agentEngine in agentSettings) {
            for (let setting of agentSettings[agentEngine]){
                newSettings[setting.NAME] = setting.DEFAULT;
            }
            // 嵌入模式身份透传（问题4 修复，长期记忆根因）：主站 5173 ChatView
            // 以 auth_token URL 参数注入登录用户/游客 JWT；覆盖 yaml 默认空 token，
            // 对话/建会话请求即携带 LLM_API_TOKEN → ADH 后端 Bearer 透传 → 主后端
            // resolve_dialogue_user 解析真实身份（对话落库 user=用户 id、长期记忆
            // 按 user 隔离生效）。未登录（无参数）不注入：行为与现状一致（visitor 兜底）
            const { authToken } = getEmbedAuth();
            if (authToken) {
                newSettings["LLM_API_TOKEN"] = authToken;
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
        // 新一轮对话：清空上一轮流式文本；聊天记录 chatRecord 保留
        // （步骤气泡/路线卡片/文本片段均已按序固化进历史，多轮不清空）
        setStreamingText("");
        addChatRecord({ role: ChatRole.HUMAN, content: message });
        // 请求AI
        // 当前文本片段（按 step/route 事件边界切分）：token 回调实时累积并
        // 渲染（逐字可见）；step/route 回调到达时先把当前片段固化（append 为
        // text 消息）再 append 该 step/route，开始新片段；流完成时固化最后片段
        // （#70-5b 回归修复：流中断/异常时 comm.ts 仍会触发 done 回调，此处
        // 固化已收到的部分文本并恢复输入——不再出现「工具调用后文字气泡不显示」
        // 或输入框永久禁用；text/step/route 保持发生顺序，多轮保留不清空）
        let fragment = "";
        // 上一条已提交的步骤（连续重复步骤去重用；文本片段固化后重置）
        let lastStep: { tool: string; label: string } | null = null;
        const flushFragment = () => {
            if (fragment) {
                addChatRecord({ role: ChatRole.AI, content: fragment });
                fragment = "";
                lastStep = null;
                setStreamingText("");
            }
        };
        let audioText = "";
        // 保证顺序执行
        let audioRecorderIndex = 0;
        let audioRecorderDict = new Map<number, ArrayBuffer | null>();
        if (audioAutoStop) {
            CharacterManager.getInstance().clearAudioQueue();
            // 新一轮对话开始：递增代际，作废上一轮仍在后台合成的 TTS 入队
            // （与 clearAudioQueue 同一语义：打断未结束的旧语音）
            ttsSession++;
        }
        // 本轮 TTS 入队代际快照（audioAutoStop 关闭时保持原代际，
        // 旧语音不打断——与「新对话是否打断未结束对话=否」语义一致）
        const myTtsSession = ttsSession;
        // 单句 TTS 入队（#77 修复 + #70-5b 回归修复）：
        // - 后端 qwenTTS WebSocket 偶发中断会导致单句合成失败/悬挂，失败/超时
        //   句记为 null 出队跳过，后续句子照常播放（不卡死队列）；
        // - 旧逻辑按标点断句后所有句子立即并发请求——长回答十几句同时打到
        //   DashScope 触发限流（Throttling.RateQuota）+ WebSocket 连接风暴
        //   （高频「connection is not established」），实际大量句子被跳过。
        //   现改为 promise 链串行化：前一句合成完成后再发起下一句（与后端
        //   qwenTTS 串行化配合，同刻最多一个在途连接）；超时兜底（45s，长于
        //   后端停滞检测 20s × 重试 + 退避，正常合成不受影响），后端彻底悬挂
        //   时本句最多等 45s 即继续；单句失败不连跳后续（链式 catch 记 null）。
        const ttsSegmentTimeoutMs = 45000;
        let ttsChain: Promise<void> = Promise.resolve();
        const enqueueTTS = (index: number, sentence: string) => {
            console.log("tts:", sentence);
            ttsChain = ttsChain.then(() => {
                const ttsPromise = Comm.getInstance().tts(sentence, settings);
                const timeoutPromise = new Promise<null>((resolve) => {
                    setTimeout(() => resolve(null), ttsSegmentTimeoutMs);
                });
                return Promise.race([ttsPromise, timeoutPromise]);
            }).then((data: ArrayBuffer | null) => {
                // 代际校验：新会话/新一轮已开始后，旧链仍在后台合成的音频
                // 直接丢弃不入队（不跨会话/跨轮播放旧语音）
                if (ttsSession !== myTtsSession) return;
                // 成功/失败/超时都落记录：失败为 null，出队推进不卡后续句子
                audioRecorderDict.set(index, data || null);
                while (true) {
                    if (!audioRecorderDict.has(audioRecorderIndex)) break;
                    const audio = audioRecorderDict.get(audioRecorderIndex);
                    if (audio) {
                        CharacterManager.getInstance().pushAudioQueue(audio);
                    }
                    audioRecorderIndex++;
                }
            }).catch(() => {
                // 单句异常兜底：记 null 跳过，不影响后续句子（链路不断）
                audioRecorderDict.set(index, null);
            });
        };
        Comm.getInstance().streamingChat(message, agentEngine, conversationId, settings, (index: number, data: string) => {
            fragment += data;
            // AI 流式文本：当前片段实时渲染（用户可见逐字），切分时固化进历史
            setStreamingText(fragment);
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
                    enqueueTTS(index, firstPart);
                    audioText = secondPart;
                } else {
                    audioRecorderDict.set(index, null)
                }
            }
        }, (index: number) => {
            // 处理剩余tts（流结束时的最后一段文本；失败/超时同样跳过不阻塞）
            if (!mute && audioText) {
                enqueueTTS(index, audioText);
            }
            // 流完成：固化最后文本片段（若为空说明前面均已按序提交），
            // 多轮顺序：用户 → [文本片段1][步骤][文本片段2]... → AI
            flushFragment();
            setIsProcessing(false);
        }, (step: { tool: string; label: string }) => {
            // 工具调用步骤：先固化当前文本片段，再把该步骤作为独立气泡按序
            // 提交进聊天记录（多轮保留，展示对话整体过程）；连续重复步骤去重
            console.log("agent step: ", step);
            flushFragment();
            if (lastStep && lastStep.tool === step.tool && lastStep.label === step.label) {
                return;
            }
            lastStep = step;
            addChatRecord({ role: ChatRole.AI, type: ChatMessageType.STEP, step, content: step.label || step.tool });
        }, (route: any) => {
            // 路线帧（批次8 B3）：先固化当前文本片段，再把路线卡片作为独立
            // 消息按序提交进聊天记录（多轮保留，新问题不再清空）
            console.log("route data: ", route);
            flushFragment();
            if (route) {
                addChatRecord({ role: ChatRole.AI, type: ChatMessageType.ROUTE, route, content: "" });
            }
        });
    }


    // 新开会话（实机测试问题3）：重新生成 conversation_id（全新会话，
    // 短期记忆不延续），清空聊天记录/流式文本并恢复初始欢迎状态。
    // - 进行中（流式 isProcessing / 录音 micRecording）时不响应（按钮禁用），
    //   避免打断进行中的对话；
    // - 两段式轻量确认：首次点击进入确认态（按钮文案变化，3 秒未确认自动
    //   复位），再次点击才执行——防误触丢失当前聊天记录；
    // - 执行时先置空 conversation_id 再异步取新 id；取回前输入/发送/麦克风
    //   禁用（newChatPending 在途标记），保证新会话第一条消息一定携带新 id
    //   （防会话分裂：取回前发送会以空 id 在后端另建会话）；取回失败保持
    //   空 id——后端对空 conversation_id 自动新建会话，对话仍可用
    //   （仅跨轮短期记忆不延续）；
    // - 同步作废/清空旧会话 TTS（ttsSession 代际 + clearAudioQueue），
    //   旧会话语音不再继续播放
    const doNewChat = () => {
        ttsSession++;
        CharacterManager.getInstance().clearAudioQueue();
        setConversationId("");
        setStreamingText("");
        clearChatRecord();
        setNewChatPending(true);
        Comm.getInstance().getConversionId(agentEngine, settings).then((id) => {
            console.log("new conversationId: ", id);
            setConversationId(id);
        }).finally(() => {
            // 取回（含失败：保持空 id，后端对空 conversation_id 自动新建）
            // 后恢复输入
            setNewChatPending(false);
        });
    };

    const newChatClick = () => {
        if (newChatPending) return;
        const action = nextNewChatAction({
            isProcessing,
            micRecording,
            confirming: confirmingNewChat,
        });
        if (action === "ignore") return;
        if (action === "arm") {
            // 首次点击：进入确认态，3 秒未确认自动复位（防误触）
            setConfirmingNewChat(true);
            if (newChatConfirmTimerRef.current) clearTimeout(newChatConfirmTimerRef.current);
            newChatConfirmTimerRef.current = setTimeout(() => setConfirmingNewChat(false), 3000);
            return;
        }
        // 确认态再次点击：执行
        if (newChatConfirmTimerRef.current) clearTimeout(newChatConfirmTimerRef.current);
        newChatConfirmTimerRef.current = null;
        setConfirmingNewChat(false);
        doNewChat();
    };

    const micClick = () => {
        if (isProcessing || newChatPending) return;
        
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
        if (newChatPending) return;
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

    // 卸载时清理新开会话确认定时器（防组件卸载后 setState）
    useEffect(() => {
        return () => {
            if (newChatConfirmTimerRef.current) clearTimeout(newChatConfirmTimerRef.current);
        };
    }, []);

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
            {/* 新开会话入口（实机测试问题3）：始终可见（嵌入 hide_menu=1 与独立
                模式行为一致）；对话进行中（流式/录音）禁用不响应；首次点击进入
                确认态（文案变化，3 秒未确认自动复位），再次点击执行新开会话 */}
            <div className="flex items-center justify-end px-1 z-10">
                <button
                    type="button"
                    onClick={newChatClick}
                    disabled={isProcessing || micRecording || newChatPending}
                    title={
                        newChatPending
                            ? "正在创建新会话…"
                            : isProcessing || micRecording
                                ? "对话进行中，暂不能新开"
                                : confirmingNewChat
                                    ? "再次点击确认新开会话（清空聊天记录）"
                                    : "新开会话（清空聊天记录，短期记忆不延续）"
                    }
                    className={clsx(
                        "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition duration-300 ease-in-out focus:outline-none",
                        confirmingNewChat
                            ? "bg-red-100 text-red-600 hover:bg-red-200"
                            : "text-gray-500 hover:bg-gray-200 hover:text-gray-700",
                        (isProcessing || micRecording || newChatPending) && "opacity-40 cursor-not-allowed"
                    )}
                >
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor" className="size-3.5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                    </svg>
                    {confirmingNewChat ? "再次点击确认新开" : "新开会话"}
                </button>
            </div>
            <div id="messages" ref={chatbotRef} className="flex flex-col space-y-4 p-3 overflow-y-auto no-scrollbar z-10">
                {
                    showChatHistory ?
                        chatRecord.map((chat: ChatMessage, index: number) => {
                            // step 消息：工具调用步骤小气泡（保留既有样式，多轮保留）
                            if (chat.type === ChatMessageType.STEP) {
                                return (
                                    <div className="chat-message" key={index}>
                                        <div className="flex items-end">
                                            <div className="flex flex-col space-y-1 text-xs max-w-xs mx-2 order-2 items-start">
                                                <div className="px-3 py-1.5 rounded-lg rounded-bl-none bg-gray-200/80 text-gray-500">
                                                    <span className="inline-flex items-center gap-1.5">
                                                        <span className="w-2 h-2 rounded-full bg-blue-500" />
                                                        <span>🔍 {chat.step?.label || chat.step?.tool || chat.content}</span>
                                                    </span>
                                                </div>
                                            </div>
                                            <img src="/icons/ai_icon.svg" alt="" className="w-6 h-6 rounded-full order-1 self-start" />
                                        </div>
                                    </div>
                                );
                            }
                            // route 消息：路线卡片（地图失败自动降级文字路线，见 RouteCard）
                            if (chat.type === ChatMessageType.ROUTE && chat.route) {
                                return (
                                    <div className="chat-message" key={index}>
                                        <div className="flex items-end">
                                            <div className="flex flex-col space-y-1 text-xs max-w-xs mx-2 order-2 items-start">
                                                <RouteCard route={chat.route} />
                                            </div>
                                            <img src="/icons/ai_icon.svg" alt="" className="w-6 h-6 rounded-full order-1 self-start" />
                                        </div>
                                    </div>
                                );
                            }
                            // text（缺省 type 即 text）：Markdown 气泡
                            return (
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
                            );
                        })
                        :
                        <></>
                }
                {
                    // AI 流式文本气泡：当前文本片段逐字填充于此（位于已固化的
                    // 历史消息之后，顺序正确）；step/route/流完成时固化进 chatRecord
                    streamingText ?
                        <div className="chat-message">
                            <div className="flex items-end">
                                <div className="flex flex-col space-y-2 text-xs max-w-xs mx-2 order-2 items-start">
                                    <div><Markdown className="px-4 py-2 rounded-lg inline-block rounded-bl-none bg-gray-300 text-gray-600">{streamingText}</Markdown></div>
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
                    <input enterKeyHint="send" type="text" disabled={isProcessing || newChatPending} placeholder="Write your message!" ref={inputRef} onKeyDown={enterPress} className={clsx("w-full focus:outline-none focus:placeholder-gray-400 text-gray-600 placeholder-gray-600 bg-gray-200 rounded-md py-3", geoSupported ? "pl-24" : "pl-12")} />
                    <div className="absolute right-0 items-center inset-y-0 hidden sm:flex">
                        <button type="button" className="inline-flex items-center justify-center rounded-full h-10 w-10 transition duration-500 ease-in-out text-gray-500 hover:bg-gray-300 focus:outline-none">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" className="h-6 w-6 text-gray-600">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path>
                            </svg>
                        </button>
                        <button type="button" onClick={sendClick} disabled={isProcessing || newChatPending} className="inline-flex items-center justify-center rounded-lg px-4 py-3 transition duration-500 ease-in-out text-white bg-blue-500 hover:bg-blue-400 focus:outline-none">
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