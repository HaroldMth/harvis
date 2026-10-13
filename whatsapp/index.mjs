import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers
} from '@whiskeysockets/baileys';
import pino from 'pino';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import { queryHarvis } from './harvis_bridge.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AUTH_DIR = path.join(__dirname, 'auth_info_baileys');

// Whitelisted phone numbers (international format without +)
const ALLOWED_NUMBERS = ['237680260772', '27632883428'];

// Default pairing phone number (can be overridden via env PAIRING_NUMBER)
const PAIRING_NUMBER = process.env.PAIRING_NUMBER || '237694668970';

// Set BAILEYS_LOG_LEVEL=trace in env for full protocol tracing (shows
// unhandled server notifications like companion_reg_refresh — see
// https://github.com/WhiskeySockets/baileys/issues/2737).
// Set to 'silent' once things are stable again.
const LOG_LEVEL = process.env.BAILEYS_LOG_LEVEL || 'trace';

// In-memory LID to Phone Number cache
const lidCache = new Map();

async function startWhatsAppBridge() {
  if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
  }

  // Check if root creds.json exists in workspace and move to auth folder if needed
  const rootCreds = path.join(__dirname, 'creds.json');
  const targetCreds = path.join(AUTH_DIR, 'creds.json');
  if (fs.existsSync(rootCreds) && !fs.existsSync(targetCreds)) {
    console.log('Found creds.json, copying to auth_info_baileys…');
    fs.copyFileSync(rootCreds, targetCreds);
  }

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  console.log(`🚀 Starting HARVIS WhatsApp Bridge (Baileys v${version.join('.')})`);
  console.log(`🔒 Whitelisted Phone Numbers: ${ALLOWED_NUMBERS.join(', ')}`);
  console.log(`🪵 Baileys log level: ${LOG_LEVEL}`);

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: LOG_LEVEL }),
    browser: Browsers.macOS('Chrome'),
    connectTimeoutMs: 60_000,
    defaultQueryTimeoutMs: undefined,
    shouldSyncHistoryMessage: () => false,
    keepAliveIntervalMs: 30_000,
    markOnlineOnConnect: false,
  });

  // Tracks whether the latest creds.update has actually finished writing to
  // disk. Baileys can fire the 515 (restartRequired) close almost
  // immediately after creds.update during pairing — if we restart before
  // the write flushes, we reconnect with a stale/incomplete auth folder.
  let credsSaved = false;

  sock.ev.on('creds.update', async () => {
    credsSaved = false;
    await saveCreds();
    credsSaved = true;
    console.log('[CREDS] saved to disk');
  });

  // TEMP DEBUG: full raw connection.update dump. This is what shows you
  // whether the socket is dying on an unhandled companion_reg_refresh
  // notification (see #2737) vs an actual local bug. Remove/gate behind
  // BAILEYS_LOG_LEVEL once diagnosed.
  sock.ev.on('connection.update', (update) => {
    console.log('[RAW UPDATE]', JSON.stringify(update, null, 2));
  });

  let pairingRequested = false;

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (connection) {
      console.log(`[CONNECTION STATUS]: ${connection}`);
    }

    // Baileys v7 Pairing Code Request Flow
    if (qr && !sock.authState.creds.registered && !pairingRequested) {
      pairingRequested = true;
      try {
        const cleanNumber = PAIRING_NUMBER.replace(/[^0-9]/g, '');
        console.log(`\n⏳ Requesting Baileys v7 Pairing Code for +${cleanNumber} (waiting 3s for handshake)…`);

        // 3-second delay to ensure noise handshake completes before querying code
        await new Promise(r => setTimeout(r, 3000));

        const code = await sock.requestPairingCode(cleanNumber);
        console.log('\n==================================================');
        console.log(`🔑 YOUR FRESH WHATSAPP PAIRING CODE IS: ${code}`);
        console.log(`📱 Phone Number: +${cleanNumber}`);
        console.log('👉 Open WhatsApp -> Linked Devices -> Link with Phone Number');
        console.log('==================================================\n');
      } catch (err) {
        console.error('❌ Failed to request pairing code:', err.message || err);
      }
    }

    if (connection === 'open') {
      console.log('\n✅ Connected to WhatsApp Web successfully!');
      pairingRequested = false;

      // Resolve LIDs for allowed phone numbers using Baileys v7 mapping
      try {
        if (sock.signalRepository?.lidMapping?.getLIDsForPNs) {
          const lids = await sock.signalRepository.lidMapping.getLIDsForPNs(ALLOWED_NUMBERS);
          console.log('🔗 Resolved Whitelist LIDs:', lids);
          if (lids) {
            Object.entries(lids).forEach(([pn, lid]) => {
              if (lid) lidCache.set(lid, pn);
            });
          }
        }
      } catch (err) {
        console.log('LID Resolution Notice:', err.message || err);
      }
    } else if (connection === 'close') {
      // Robustly pull the status code regardless of how the error is wrapped
      // (Boom vs plain Error vs { error, date } wrapper).
      const err = lastDisconnect?.error;
      const statusCode =
        err?.output?.statusCode ??
        err?.error?.output?.statusCode ??
        err?.status ??
        undefined;

      console.log(`⚠ Connection closed with statusCode: ${statusCode}`);
      console.log('[DISCONNECT DETAIL]', JSON.stringify(lastDisconnect, null, 2));

      if (statusCode === DisconnectReason.restartRequired) {
        console.log('🔄 Pairing successful (515 restartRequired) — waiting for creds to flush before reconnecting…');

        // Wait for the in-flight creds.update write to finish (failsafe cap
        // at 3s) before restarting, so we never reconnect against a
        // half-written auth folder.
        await new Promise((resolve) => {
          const check = setInterval(() => {
            if (credsSaved) {
              clearInterval(check);
              resolve();
            }
          }, 100);
          setTimeout(() => {
            clearInterval(check);
            resolve();
          }, 3000);
        });

        setTimeout(startWhatsAppBridge, 500);

      } else if (
        statusCode === DisconnectReason.loggedOut ||
        statusCode === DisconnectReason.badSession ||
        statusCode === 401 ||
        statusCode === 500
      ) {
        console.log(`❌ Logged out / bad session (code ${statusCode}). Wiping auth folder for fresh pair…`);
        pairingRequested = false;
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
        setTimeout(startWhatsAppBridge, 3000);

      } else if (statusCode === 408) {
        // QR/pairing ref pool exhausted without ever reaching pair-success.
        // As of Aug 2026 this is the signature of the unpatched
        // companion_reg_refresh regression (baileys#2737) — not a local bug.
        console.log('❌ Ref pool exhausted before pair-success (statusCode 408).');
        console.log('   If this happens on every fresh pairing attempt, see:');
        console.log('   https://github.com/WhiskeySockets/baileys/issues/2737');
        console.log('   Workaround: npm install github:WhiskeySockets/Baileys#pull/2765/head');
        pairingRequested = false;
        setTimeout(startWhatsAppBridge, 5000);

      } else {
        console.log('⚠ Reconnecting socket…');
        setTimeout(startWhatsAppBridge, 5000);
      }
    }
  });

  sock.ev.on('messages.upsert', async (m) => {
    if (m.type !== 'notify') return;

    for (const msg of m.messages) {
      if (!msg.message || msg.key.fromMe) continue;

      const remoteJid = msg.key.remoteJid;
      if (!remoteJid) continue;

      // Extract sender phone number or LID
      let senderNum = '';
      let isAllowed = false;

      // Check if remoteJid contains one of the allowed phone numbers
      for (const num of ALLOWED_NUMBERS) {
        if (remoteJid.includes(num)) {
          isAllowed = true;
          senderNum = num;
          break;
        }
      }

      // Check LID mapping store for Baileys v7 LID resolution
      if (!isAllowed && remoteJid.endsWith('@lid')) {
        try {
          if (lidCache.has(remoteJid)) {
            senderNum = lidCache.get(remoteJid);
            isAllowed = ALLOWED_NUMBERS.includes(senderNum);
          } else if (sock.signalRepository?.lidMapping?.getPNForLID) {
            const resolvedPn = await sock.signalRepository.lidMapping.getPNForLID(remoteJid);
            if (resolvedPn) {
              senderNum = resolvedPn;
              lidCache.set(remoteJid, resolvedPn);
              isAllowed = ALLOWED_NUMBERS.some(num => resolvedPn.includes(num));
            }
          }
        } catch (e) {}
      }

      if (!isAllowed) {
        console.log(`🚫 [BLOCKED] Ignored unauthorized WhatsApp message from: ${remoteJid}`);
        continue;
      }

      // Extract message text content
      const text = msg.message.conversation ||
                 msg.message.extendedTextMessage?.text ||
                 msg.message.imageMessage?.caption ||
                 '';

      if (!text) continue;

      console.log(`\n📩 [WHATSAPP MESSAGE from +${senderNum}]: "${text}"`);

      // Query HARVIS Brain
      const harvisReply = await queryHarvis(text, senderNum);

      console.log(`🤖 [HARVIS REPLY to +${senderNum}]: "${harvisReply.slice(0, 100)}…"`);

      // Reply back on WhatsApp
      await sock.sendMessage(remoteJid, { text: harvisReply }, { quoted: msg });
    }
  });
}

startWhatsAppBridge().catch(err => {
  console.error('Fatal WhatsApp Bridge Error:', err);
});
