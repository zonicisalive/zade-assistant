/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

// The page side: native.ts forwards each request from Zade here as window.__zadeControl(name, args), and
// Discord's own stores and actions do the work, so nothing depends on the window being focused or on
// clicking. Only what you ask Zade for runs; Zade reads a message back and asks before sending it.

import { sendMessage } from "@utils/discord";
import definePlugin, { PluginNative } from "@utils/types";
import { findByPropsLazy } from "@webpack";
import {
    ChannelActionCreators, ChannelRouter, ChannelStore, GuildChannelStore, GuildStore, MediaEngineStore, MessageActions,
    MessageStore, ReadStateStore, RelationshipStore, SelectedChannelStore, SelectedGuildStore, UserStore, VoiceStateStore
} from "@webpack/common";

const Native = VencordNative.pluginHelpers.ZadeControl as PluginNative<typeof import("./native")>;
const VoiceActions = findByPropsLazy("toggleSelfMute", "toggleSelfDeaf");
const { selectVoiceChannel } = findByPropsLazy("selectVoiceChannel", "selectChannel");

type Args = Record<string, any>;
type Result = { ok: boolean; [key: string]: any; };

// "💬│general-chat" and "General Chat" are the same channel when spoken
const simple = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ").trim();

// Speech gets names slightly wrong ("dexoto" for dexorto): how alike two names are, 0 to 1.
function likeness(a: string, b: string) {
    const d = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
        let prev = d[0];
        d[0] = i;
        for (let j = 1; j <= b.length; j++) {
            const cur = d[j];
            d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
            prev = cur;
        }
    }
    return 1 - d[b.length] / Math.max(a.length, b.length, 1);
}

// Exact name first, then one starting with what was said, then one containing it, then the closest spelling.
function best<T>(items: T[], names: (item: T) => string[], query: string): T | undefined {
    const q = simple(query);
    if (!q) return;
    for (const test of [(n: string) => n === q, (n: string) => n.startsWith(q), (n: string) => n.includes(q)]) {
        const hit = items.find(i => names(i).some(n => test(simple(n))));
        if (hit) return hit;
    }
    let top: T | undefined, score = 0.7; // below this it's a different name, not a misspelling
    for (const i of items) {
        for (const n of names(i)) {
            const s = likeness(q.replace(/ /g, ""), simple(n).replace(/ /g, ""));
            if (s > score) [top, score] = [i, s];
        }
    }
    return top;
}

function userName(id: string) {
    const u = UserStore.getUser(id);
    return u ? (u as any).globalName || u.username : "someone";
}

function findUser(name: string) {
    const ids = new Set<string>(RelationshipStore.getFriendIDs());
    for (const c of ChannelStore.getSortedPrivateChannels()) (c.recipients ?? []).forEach(r => ids.add(r));
    const users = [...ids].map(id => UserStore.getUser(id)).filter(Boolean);
    return best(users, u => [u.username, (u as any).globalName ?? "", RelationshipStore.getNickname?.(u.id) ?? ""], name);
}

// Channels to search: the server on screen first, then every other server.
function guildChannels(kind: "SELECTABLE" | "VOCAL") {
    const current = SelectedGuildStore.getGuildId();
    const ids = Object.keys(GuildStore.getGuilds()).sort((a, b) => Number(b === current) - Number(a === current));
    return ids.flatMap(id => (GuildChannelStore.getChannels(id)?.[kind] ?? []).map((c: any) => c.channel).filter(Boolean));
}

// Read back so a channel doesn't sound like a person: "the dexorto channel in BITNADE"
function describe(channel: any) {
    const name = channel.name.replace(/^[^a-z0-9]+/i, "");
    return channel.guild_id ? `the ${name} channel in ${GuildStore.getGuild(channel.guild_id)?.name ?? "a server"}` : name;
}

// A person's DM, else a text channel, by what was said ("dexorto", "general", "general in bitnade"). If the
// whole phrase finds nothing ("dexorto user"), its words are tried, longest first.
async function findChat(name: string): Promise<{ id: string; label: string; } | undefined> {
    const whole = await findChatExactly(name);
    if (whole || !name.includes(" ")) return whole;
    for (const word of name.split(/\s+/).filter(w => w.length >= 3).sort((a, b) => b.length - a.length)) {
        const hit = await findChatExactly(word);
        if (hit) return hit;
    }
}

async function findChatExactly(name: string): Promise<{ id: string; label: string; } | undefined> {
    // "the current chat", "this channel", "here": what's open on screen (small models misspell it: "current chant")
    if (/^(?:the )?(?:current|this|here|open|opened|same)\b/.test(simple(name))) {
        const channel = ChannelStore.getChannel(SelectedChannelStore.getChannelId());
        if (!channel) throw new Error("No chat is open in Discord right now.");
        const other = channel.recipients?.length === 1 ? userName(channel.recipients[0]) : null;
        return { id: channel.id, label: other ?? describe(channel) };
    }
    const user = findUser(name);
    if (user) {
        let id = ChannelStore.getDMFromUserId(user.id);
        if (!id) {
            await ChannelActionCreators.openPrivateChannel(user.id);
            id = ChannelStore.getDMFromUserId(user.id);
        }
        if (id) return { id, label: userName(user.id) };
    }
    const [channelName, guildName] = name.split(/ (?:in|on|from) /i);
    let channels = guildChannels("SELECTABLE");
    if (guildName) channels = channels.filter(c => simple(GuildStore.getGuild(c.guild_id)?.name ?? "").includes(simple(guildName)));
    const channel = best(channels, c => [c.name], channelName);
    return channel && { id: channel.id, label: describe(channel) };
}

function voiceStatus() {
    const id = SelectedChannelStore.getVoiceChannelId();
    const channel = id && ChannelStore.getChannel(id);
    return {
        muted: MediaEngineStore.isSelfMute(), deafened: MediaEngineStore.isSelfDeaf(),
        voice: channel ? { channel: describe(channel), people: Object.keys(VoiceStateStore.getVoiceStatesForChannel(id)).map(userName) } : null,
    };
}

const tools: Record<string, (a: Args) => Promise<Result> | Result> = {
    status: () => ({ ok: true, ...voiceStatus() }),

    // on: true / false, or leave it out to toggle
    mute(a) {
        if (a.on === undefined || a.on !== MediaEngineStore.isSelfMute()) VoiceActions.toggleSelfMute();
        return { ok: true, muted: MediaEngineStore.isSelfMute() };
    },
    deafen(a) {
        if (a.on === undefined || a.on !== MediaEngineStore.isSelfDeaf()) VoiceActions.toggleSelfDeaf();
        return { ok: true, deafened: MediaEngineStore.isSelfDeaf() };
    },

    disconnect() {
        if (!SelectedChannelStore.getVoiceChannelId()) return { ok: false, error: "You're not in a voice channel." };
        selectVoiceChannel(null);
        return { ok: true };
    },

    join_voice(a) {
        const channel = best(guildChannels("VOCAL"), c => [c.name], a.name ?? "");
        if (!channel) return { ok: false, error: `I couldn't find a voice channel called ${a.name}.` };
        selectVoiceChannel(channel.id);
        return { ok: true, channel: describe(channel) };
    },

    // A DM call: joining the DM's voice rings them
    call(a) {
        const user = findUser(a.name ?? "");
        const id = user && ChannelStore.getDMFromUserId(user.id);
        if (!id) return { ok: false, error: `I couldn't find ${a.name}.` };
        selectVoiceChannel(id);
        return { ok: true, calling: userName(user.id) };
    },

    async open(a) {
        const chat = await findChat(a.name ?? "");
        if (!chat) return { ok: false, error: `I couldn't find ${a.name} on Discord.` };
        ChannelRouter.transitionToChannel(chat.id);
        return { ok: true, opened: chat.label };
    },

    // Who "name" is, before Zade asks you to confirm a message to them
    async find(a) {
        const chat = await findChat(a.name ?? "");
        return chat ? { ok: true, channel_id: chat.id, label: chat.label } : { ok: false, error: `I couldn't find ${a.name} on Discord.` };
    },

    send(a) {
        if (!a.channel_id || !String(a.text ?? "").trim()) return { ok: false, error: "Nothing to send." };
        sendMessage(a.channel_id, { content: String(a.text) });
        return { ok: true };
    },

    async read(a) {
        const chat = await findChat(a.name ?? "");
        if (!chat) return { ok: false, error: `I couldn't find ${a.name} on Discord.` };
        const count = Math.min(Math.max(Number(a.count) || 5, 1), 20);
        if (!MessageStore.getMessages(chat.id)?._array?.length) await MessageActions.fetchMessages?.({ channelId: chat.id, limit: 50 });
        const messages = (MessageStore.getMessages(chat.id)?._array ?? []).slice(-count)
            .map((m: any) => ({ from: userName(m.author.id), text: m.content || (m.attachments?.length ? "(an attachment)" : "(no text)") }));
        return { ok: true, chat: chat.label, messages };
    },

    // DMs and group DMs with unread messages
    unread() {
        const chats = ChannelStore.getSortedPrivateChannels()
            .map(c => ({ c, count: ReadStateStore.getMentionCount(c.id) }))
            .filter(x => x.count > 0)
            .map(x => ({ from: x.c.name || (x.c.recipients ?? []).map(userName).join(", "), count: x.count }));
        return { ok: true, chats };
    },
};

export default definePlugin({
    name: "ZadeControl",
    description: "Lets the Zade voice assistant control Discord: mute, deafen, voice channels, calls, chats and messages",
    authors: [{ name: "Zonic", id: 0n }],

    start() {
        (window as any).__zadeControl = async (name: string, args: Args) => {
            const tool = tools[name];
            if (!tool) return { ok: false, error: `unknown tool ${name}`, tools: Object.keys(tools) };
            try {
                return await tool(args ?? {});
            } catch (e) {
                return { ok: false, error: e instanceof Error ? e.message : String(e) };
            }
        };
        Native.start();
    },

    stop() {
        Native.stop();
        delete (window as any).__zadeControl;
    },
});
