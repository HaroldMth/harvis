import hashlib
import json
import logging
import re
import os
import base64
import time
from db.store import get_history, save_message, update_provider_status, set_harvis_busy
from brain.modes import apply_prompt_mode, omniroute_options
from brain.providers import call_omniroute
from brain.self_improvement import log_issue

logger = logging.getLogger("harvis.router")

from brain.providers import call_omniroute, call_opus_proxy, call_groq, call_mistral, call_gemini
from brain.modes import get_fallback_chain, get_engine

# Primary provider is OmniRoute (running locally on port 20128 with Kiro routes).
CAPABLE_PROVIDERS = [
    {"name": "omniroute",  "func": call_omniroute},
    {"name": "groq",       "func": call_groq},
    {"name": "gemini",     "func": call_gemini},
    {"name": "mistral",    "func": call_mistral},
    {"name": "opus_proxy", "func": call_opus_proxy},
]
PROVIDERS = CAPABLE_PROVIDERS

PROVIDER_CHAINS = {
    "normal": CAPABLE_PROVIDERS,
    "fast":   CAPABLE_PROVIDERS,
}


GENERIC_GREETINGS_PATTERN = re.compile(
    r"^(Harold,\s*I['']?m\s*your\s*local\s*assistant\s*HARVIS\.?\s*How\s*can\s*I\s*help\s*you\s*today\??\s*|Hello\s*Harold!??\s*|Hi\s*Harold!??\s*)",
    re.IGNORECASE
)


def clean_tool_response(text: str) -> str:
    """Strips redundant generic opening greetings when returning tool execution results."""
    if not text:
        return text
    cleaned = GENERIC_GREETINGS_PATTERN.sub("", text.strip()).strip()
    return cleaned if cleaned else text


def detect_images_in_prompt(prompt: str) -> list:
    """Detect if prompt contains image data (base64 or file paths)."""
    images = []
    
    # Check for base64 image data
    if "data:image/" in prompt or "base64," in prompt:
        # Extract base64 images
        import re
        base64_pattern = r'data:image/([a-zA-Z]+);base64,([a-zA-Z0-9+/=]+)'
        matches = re.findall(base64_pattern, prompt)
        for mime, b64_data in matches:
            images.append({
                "type": "base64",
                "mime_type": f"image/{mime}",
                "data": b64_data
            })
    
    # Check for file paths to images
    import re
    # Common image extensions - match file paths more carefully
    image_extensions = r'(png|jpg|jpeg|gif|webp|bmp|svg)'
    # Match paths that end with image extensions
    path_pattern = rf'(\S+?\.{image_extensions})'
    matches = re.findall(path_pattern, prompt, re.IGNORECASE)
    for match in matches:
        if isinstance(match, tuple):
            match = match[0]  # Handle regex groups
        if os.path.exists(match):
            images.append({
                "type": "file",
                "path": match
            })
    
    return images


def encode_image_to_base64(image_path: str) -> str:
    """Encode an image file to base64 string."""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encode image {image_path}: {e}")
        return None


def prepare_vision_request(prompt: str, images: list) -> list:
    """Convert prompt with images into vision API format (pure list of parts)."""
    # Clean the prompt by removing [IMAGE: data:image/...;base64,...] or raw base64 tags
    clean_text = re.sub(r'\[IMAGE:\s*data:image/[a-zA-Z]+;base64,[a-zA-Z0-9+/=]+\]', '', prompt).strip()
    if not clean_text:
        clean_text = "What is in this image?"

    if not images:
        return [{"type": "text", "text": clean_text}]

    # Build content array with text and images
    content = [{"type": "text", "text": clean_text}]

    for img in images:
        if img["type"] == "base64":
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['mime_type']};base64,{img['data']}"
                }
            })
        elif img["type"] == "file":
            ext = os.path.splitext(img["path"])[1].lower()
            mime_types = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }
            mime_type = mime_types.get(ext, 'image/png')
            b64_data = encode_image_to_base64(img["path"])
            if b64_data:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64_data}"
                    }
                })

    return content


def complete(history, system_prompt=None, mode="normal", stream_callback=None,
             extra_tools=None, model_override=None):
    """One model turn through the provider chain, with no session/DB side effects.

    Same provider order and model fallback as ask(), but the caller owns the
    history. Returns the response dict, or None if every provider failed.
    extra_tools (e.g. the harness `finish` tool) are only offered to OmniRoute;
    other providers can still end a task with a plain text reply.
    """
    pool = omniroute_options(mode).get("model", "auto/smart:pro")
    system_prompt = (system_prompt or "") + (
        f"\n\n[IDENTITY] You are HARVIS on OmniRoute pool `{pool}`. "
        "Only capable models are in this pool \u2014 not flash, lite, mini, nano, or haiku-class. "
        "If asked which model you are, answer: HARVIS, routed through that OmniRoute pool. "
        "You will not see a single fixed lab model name unless the gateway reports one."
    )

    for provider in PROVIDER_CHAINS.get(mode, CAPABLE_PROVIDERS):
        name, func = provider["name"], provider["func"]
        try:
            if name == "omniroute":
                last_err = None
                for candidate in ([model_override] if model_override else get_fallback_chain(mode)):
                    try:
                        res = func(history, system_prompt=system_prompt, stream_callback=stream_callback,
                                   model=candidate, extra_tools=extra_tools)
                        res["provider"] = name
                        res["model"] = res.get("routed_model") or candidate
                        update_provider_status(name, True)
                        return res
                    except Exception as m_err:
                        if stream_callback and "Cancelled by user" in str(m_err):
                            raise
                        last_err = m_err
                        logger.warning(f"OmniRoute candidate {candidate} failed: {m_err}")
                if last_err:
                    raise last_err
            else:
                try:
                    res = func(history, system_prompt=system_prompt, stream_callback=stream_callback)
                except TypeError:
                    res = func(history, system_prompt=system_prompt)
                res["provider"] = name
                res["model"] = res.get("routed_model") or name
                update_provider_status(name, True)
                return res
        except Exception as e:
            if "Cancelled by user" in str(e):
                raise
            logger.warning(f"Provider {name} failed: {e}")
            update_provider_status(name, False)
            log_issue("PROVIDER_FAIL", f"Provider '{name}' failed in mode '{mode}'",
                      details=str(e), recommendation=f"Check API key, quota limits, or model route mapping for '{name}'.")

    log_issue("SYSTEM", f"All providers failed in mode '{mode}'",
              details=f"History count: {len(history)}.",
              recommendation="Verify network connection, gateway availability, and key configuration.")
    return None


def ask(session_id, prompt, role="user", use_cache=False, system_prompt=None, stream_callback=None, thinking_mode=False, mode="normal", model_override=None):
    """
    Queries the brain fallback chain for a response with persistent chat memory.
    mode: "normal" | "think" | "fast" | "coding" | "cheap" | "vision"
    thinking_mode=True is equivalent to mode="think" (legacy compat).
    Automatically switches to vision mode if images are detected in prompt.
    """
    set_harvis_busy(True)
    try:
        # Normalize mode
        if thinking_mode:
            mode = "think"

        mode, prompt = apply_prompt_mode(prompt, mode)

        if isinstance(prompt, str) and prompt.lower().startswith("think:"):
            mode = "think"
            prompt = prompt[6:].strip()

        # Auto-detect images and switch to vision mode
        images = detect_images_in_prompt(prompt) if isinstance(prompt, str) else []
        if images and mode != "vision":
            logger.info(f"Images detected in prompt, auto-switching to vision mode")
            mode = "vision"

        if mode == "think":
            reasoning_prompt = (
                "\n\n[MODE: DEEP THINKING & HIGH-PRECISION REASONING]\n"
                "You are operating in Deep Thinking / Reasoning Mode. For this prompt:\n"
                "1. Wrap your step-by-step internal reasoning process inside <think> ... </think> tags.\n"
                "2. AFTER the </think> tag, present your complete, detailed, and polished technical answer to Harold.\n"
            )
            system_prompt = (system_prompt or "") + reasoning_prompt

        pool = omniroute_options(mode).get("model", "auto/smart:pro")
        system_prompt = (system_prompt or "") + (
            f"\n\n[IDENTITY] You are HARVIS on OmniRoute pool `{pool}`. "
            "Only capable models are in this pool — not flash, lite, mini, nano, or haiku-class. "
            "If asked which model you are, answer: HARVIS, routed through that OmniRoute pool. "
            "You will not see a single fixed lab model name unless the gateway reports one."
        )

        # 1. Save incoming message to SQLite database for persistent memory
        if prompt:
            if role == "tool":
                # If prompt is structured JSON, save as-is so multi-turn history mapping is exact
                try:
                    p_json = json.loads(prompt) if isinstance(prompt, str) else prompt
                    if isinstance(p_json, dict) and p_json.get("tool_name"):
                        prompt_to_save = json.dumps(p_json) if not isinstance(prompt, str) else prompt
                    else:
                        prompt_to_save = f"[TOOL EXECUTION RESULT]:\n{prompt}"
                except Exception:
                    prompt_to_save = f"[TOOL EXECUTION RESULT]:\n{prompt}"
            elif images:
                # Save cleaned text with placeholder instead of massive base64 blob
                clean_text = re.sub(r'\[IMAGE:\s*data:image/[a-zA-Z]+;base64,[a-zA-Z0-9+/=]+\]', '[IMAGE ATTACHED]', str(prompt)).strip()
                prompt_to_save = clean_text or "[IMAGE ATTACHED]"
            else:
                prompt_to_save = prompt
                
            save_message(session_id, role, prompt_to_save)

        # 2. Retrieve history from SQLite database (persistent multi-turn memory)
        history = get_history(session_id, limit=50)

        # Sanitize: remove dangling assistant tool_call JSON with no following tool result
        clean_history = []
        for i, msg in enumerate(history):
            if msg["role"] == "assistant":
                content = msg["content"] or ""
                if isinstance(content, str) and content.startswith('{') and '"type": "tool_call"' in content:
                    next_msg = history[i + 1] if i + 1 < len(history) else None
                    if not next_msg or next_msg["role"] != "tool":
                        continue  # orphaned tool_call — drop it
            clean_history.append(msg)
        history = clean_history

        # If we have images, prepare vision request format
        if images:
            # Convert current prompt to vision format
            vision_content = prepare_vision_request(prompt, images)
            # Update the current message in history to use vision format
            if history and history[-1]["role"] == role:
                history[-1]["content"] = vision_content
            else:
                history.append({"role": role, "content": vision_content})
        elif not history and prompt:
            history = [{"role": role, "content": prompt}]

        # 3. Pick provider chain based on mode
        provider_chain = PROVIDER_CHAINS.get(mode, CAPABLE_PROVIDERS)

        start_time = time.time()
        for provider in provider_chain:
            name = provider["name"]
            func = provider["func"]

            p_start = time.time()
            logger.info(f"Attempting provider: {name} (mode={mode}, Session: {session_id}, History len: {len(history)})...")
            try:
                if name == "omniroute":
                    models_to_try = [model_override] if model_override else get_fallback_chain(mode)
                    omni_success = False
                    last_omni_err = None
                    for candidate_model in models_to_try:
                        m_start = time.time()
                        try:
                            logger.info(f"OmniRoute attempting model candidate: {candidate_model}...")
                            res = func(history, system_prompt=system_prompt, stream_callback=stream_callback, model=candidate_model)
                            m_elapsed = round(time.time() - m_start, 2)
                            routed = res.get("routed_model") or candidate_model
                            res["provider"] = name
                            res["model"] = routed
                            update_provider_status(name, True)

                            if role == "tool" and res.get("type") == "text":
                                res["content"] = clean_tool_response(res["content"])

                            if res["type"] == "text":
                                save_message(session_id, "assistant", res["content"])
                            elif res["type"] == "tool_call":
                                save_message(session_id, "assistant", json.dumps(res))

                            logger.info(f"OmniRoute succeeded via {routed} in {m_elapsed}s (mode={mode}).")
                            return res
                        except Exception as m_err:
                            last_omni_err = m_err
                            logger.warning(f"OmniRoute candidate {candidate_model} failed: {m_err}. Trying next fallback...")
                    if last_omni_err:
                        raise last_omni_err
                else:
                    extra = {}
                    try:
                        res = func(history, system_prompt=system_prompt, stream_callback=stream_callback, **extra)
                    except TypeError:
                        res = func(history, system_prompt=system_prompt, **extra)

                    p_elapsed = round(time.time() - p_start, 2)
                    update_provider_status(name, True)
                    routed = res.get("routed_model") or extra.get("model") or name
                    res["provider"] = name
                    res["model"] = routed

                    # Bottleneck check (> 8 seconds)
                    if p_elapsed > 8.0:
                        log_issue(
                            "BOTTLENECK",
                            f"High response latency on provider '{name}' ({p_elapsed}s)",
                            details=f"Mode: {mode}, Model: {routed}, History items: {len(history)}",
                            recommendation=f"Consider adjusting timeout or ranking provider '{name}' lower if slowdown persists."
                        )

                    if role == "tool" and res.get("type") == "text":
                        res["content"] = clean_tool_response(res["content"])

                    if res["type"] == "text":
                        save_message(session_id, "assistant", res["content"])
                    elif res["type"] == "tool_call":
                        save_message(session_id, "assistant", json.dumps(res))

                    logger.info(f"Provider {name} succeeded via {routed} in {p_elapsed}s (mode={mode}).")
                    return res
            except Exception as e:
                err_msg = str(e)
                logger.warning(f"Provider {name} failed: {err_msg}")
                update_provider_status(name, False)
                log_issue(
                    "PROVIDER_FAIL",
                    f"Provider '{name}' failed in mode '{mode}'",
                    details=err_msg,
                    recommendation=f"Check API key, quota limits, or model route mapping for '{name}'."
                )

        # Fallback response when all providers fail
        log_issue(
            "SYSTEM",
            f"All providers failed in mode '{mode}'",
            details=f"Session: {session_id}. History count: {len(history)}.",
            recommendation="Verify network connection, gateway availability, and key configuration."
        )
        err_res = {
            "type": "text",
            "content": "I am sorry, but all of my brain connections are currently unavailable. Please check your network and API keys.",
            "tool_name": None,
            "tool_args": None,
            "model": "error"
        }
        return err_res
    finally:
        set_harvis_busy(False)
