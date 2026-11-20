import { init } from "@heyputer/puter.js/src/init.cjs";
import { readFile } from "fs/promises";

async function main() {
    const token = process.env.PUTER_AUTH_TOKEN;
    if (!token) {
        console.error("No PUTER_AUTH_TOKEN provided");
        process.exit(1);
    }
    const audioPath = process.argv[2];
    if (!audioPath) {
        console.error("Usage: node puter_stt.mjs <audio_path>");
        process.exit(1);
    }

    try {
        const puter = init(token);
        const buffer = await readFile(audioPath);
        const dataUrl = `data:audio/wav;base64,${buffer.toString("base64")}`;
        
        // Use default model (which maps to gpt-4o-mini-transcribe or whisper usually)
        const result = await puter.ai.speech2txt(dataUrl, { language: "en" });
        
        console.log((result.text ?? result ?? "").trim());
        process.exit(0);
    } catch (err) {
        console.error("Error calling puter.js:", err.message);
        process.exit(1);
    }
}

main();
