/**
 * 新开会话交互逻辑纯函数验证（零依赖、无测试框架，node:assert）。
 * 运行：npm run test:new-chat
 * （先 tsc 编译到 .test-out/ 再 node 执行，不改动构建产物目录）
 */
import assert from "node:assert";
import { nextNewChatAction, NewChatState } from "../app/lib/newChat";

function s(partial: Partial<NewChatState>): NewChatState {
  return { isProcessing: false, micRecording: false, confirming: false, ...partial };
}

// 对话进行中（流式/录音）不响应——避免打断进行中的对话
assert.strictEqual(nextNewChatAction(s({ isProcessing: true })), "ignore");
assert.strictEqual(nextNewChatAction(s({ micRecording: true })), "ignore");
assert.strictEqual(nextNewChatAction(s({ isProcessing: true, micRecording: true })), "ignore");
// 进行中即使已进入确认态也不执行（确认态在对话结束后失效）
assert.strictEqual(nextNewChatAction(s({ isProcessing: true, confirming: true })), "ignore");
assert.strictEqual(nextNewChatAction(s({ micRecording: true, confirming: true })), "ignore");

// 空闲首次点击：进入确认态（防误触丢失当前聊天记录）
assert.strictEqual(nextNewChatAction(s({})), "arm");

// 确认态再次点击：执行新开会话
assert.strictEqual(nextNewChatAction(s({ confirming: true })), "confirm");

console.log("✓ newChat 交互逻辑验证通过（8 断言）");
