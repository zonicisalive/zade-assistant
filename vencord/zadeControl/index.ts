/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 Vendicated and contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

// The page side: native.ts forwards each request from Zade here as window.__zadeControl(name, args), and
// Discord's own stores and actions do the work, so nothing depends on the window being focused or on
// clicking. Only what you ask Zade for runs; Zade reads a message back and asks before sending it.

import { getUserSettingLazy } from "@api/UserSettings";
import { sendMessage } from "@utils/discord";
import definePlugin, { PluginNative } from "@utils/types";
import { findByPropsLazy } from "@webpack";
import {
    ChannelActionCreators, ChannelRouter, ChannelStore, GuildChannelStore, GuildStore, MediaEngineStore, MessageActions,
    MessageStore, ReadStateStore, RelationshipStore, RestAPI, SelectedChannelStore, SelectedGuildStore, UserStore,
    VoiceStateStore
} from "@webpack/common";

const Native = VencordNative.pluginHelpers.ZadeControl as PluginNative<typeof import("./native")>;
const VoiceActions = findByPropsLazy("toggleSelfMute", "toggleSelfDeaf");
const { selectVoiceChannel } = findByPropsLazy("selectVoiceChannel", "selectChannel");
const StatusSetting = getUserSettingLazy<string>("status", "status")!;
const STATUSES = ["online", "idle", "dnd", "invisible"];

type Args = Record<string, any>;

// New DMs, mentions and incoming calls, for Zade to announce: it collects them with the events tool.
// Nothing is queued for a chat you have open in front of you, or while your status is Do Not Disturb.
const events: Args[] = [];
function queue(event: Args) {
    if (StatusSetting.getSetting() === "dnd") return;
    events.push({ ...event, at: Date.now() });
    events.splice(0, Math.max(0, events.length - 20));
}
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
// The servers "server" names: an exact match with the same capitals ("BITNADE", not "bitnade") first, then
// any-case matches. More than one left means it's ambiguous.
function servers(server: string) {
    const guilds = Object.values(GuildStore.getGuilds()) as any[];
    const words = server.replace(/\b(?:the|server|guild)\b/gi, " ").trim();
    for (const test of [(n: string) => n === words, (n: string) => simple(n) === simple(words), (n: string) => simple(n).includes(simple(words))]) {
        const hits = guilds.filter(g => test(g.name));
        if (hits.length) return hits;
    }
    return [];
}

function describe(channel: any) {
    const name = channel.name.replace(/^[^a-z0-9]+/i, "");
    return channel.guild_id ? `the ${name} channel in ${GuildStore.getGuild(channel.guild_id)?.name ?? "a server"}` : name;
}

// A person's DM, else a text channel, by what was said ("dexorto", "general", "general in bitnade"). If the
// whole phrase finds nothing ("dexorto user"), its words are tried, longest first.
// anywhere: search channels in every server (to open or read one). For sending, only the server on screen, or
// the one named, is searched: a message must never go to a same-named channel in some random server.
async function findChat(name: string, anywhere = true): Promise<{ id: string; label: string; } | undefined> {
    const whole = await findChatExactly(name, anywhere);
    if (whole || !name.includes(" ")) return whole;
    for (const word of name.split(/\s+/).filter(w => w.length >= 3).sort((a, b) => b.length - a.length)) {
        const hit = await findChatExactly(word, anywhere);
        if (hit) return hit;
    }
}

async function findChatExactly(name: string, anywhere: boolean): Promise<{ id: string; label: string; } | undefined> {
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
    if (guildName) {
        const ids = new Set(servers(guildName).map(g => g.id));
        channels = channels.filter(c => ids.has(c.guild_id));
    }
    else if (!anywhere) channels = channels.filter(c => c.guild_id === SelectedGuildStore.getGuildId());
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

async function messagesIn(channelId: string) {
    if (!MessageStore.getMessages(channelId)?._array?.length) await MessageActions.fetchMessages?.({ channelId, limit: 50 });
    return (MessageStore.getMessages(channelId)?._array ?? []) as any[];
}

// The message a reaction or reply is for, in the named chat (or the one open): the latest from someone else, or
// with mine, my own latest (to edit or delete).
async function target(a: Args, mine = false) {
    const chat = a.name ? await findChat(a.name, false) : await findChat("the current chat", false);
    if (!chat) throw new Error(`I couldn't find ${a.name} on Discord.`);
    const me = UserStore.getCurrentUser().id;
    const message = (await messagesIn(chat.id)).filter(m => (m.author.id === me) === mine).at(-1);
    if (!message) throw new Error(mine ? `You have no message in ${chat.label}.` : `There's no message to answer in ${chat.label}.`);
    return { chat, message, author: userName(message.author.id), text: message.content || "(an attachment)" };
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

    // name: "staff-vc", or "staff-vc in BITNADE" (only that server)
    join_voice(a) {
        const [channelName, server] = String(a.name ?? "").split(/ (?:in|on|from) /i);
        let channels = guildChannels("VOCAL");
        if (server) {
            const guilds = servers(server);
            if (!guilds.length) return { ok: false, error: `I couldn't find a server called ${server}.` };
            const withIt = guilds.filter(g => best(channels.filter(c => c.guild_id === g.id), c => [c.name], channelName));
            if (withIt.length > 1) return { ok: false, error: `More than one server matches ${server}: ${withIt.map(g => g.name).join(", ")}. Say its exact name.` };
            channels = channels.filter(c => c.guild_id === (withIt[0] ?? guilds[0]).id);
        }
        if (!server) { // no server named: the one on screen if it has it, else ask rather than guess between servers
            const here = best(channels.filter(c => c.guild_id === SelectedGuildStore.getGuildId()), c => [c.name], channelName);
            const places = new Set(channels.filter(c => best([c], x => [x.name], channelName)).map(c => c.guild_id));
            if (!here && places.size > 1)
                return { ok: false, error: `${channelName} is in ${[...places].map(id => GuildStore.getGuild(id)?.name).join(", ")}. Which server?` };
            if (here) channels = [here];
        }
        const channel = best(channels, c => [c.name], channelName);
        if (!channel) return { ok: false, error: `I couldn't find a voice channel called ${channelName}${server ? " in " + server : ""}.` };
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
        const chat = await findChat(a.name ?? "", false);
        return chat ? { ok: true, channel_id: chat.id, label: chat.label } : { ok: false, error: `I couldn't find ${a.name} on Discord.` };
    },

    send(a) {
        if (!a.channel_id || !String(a.text ?? "").trim()) return { ok: false, error: "Nothing to send." };
        sendMessage(a.channel_id, { content: String(a.text) });
        return { ok: true };
    },

    async read(a) {
        const chat = await findChat(a.name || "the current chat");
        if (!chat) return { ok: false, error: `I couldn't find ${a.name} on Discord.` };
        const count = Math.min(Math.max(Number(a.count) || 5, 1), 60);
        if (!MessageStore.getMessages(chat.id)?._array?.length) await MessageActions.fetchMessages?.({ channelId: chat.id, limit: 50 });
        const messages = (MessageStore.getMessages(chat.id)?._array ?? []).slice(-count)
            .map((m: any) => ({ from: userName(m.author.id), text: m.content || (m.attachments?.length ? "(an attachment)" : "(no text)") }));
        return { ok: true, chat: chat.label, messages };
    },

    // What a reply, reaction, edit or delete would act on, without doing anything: for Zade's question first
    async peek(a) {
        const t = await target(a, !!a.mine);
        return { ok: true, chat: t.chat.label, author: t.author, text: t.text.slice(0, 200) };
    },

    // emoji: a character ("🔥"); remove: take the reaction back
    async react(a) {
        const t = await target(a);
        const url = `/channels/${t.chat.id}/messages/${t.message.id}/reactions/${encodeURIComponent(String(a.emoji))}/@me`;
        await (a.remove ? RestAPI.del({ url }) : RestAPI.put({ url }));
        return { ok: true, author: t.author, chat: t.chat.label };
    },

    async reply(a) {
        const t = await target(a);
        if (!String(a.text ?? "").trim()) return { ok: false, error: "Nothing to reply." };
        sendMessage(t.chat.id, { content: String(a.text) }, true, {
            messageReference: { channel_id: t.chat.id, message_id: t.message.id, guild_id: (ChannelStore.getChannel(t.chat.id) as any)?.guild_id }
        } as any);
        return { ok: true, author: t.author, chat: t.chat.label };
    },

    async edit_last(a) {
        const t = await target(a, true);
        if (!String(a.text ?? "").trim()) return { ok: false, error: "Nothing to change it to." };
        await MessageActions.editMessage(t.chat.id, t.message.id, { content: String(a.text) });
        return { ok: true, chat: t.chat.label };
    },

    async delete_last(a) {
        const t = await target(a, true);
        await MessageActions.deleteMessage(t.chat.id, t.message.id);
        return { ok: true, chat: t.chat.label };
    },

    // online, idle, dnd (do not disturb) or invisible
    async set_status(a) {
        const status = String(a.status ?? "").toLowerCase();
        if (!STATUSES.includes(status)) return { ok: false, error: `Status can be ${STATUSES.join(", ")}.` };
        await StatusSetting.updateSetting(status);
        return { ok: true, status };
    },

    // What happened since Zade last asked (and it's forgotten here once collected)
    events: () => ({ ok: true, events: events.splice(0) }),

    answer(a) {
        selectVoiceChannel(a.channel_id);
        return { ok: true };
    },

    // Stops it ringing for you, like the decline button
    async decline(a) {
        await RestAPI.post({ url: `/channels/${a.channel_id}/call/stop-ringing`, body: { recipients: [UserStore.getCurrentUser().id] } });
        return { ok: true };
    },

    // Server names, to tell apart servers with similar names
    servers: () => ({ ok: true, servers: (Object.values(GuildStore.getGuilds()) as any[]).map(g => g.name) }),

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

    flux: {
        MESSAGE_CREATE({ message, optimistic }: { message: any; optimistic: boolean; }) {
            const me = UserStore.getCurrentUser()?.id;
            if (optimistic || !message?.author || message.author.id === me) return;
            if (document.hasFocus() && SelectedChannelStore.getChannelId() === message.channel_id) return; // you're reading it
            const channel: any = ChannelStore.getChannel(message.channel_id);
            if (!channel) return;
            const dm = !channel.guild_id;
            const mentioned = message.mention_everyone || (message.mentions ?? []).some((u: any) => (u?.id ?? u) === me);
            if (!dm && !mentioned) return;
            queue({
                kind: dm ? "dm" : "mention", channel_id: channel.id, from: userName(message.author.id),
                where: dm ? (channel.recipients?.length > 1 ? describe(channel) || "a group chat" : "") : describe(channel),
                text: message.content || (message.attachments?.length ? "sent an attachment" : "sent something")
            });
        },
        CALL_UPDATE({ call }: { call: any; }) {
            const me = UserStore.getCurrentUser()?.id;
            if (!call?.ringing?.includes(me) || SelectedChannelStore.getVoiceChannelId() === call.channel_id) return;
            if (events.some(e => e.kind === "call" && e.channel_id === call.channel_id)) return;
            const channel: any = ChannelStore.getChannel(call.channel_id);
            const from = channel?.recipients?.length === 1 ? userName(channel.recipients[0]) : describe(channel ?? { name: "someone" });
            queue({ kind: "call", channel_id: call.channel_id, from });
        },
    },

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
