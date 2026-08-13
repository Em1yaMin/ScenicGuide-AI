/**
 * 新开会话交互逻辑（纯函数，零依赖，便于单测）：
 * 根据当前对话状态决定「新开会话」按钮点击行为（实机测试问题3）。
 * - 对话进行中（流式 isProcessing / 录音 micRecording）→ ignore：
 *   不响应，避免打断进行中的对话（按钮禁用态）；
 * - 空闲首次点击 → arm：进入轻量确认态（按钮文案变化，等待二次点击），
 *   防误触丢失当前聊天记录；
 * - 确认态再次点击 → confirm：执行新开会话（重新生成 conversation_id、
 *   清空聊天记录）。
 */
export type NewChatState = {
  /** 流式对话进行中（streamingChat 未结束） */
  isProcessing: boolean;
  /** 正在录音 */
  micRecording: boolean;
  /** 已进入轻量确认态（等待二次点击确认） */
  confirming: boolean;
};

export type NewChatAction = "ignore" | "arm" | "confirm";

export function nextNewChatAction(state: NewChatState): NewChatAction {
  if (state.isProcessing || state.micRecording) return "ignore";
  return state.confirming ? "confirm" : "arm";
}
