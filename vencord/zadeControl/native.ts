/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

// Runs in Discord's main process: a small JSON API on 127.0.0.1 for Zade. Each request is passed to the page
// (index.ts), where Discord's own stores and actions do the work. A secret token in
// ~/.config/zade/discord-token (only you can read it) guards it: no token, no access. Browsers can't use it
// either (they can't send the Authorization header to a server that doesn't answer CORS).

import { randomBytes, timingSafeEqual } from "crypto";
import { BrowserWindow, IpcMainInvokeEvent } from "electron";
import { existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "fs";
import { createServer, Server } from "http";
import { homedir } from "os";
import { join } from "path";

const PORT = 47823;
const TOKEN_FILE = join(homedir(), ".config", "zade", "discord-token");
let server: Server | null = null;

function token() {
    if (!existsSync(TOKEN_FILE)) {
        mkdirSync(join(homedir(), ".config", "zade"), { recursive: true });
        writeFileSync(TOKEN_FILE, randomBytes(24).toString("hex") + "\n", { mode: 0o600 });
    }
    return readFileSync(TOKEN_FILE, "utf8").trim();
}

function mainWindow() {
    return BrowserWindow.getAllWindows().find(w => !w.webContents.isDestroyed() && /discord\.com/.test(w.webContents.getURL()));
}

export function start(_: IpcMainInvokeEvent) {
    if (server) return;
    const secret = Buffer.from(token());
    server = createServer((req, res) => {
        const reply = (status: number, body: unknown) => {
            res.writeHead(status, { "Content-Type": "application/json" });
            res.end(JSON.stringify(body));
        };
        const given = Buffer.from((req.headers.authorization ?? "").replace(/^Bearer /, ""));
        if (given.length !== secret.length || !timingSafeEqual(given, secret)) return reply(401, { ok: false, error: "bad token" });
        if (req.method !== "POST" || req.url !== "/tool") return reply(404, { ok: false, error: "POST /tool" });
        let body = "";
        req.on("data", chunk => { body += chunk; if (body.length > 65536) req.destroy(); });
        req.on("end", async () => {
            const win = mainWindow();
            if (!win) return reply(503, { ok: false, error: "Discord isn't open" });
            try {
                const { name, args } = JSON.parse(body || "{}");
                // JSON.stringify makes the call safe to build as code: the page gets plain data, never script
                const result = await win.webContents.executeJavaScript(
                    `window.__zadeControl(${JSON.stringify(String(name))}, ${JSON.stringify(args ?? {})})`);
                reply(200, result);
            } catch (e) {
                reply(500, { ok: false, error: String(e) });
            }
        });
    });
    server.on("error", e => console.error("[ZadeControl]", e));
    server.listen(PORT, "127.0.0.1");
}

// A picture for Zade to send (a screenshot), as base64. Only image files inside ~/Pictures, up to 25 MB:
// the page can ask for a file, so this is kept from reading anything else.
export function readPicture(_: IpcMainInvokeEvent, path: string) {
    const real = realpathSync(path);
    if (!real.startsWith(join(homedir(), "Pictures") + "/") || !/\.(png|jpe?g|webp|gif)$/i.test(real)) throw new Error("not a picture in ~/Pictures");
    if (statSync(real).size > 25 * 1024 * 1024) throw new Error("that picture is over 25 MB");
    return readFileSync(real).toString("base64");
}

export function stop(_: IpcMainInvokeEvent) {
    server?.close();
    server = null;
}
