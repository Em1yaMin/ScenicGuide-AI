import * as API from '@/app/lib/api';

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    // 读取完成后的处理  
    reader.onloadend = function () {
      // 读取成功，result属性中将包含一个data: URL格式的字符串  
      // 通过split(',')来提取Base64编码的字符串  
      const base64data = reader.result as string;
      const base64String = base64data.split(',')[1];
      resolve(base64String);
    };

    // 错误处理  
    reader.onerror = function (error) {
      reject(error);
    };

    // 读取Blob对象，并作为data URL返回  
    reader.readAsDataURL(blob);
  });
}

function base64ToArrayBuffer(base64String: string): ArrayBuffer {
  const binaryString = window.atob(base64String);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes.buffer;
}

export class Comm {
  // 单例
  public static getInstance(): Comm {
    if (!this._instance) {
      this._instance = new Comm();
    }

    return this._instance;
  }

  // public async getHeartbeat(): Promise<boolean> {
  //   return API.common_heatbeat_api().then(response => {
  //     if (response.data == 1) {
  //       return true;
  //     } else {
  //       return false;
  //     }
  //   }).catch(error => {
  //     console.error(error);
  //     return false;
  //   })
  // }

  public async getAgentsList(): Promise<string[]> {
    return API.agents_list_api().then(response => {
      return response.data;
    }).catch(error => {
      console.error(error);
      return []
    })
  }

  public async getDefaultAgent(): Promise<string> {
    return API.agent_default_api().then(response => {
      return response.data;
    }).catch(error => {
      console.error(error);
      return ""
    })
  }

  public async getAgentSettings(agent: string): Promise<{[key: string]: string}[]> {
    return API.agent_settings_api(agent).then(response => {
      return response.data;
    }).catch(error => {
      console.error(error);
      return {}
    })
  }

  public async getConversionId(
    agent: string = "default",
    settings: { [key: string]: string } = {},
  ): Promise<string> {
    return API.agent_conversationid_api(agent, settings).then(response => {
      return response.data;
    }).catch(error => {
      console.error(error);
      return ""
    })
  }

  public async streamingChat(
    data: string,
    engine: string = "default",
    conversationId: string = "",
    settings: { [key: string]: string } = {},
    callbackProcessing: (index: number, data: string) => void,
    callbackEnd: (index: number) => void,
    callbackStep?: (step: { tool: string; label: string }) => void,
    callbackRoute?: (route: any) => void
  ): Promise<void> {
    // 流式对话结束回调兜底：无论正常 EOF 还是中途异常（网络错误/服务端
    // 中断），都必须触发 callbackEnd——前端据此固化已收到的部分文本并恢复
    // 输入（不永久卡死）。修复「工具调用后文字气泡不显示」（#70-5b）：
    // 旧逻辑 catch 吞掉异常后直接返回，callbackEnd 永不触发，isProcessing
    // 保持 true → 输入框永远禁用，且已流出的文本不固化进聊天记录。
    let reader: ReadableStreamDefaultReader | null = null;
    try {
      reader = await API.agent_infer_streaming_api(data, engine, conversationId, settings);
      const decoder = new TextDecoder("utf-8");
      // 步骤/路线帧协议（agentApi 编码）：\x02{json}\x03。
      // json 为步骤帧（{tool, label}）或路线帧（{type: "route", data}，
      // 批次8 B3——后端 done 事件 route_data 原样透传）。
      // 回答文本仍按原始文本流透出；帧可能被 fetch 分块切碎，需缓冲拼接。
      const STEP_START = "\u0002";
      const STEP_END = "\u0003";
      let buffer = "";
      let index = 0;
      const emitText = (text: string) => {
        if (!text) return;
        callbackProcessing(index, text);
        index++;
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          callbackEnd(index);
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        // 解析缓冲：完整步骤帧提取给 callbackStep，其余文本按序透出
        while (true) {
          const start = buffer.indexOf(STEP_START);
          if (start === -1) {
            // 无帧起始：整段按文本处理
            emitText(buffer);
            buffer = "";
            break;
          }
          if (start > 0) {
            emitText(buffer.slice(0, start));
            buffer = buffer.slice(start);
          }
          const end = buffer.indexOf(STEP_END, 1);
          if (end === -1) {
            // 帧未完整（被切碎），保留等待后续数据
            break;
          }
          const raw = buffer.slice(1, end);
          buffer = buffer.slice(end + 1);
          try {
            const stepData = JSON.parse(raw);
            if (!stepData || typeof stepData !== "object") continue;
            if (stepData.type === "route") {
              // 路线帧：plan_route 工具结果（成功/错误信封），透给调用方渲染
              if (callbackRoute) callbackRoute(stepData.data);
              continue;
            }
            if (callbackStep) {
              callbackStep({
                tool: stepData.tool || "",
                label: stepData.label || stepData.tool || "",
              });
            }
          } catch (error) {
            console.error("[Comm] step frame parse error:", error);
          }
        }
      }
    } catch (error) {
      // 流中断（网络错误/服务端异常/连接被切断）：已收到的部分文本由
      // callbackEnd 固化（flushFragment），输入框恢复可用；不重新抛错
      // （旧行为即吞错，保持兼容）。
      console.error(error);
      if (typeof callbackEnd === "function") {
        callbackEnd(0);
      }
    } finally {
      try {
        reader?.releaseLock();
      } catch (e) {
        // 忽略释放异常（流已损坏时 releaseLock 可能抛错）
      }
    }
  }

  public async asr(
    data: Blob,
    settings: { [key: string]: string } = {},
    engine: string = "default",
    format: string = "wav",
    sampleRate: Number = 16000,
    sampleWidth: Number = 2
  ): Promise<string> {
    return blobToBase64(data).then(base64str => {
      if (base64str == null) {
        return "";
      }
      return API.asr_infer_api(base64str, engine, format, sampleRate, sampleWidth, settings).then(response => {
        return response.data;
      }).catch(error => {
        console.error(error);
        return ""
      })
    }).catch(error => {
      console.error(error);
      return "";
    })
  }

  public async tts(
    data: string,
    settings: { [key: string]: string } = {},
    engine: string = "default"
  ): Promise<ArrayBuffer> {
    // 处理tts输入数据
    const filterData = (data: string): string => {
      // 过滤所有的emoji
      const regex = /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{2700}-\u{27BF}\u{1F1E0}-\u{1F1FF}]/gu;  
      data = data.replace(regex, '');
      return data
    }
    data = filterData(data);
    if (data.length == 0) {
      return null;
    }
    return API.tts_infer_api(data, engine, settings).then(response => {
      if (response.data == null) {
        return null;
      }
      return base64ToArrayBuffer(response.data);
    }).catch(error => {
      console.error(error)
      return null;
    })
  }

  private static _instance: Comm;
}