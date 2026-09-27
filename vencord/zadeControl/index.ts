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
import { findByPropsLazy, findLazy } from "@webpack";
import {
    ChannelActionCreators, ChannelRouter, ChannelStore, Constants, GuildChannelStore, GuildStore, MediaEngineStore, MessageActions,
    MessageStore, ReadStateStore, RelationshipStore, RestAPI, SelectedChannelStore, SelectedGuildStore, SnowflakeUtils, UserStore,
    VoiceStateStore
} from "@webpack/common";

const Native = VencordNative.pluginHelpers.ZadeControl as PluginNative<typeof import("./native")>;
const VoiceActions = findByPropsLazy("toggleSelfMute", "toggleSelfDeaf");
const { selectVoiceChannel } = findByPropsLazy("selectVoiceChannel", "selectChannel");
const StatusSetting = getUserSettingLazy<string>("status", "status")!;
const CloudUpload: any = findLazy(m => m.prototype?.trackUploadFinished);
const STATUSES = ["online", "idle", "dnd", "invisible"];

type Args = Record<string, any>;

// New DMs, mentions and incoming calls, for Zade to announce: it collects them with the events tool.
// Nothing is queued for a chat you have open in front of you, or while your status is Do Not Disturb.
const events: Args[] = [];
const ringing = new Map<string, number>(); // DM calls ringing you: channel id -> when it started
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

// Friends and everyone you have a DM with
function people() {
    const ids = new Set<string>(RelationshipStore.getFriendIDs());
    for (const c of ChannelStore.getSortedPrivateChannels()) (c.recipients ?? []).forEach(r => ids.add(r));
    return [...ids].map(id => UserStore.getUser(id)).filter(Boolean) as any[];
}

const personNames = (u: any) => [u.username, u.globalName ?? "", RelationshipStore.getNickname?.(u.id) ?? ""];
const isNamed = (u: any, said: string) => personNames(u).some(n => simple(n) === simple(said));

function findUser(name: string) {
    return best(people(), personNames, name);
}

async function dmWith(userId: string) {
    if (!ChannelStore.getDMFromUserId(userId)) await ChannelActionCreators.openPrivateChannel(userId);
    return ChannelStore.getDMFromUserId(userId);
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
    // misheard ("Bitnet" for BITNADE): the closest spellings, all of them if equally close
    const scored = guilds.map(g => ({ g, s: likeness(simple(words).replace(/ /g, ""), simple(g.name).replace(/ /g, "")) }));
    const top = Math.max(0.7, ...scored.map(x => x.s));
    return scored.filter(x => x.s >= top && x.s > 0.7).map(x => x.g);
}

function describe(channel: any) {
    const name = (channel.name || "").replace(/^[^a-z0-9]+/i, "");
    return channel.guild_id ? `the ${name} channel in ${GuildStore.getGuild(channel.guild_id)?.name ?? "a server"}` : name || "a group chat";
}

// What a chat is called when read back: the person for a DM, else the channel or group
function label(channel: any) {
    return channel.recipients?.length === 1 ? userName(channel.recipients[0]) : describe(channel);
}

// A person's DM, else a text channel, by what was said ("dexorto", "general", "general in bitnade"). If the
// whole phrase finds nothing ("dexorto user"), its words are tried, longest first.
// anywhere: search channels in every server (to open or read one). For sending, only the server on screen, or
// the one named, is searched: a message must never go to a same-named channel in some random server.
async function findChat(name: string, anywhere = true): Promise<{ id: string; label: string; } | undefined> {
    const whole = await findChatExactly(name, anywhere);
    // a server was named ("general in bit net"): one of the words alone could be a channel in any server
    if (whole || !name.includes(" ") || / (?:in|on|from) /i.test(name)) return whole;
    for (const word of name.split(/\s+/).filter(w => w.length >= 3).sort((a, b) => b.length - a.length)) {
        const hit = await findChatExactly(word, anywhere);
        if (hit) return hit;
    }
}

async function findChatExactly(name: string, anywhere: boolean): Promise<{ id: string; label: string; } | undefined> {
    // "the current chat", "this channel", "here": what's open on screen (small models misspell it: "current
    // chant"). The whole name: #open-mic, #this-week or a friend called Here are not the open chat.
    if (/^(?:the )?(?:(?:current|this|open|opened|same)(?: (?:chat|chant|channel|dm|conversation|convo|one|server))?|here)$/.test(simple(name))) {
        const channel = ChannelStore.getChannel(SelectedChannelStore.getChannelId());
        if (!channel) throw new Error("No chat is open in Discord right now.");
        return { id: channel.id, label: label(channel) };
    }
    const [channelName, guildName] = name.split(/ (?:in|on|from) /i);
    let channels = guildChannels("SELECTABLE");
    if (guildName) {
        const ids = new Set(servers(guildName).map(g => g.id));
        channels = channels.filter(c => ids.has(c.guild_id));
    }
    else if (!anywhere) channels = channels.filter(c => c.guild_id === SelectedGuildStore.getGuildId());
    // An exact name first, a person's or a channel's ("chat" is #chat, not a friend called Chad), then the
    // closest of either. With a server named it's a channel there.
    const users = guildName ? [] : people();
    const exactChannel = channels.find(c => simple(c.name) === simple(channelName));
    const user = users.find(u => isNamed(u, name)) ?? (exactChannel ? undefined : best(users, personNames, name));
    const id = user && await dmWith(user.id);
    if (user && id) return { id, label: userName(user.id) };
    const channel = exactChannel ?? best(channels, c => [c.name], channelName);
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

// The message a reaction or reply is for: the one Zade asked you about (channel_id and message_id from peek,
// not whatever is newest by the time you said yes), else in the named chat (or the one open) the latest from
// someone else, or with mine, my own latest (to edit or delete).
async function target(a: Args, mine = false) {
    if (a.channel_id && a.message_id) {
        const channel: any = ChannelStore.getChannel(a.channel_id);
        const message: any = MessageStore.getMessage(a.channel_id, a.message_id);
        if (!channel || !message) throw new Error("That message isn't there any more.");
        return { chat: { id: channel.id, label: label(channel) }, message, author: userName(message.author.id), text: message.content || "(an attachment)" };
    }
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

    // A DM call: joining the DM's voice rings them. dry: only who it would ring (channel_id to call next), and
    // whether that name was said exactly; if not, Zade asks first ("call Rick" must never ring Nick unasked).
    async call(a) {
        let id = a.channel_id;
        if (!id) {
            const name = String(a.name ?? "");
            const words = name.split(/\s+/).filter(w => w.length >= 3).sort((x, y) => y.length - x.length);
            const all = people();
            // "user called DEXORTO", "my brother Sam": the whole phrase said exactly, then one of its words,
            // then the closest spelling of the phrase, then of its words (longest first)
            const user = all.find(u => isNamed(u, name)) ?? words.map(w => all.find(u => isNamed(u, w))).find(Boolean)
                ?? findUser(name) ?? words.map(w => findUser(w)).find(Boolean);
            id = user && await dmWith(user.id);
            if (!user || !id) return { ok: false, error: `I couldn't find ${a.name}.` };
            if (a.dry) return { ok: true, calling: userName(user.id), channel_id: id, exact: isNamed(user, name) || words.some(w => isNamed(user, w)) };
        }
        const channel = ChannelStore.getChannel(id);
        if (!channel || channel.guild_id) return { ok: false, error: "That isn't a DM." };
        selectVoiceChannel(id);
        return { ok: true, calling: label(channel) };
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

    async send(a) {
        if (!a.channel_id || !String(a.text ?? "").trim()) return { ok: false, error: "Nothing to send." };
        await sendMessage(a.channel_id, { content: String(a.text) }); // a rejected message fails here, not "Sent"
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
        return { ok: true, chat: t.chat.label, channel_id: t.chat.id, message_id: t.message.id, author: t.author, text: t.text.slice(0, 200) };
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
        await sendMessage(t.chat.id, { content: String(a.text) }, true, {
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

    // Send a picture (path under ~/Pictures) with optional text, the way the upload button does
    async send_file(a) {
        const b64 = await Native.readPicture(String(a.path));
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const name = String(a.path).split("/").pop()!;
        const type = /\.png$/i.test(name) ? "image/png" : /\.gif$/i.test(name) ? "image/gif" : /\.webp$/i.test(name) ? "image/webp" : "image/jpeg";
        const upload = new CloudUpload({ file: new File([bytes], name, { type }), isThumbnail: false, platform: 1 }, a.channel_id);
        await new Promise<void>((resolve, reject) => {
            upload.on("complete", () => resolve());
            upload.on("error", () => reject(new Error("Discord didn't take the upload.")));
            upload.upload();
        });
        await RestAPI.post({
            url: Constants.Endpoints.MESSAGES(a.channel_id),
            body: {
                channel_id: a.channel_id, content: String(a.text ?? ""), nonce: SnowflakeUtils.fromTimestamp(Date.now()),
                sticker_ids: [], type: 0, attachments: [{ id: "0", filename: upload.filename, uploaded_filename: upload.uploadedFilename }]
            }
        });
        return { ok: true };
    },

    // What happened since Zade last asked (and it's forgotten here once collected). A call that started
    // ringing over 30 s ago is left out: by the time it's read out it has stopped.
    events: () => ({ ok: true, events: events.splice(0).filter(e => e.kind !== "call" || Date.now() - e.at < 30000) }),

    // Only a call still ringing you: joining an ended one would ring the other person instead
    answer(a) {
        if (Date.now() - (ringing.get(a.channel_id) ?? 0) > 60000) return { ok: false, error: "The call has ended." };
        selectVoiceChannel(a.channel_id);
        return { ok: true };
    },

    // Stops it ringing for you, like the decline button
    async decline(a) {
        await RestAPI.post({ url: `/channels/${a.channel_id}/call/stop-ringing`, body: { recipients: [UserStore.getCurrentUser().id] } });
        return { ok: true };
    },

    // Names speech recognition should expect: servers, friends (and DM people), and voice channels
    names() {
        const people = new Set<string>();
        for (const id of RelationshipStore.getFriendIDs()) people.add(userName(id));
        for (const c of ChannelStore.getSortedPrivateChannels().slice(0, 30)) (c.recipients ?? []).forEach(r => people.add(userName(r)));
        const clean = (n: string) => n.replace(/[^\p{L}\p{N}' -]+/gu, " ").replace(/\s+/g, " ").trim();
        const servers = (Object.values(GuildStore.getGuilds()) as any[]).map(g => clean(g.name));
        const voice = guildChannels("VOCAL").map(c => clean(c.name));
        return { ok: true, servers, people: [...people].map(clean), voice };
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
                kind: dm ? "dm" : "mention", channel_id: channel.id, message_id: message.id, from: userName(message.author.id),
                where: dm ? (channel.recipients?.length > 1 ? describe(channel) || "a group chat" : "") : describe(channel),
                text: message.content || (message.attachments?.length ? "sent an attachment" : "sent something")
            });
        },
        CALL_UPDATE({ call }: { call: any; }) {
            const me = UserStore.getCurrentUser()?.id;
            if (!call?.ringing?.includes(me)) return void ringing.delete(call?.channel_id);
            if (SelectedChannelStore.getVoiceChannelId() === call.channel_id) return;
            // updates keep coming while it rings: announce it once (a new call a minute later is new)
            if (Date.now() - (ringing.get(call.channel_id) ?? 0) < 60000) return;
            ringing.set(call.channel_id, Date.now());
            const channel: any = ChannelStore.getChannel(call.channel_id);
            const from = channel?.recipients?.length === 1 ? userName(channel.recipients[0]) : describe(channel ?? { name: "someone" });
            queue({ kind: "call", channel_id: call.channel_id, from });
        },
        CALL_DELETE({ channelId }: { channelId: string; }) {
            ringing.delete(channelId);
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
