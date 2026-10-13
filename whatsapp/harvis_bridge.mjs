import { exec } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import { fileURLToPath } from 'url';

const execAsync = promisify(exec);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const HARVIS_ROOT = path.dirname(__dirname);

/**
 * Queries HARVIS's AI Coordinator & Brain from WhatsApp.
 * Supports running tools, spawning FRIDAY/EDITH subagents, and returning plain text or markdown replies.
 */
export async function queryHarvis(promptText, senderNumber = '') {
  try {
    // Run python runner / brain CLI command
    const pyScript = `
import sys, os
sys.path.insert(0, '${HARVIS_ROOT}')
from brain.router import ask
from tools.registry import dispatch

prompt = """${promptText.replace(/"/g, '\\"').replace(/\n/g, ' ')}"""
response = ask('whatsapp_session', prompt)

if response.get('type') == 'tool_call':
    t_name = response['tool_name']
    t_args = response.get('tool_args', {}) or {}
    t_res = dispatch(t_name, t_args)
    tool_msg = f"Executed {t_name}: {t_res.get('output', '')}"
    final_res = ask('whatsapp_session', tool_msg, role='tool')
    print(final_res.get('content', 'Done.'))
else:
    print(response.get('content', ''))
`;

    const { stdout, stderr } = await execAsync(`python3 -c "${pyScript.replace(/"/g, '\\"')}"`, {
      cwd: HARVIS_ROOT,
      env: { ...process.env, PYTHONPATH: HARVIS_ROOT }
    });

    let resultText = stdout.strip ? stdout.strip() : stdout.trim();
    if (!resultText) {
      resultText = "HARVIS processed your request.";
    }

    // Clean up code fences or React artifacts for WhatsApp text view
    cleanWhatsAppText(resultText);

    return resultText;
  } catch (err) {
    console.error('[HARVIS BRIDGE ERROR]:', err);
    return `HARVIS Error: ${err.message || 'Execution failed.'}`;
  }
}

function cleanWhatsAppText(text) {
  // If response contains React artifact ```jsx ... ```, extract key text or summary
  if (text.includes('```jsx')) {
    text = text.replace(/```jsx[\s\S]*?```/g, '\n[Interactive UI Card Rendered on Desktop]\n');
  }
  return text;
}
