/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

// Runs in Discord's main process: a small JSON API for Zade on a Unix socket in $XDG_RUNTIME_DIR/zade. Only
// you can open it: unlike a TCP port, no other program can take it over while Discord is closed and collect
// Zade's token. Each request is passed to the page (index.ts), where Discord's own stores and actions do the
// work. A secret token in ~/.config/zade/discord-token (only you can read it) guards it too.

import { randomBytes, timingSafeEqual } from "crypto";
import { IpcMainInvokeEvent } from "electron";
import { chmodSync, mkdirSync, readFileSync, realpathSync, statSync, unlinkSync, writeFileSync } from "fs";
import { createServer, Server } from "http";
import { homedir } from "os";
import { dirname, join } from "path";

const SOCKET = join(process.env.XDG_RUNTIME_DIR || "/tmp", "zade", "discord.sock");
const TOKEN_FILE = join(homedir(), ".config", "zade", "discord-token");
let server: Server | null = null;

// Made once, readable only by you. A short or empty one (Discord closed while writing it) is replaced: an
// empty token would let every request in.
function token() {
    mkdirSync(dirname(TOKEN_FILE), { recursive: true });
    try {
        writeFileSync(TOKEN_FILE, randomBytes(24).toString("hex") + "\n", { mode: 0o600, flag: "wx" });
    } catch { } // it exists already
    chmodSync(TOKEN_FILE, 0o600);
    let secret = readFileSync(TOKEN_FILE, "utf8").trim();
    if (secret.length < 32) {
        secret = randomBytes(24).toString("hex");
        writeFileSync(TOKEN_FILE, secret + "\n", { mode: 0o600 });
    }
    return secret;
}

export function start(event: IpcMainInvokeEvent) {
    if (server) return;
    const page = event.sender; // the main window, where index.ts runs (a popout window has no __zadeControl)
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
            if (page.isDestroyed()) return reply(503, { ok: false, error: "Discord isn't open" });
            try {
                const { name, args } = JSON.parse(body || "{}");
                // JSON.stringify makes the call safe to build as code: the page gets plain data, never script
                const result = await page.executeJavaScript(
                    `window.__zadeControl(${JSON.stringify(String(name))}, ${JSON.stringify(args ?? {})})`);
                reply(200, result);
            } catch (e) {
                reply(500, { ok: false, error: String(e) });
            }
        });
    });
    server.on("error", e => {
        console.error("[ZadeControl] Zade can't reach Discord: no socket at", SOCKET, e);
        server = null;
    });
    mkdirSync(dirname(SOCKET), { recursive: true, mode: 0o700 });
    try { unlinkSync(SOCKET); } catch { } // left by a Discord that didn't close cleanly
    server.listen(SOCKET, () => chmodSync(SOCKET, 0o600));
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
    try { unlinkSync(SOCKET); } catch { }
}
