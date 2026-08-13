/**
 * embedAuth 纯逻辑验证（零依赖、无测试框架，node:assert）。
 * 运行：npm run test:embed-auth
 * （先 tsc 编译到 .test-out/ 再 node 执行，不改动构建产物目录）
 */
import assert from "node:assert";
import { parseEmbedAuth } from "../app/lib/embedAuth";

// 登录用户：auth_token（JWT）+ visitor_id 同时存在（主站 ChatView iframe 参数）
const loggedIn = parseEmbedAuth(
  "?visitor_id=abc123&live2d_model=hiyori&hide_menu=1&auth_token=eyJhbGciOiJIUzI1NiJ9.xyz"
);
assert.strictEqual(loggedIn.authToken, "eyJhbGciOiJIUzI1NiJ9.xyz");
assert.strictEqual(loggedIn.visitorId, "abc123");

// 未登录：只有 visitor_id，无 auth_token（主站未登录时不传该参数）
const anon = parseEmbedAuth("?visitor_id=abc123&hide_menu=1");
assert.strictEqual(anon.authToken, "");
assert.strictEqual(anon.visitorId, "abc123");

// 空 search / 无参数（独立访问 ADH 页面）：全部空串，visitor 兜底
assert.deepStrictEqual(parseEmbedAuth(""), { authToken: "", visitorId: "" });
assert.deepStrictEqual(parseEmbedAuth("?"), { authToken: "", visitorId: "" });

// auth_token 为空值时按未登录处理
const emptyToken = parseEmbedAuth("?visitor_id=abc123&auth_token=");
assert.strictEqual(emptyToken.authToken, "");

// visitor_id 缺失但 auth_token 存在（不应发生，但解析不应崩溃）
const noVisitor = parseEmbedAuth("?auth_token=yyy");
assert.strictEqual(noVisitor.authToken, "yyy");
assert.strictEqual(noVisitor.visitorId, "");

console.log("✓ embedAuth 解析逻辑验证通过（8 断言）");
