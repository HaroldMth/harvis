import json
import re
import requests
import logging
from config import (
    GEMINI_API_KEY,
    POLLINATIONS_API_KEY,
    GROQ_API_KEY,
    OPENROUTER_API_KEY,
    CEREBRAS_API_KEY,
    MISTRAL_API_KEY,
    COHERE_API_KEY,
    OMNIROUTE_API_KEY,
    OPUS_PROXY_API_KEY
)
from tools.registry import get_all_tools_schema
from brain.prompts import SYSTEM_PROMPT as DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger("harvis.providers")

# Helper to map standard tools schema to OpenAI/Cohere format
def get_openai_tools():
    return [{"type": "function", "function": t} for t in get_all_tools_schema()]

# Mapper functions for history
def map_history_to_gemini(history):
    contents = []
    awaiting_tool_response = False
    for msg in history:
        role = msg["role"]
        content = msg["content"]
        
        # Handle structured vision content list/dict
        if isinstance(content, list):
            # List of OpenAI-style parts: [{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": "data:..."}}]
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if "base64," in url:
                            header, b64_data = url.split("base64,", 1)
                            mime_type = header.split(";")[0].replace("data:", "") or "image/jpeg"
                            parts.append({
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": b64_data
                                }
                            })
            if role == "user":
                contents.append({"role": "user", "parts": parts})
                awaiting_tool_response = False
            continue

        is_json = False
        parsed_content = None
        if isinstance(content, str) and (content.startswith("{") or content.startswith("[")):
            try:
                parsed_content = json.loads(content)
                is_json = True
            except Exception:
                pass
                
        if role == "user":
            text_val = content if isinstance(content, str) else str(content)
            contents.append({"role": "user", "parts": [{"text": text_val}]})
            awaiting_tool_response = False
        elif role == "assistant":
            if is_json and isinstance(parsed_content, dict):
                if parsed_content.get("type") == "tool_call" and parsed_content.get("tool_name") is not None:
                    part = {
                        "functionCall": {
                            "name": parsed_content["tool_name"],
                            "args": parsed_content.get("tool_args", {})
                        }
                    }
                    if parsed_content.get("thought_signature"):
                        part["thoughtSignature"] = parsed_content["thought_signature"]
                    contents.append({
                        "role": "model",
                        "parts": [part]
                    })
                    awaiting_tool_response = True
                else:
                    contents.append({"role": "model", "parts": [{"text": parsed_content.get("content", "")}]})
                    awaiting_tool_response = False
            else:
                text_val = content if isinstance(content, str) else str(content)
                contents.append({"role": "model", "parts": [{"text": text_val}]})
                awaiting_tool_response = False
        elif role == "tool":
            if awaiting_tool_response and is_json and isinstance(parsed_content, dict) and parsed_content.get("tool_name") is not None:
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": parsed_content["tool_name"],
                            "response": {"result": parsed_content.get("result", "")}
                        }
                    }]
                })
                awaiting_tool_response = False
    return contents

def map_history_to_openai(history, system_prompt=None):
    messages = [{"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}]
    active_tool_call_id = None
    for idx, msg in enumerate(history):
        role = msg["role"]
        content = msg["content"]
        
        # Handle structured vision content list/dict directly
        if isinstance(content, list) or isinstance(content, dict):
            # In OpenAI format, multimodal content is directly passed as a list of dict parts
            content_val = content if isinstance(content, list) else content.get("content", str(content))
            if role == "user":
                messages.append({"role": "user", "content": content_val})
                active_tool_call_id = None
            elif role == "assistant":
                messages.append({"role": "assistant", "content": str(content)})
                active_tool_call_id = None
            continue

        is_json = False
        parsed_content = None
        if isinstance(content, str) and (content.startswith("{") or content.startswith("[")):
            try:
                parsed_content = json.loads(content)
                is_json = True
            except Exception:
                pass
                
        if role == "user":
            messages.append({"role": "user", "content": content})
            active_tool_call_id = None
        elif role == "assistant":
            if is_json and isinstance(parsed_content, dict):
                if parsed_content.get("type") == "tool_call" and parsed_content.get("tool_name") is not None:
                    active_tool_call_id = f"call_{idx}"
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": active_tool_call_id,
                            "type": "function",
                            "function": {
                                "name": parsed_content["tool_name"],
                                "arguments": json.dumps(parsed_content.get("tool_args", {}))
                            }
                        }]
                    })
                else:
                    messages.append({"role": "assistant", "content": parsed_content.get("content", "")})
                    active_tool_call_id = None
            else:
                messages.append({"role": "assistant", "content": content})
                active_tool_call_id = None
        elif role == "tool":
            # Conversation windows can begin with a historical tool result. It
            # has no matching assistant tool-call, so providers correctly reject
            # it; omit it rather than sending an invalid message sequence.
            if not active_tool_call_id:
                continue
            tool_name = "unknown_tool"
            if is_json and isinstance(parsed_content, dict) and parsed_content.get("tool_name") is not None:
                tool_name = parsed_content["tool_name"]
                tool_result = parsed_content.get("result", "")
            else:
                tool_result = content
                if isinstance(content, str):
                    match = re.search(r"Tool `([a-zA-Z0-9_]+)` returned:", content)
                    if match:
                        tool_name = match.group(1)
                
            messages.append({
                "role": "tool",
                "tool_call_id": active_tool_call_id,
                "name": tool_name,
                "content": json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result
            })
            active_tool_call_id = None
    return messages

def parse_tool_arguments(raw):
    """OpenAI-compatible servers send arguments as a JSON string; some local
    proxies send an already-decoded object. Empty/invalid values become {}."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def accumulate_stream_arguments(existing: str, chunk) -> str:
    """Merge one streamed tool-call arguments delta into a JSON string."""
    if chunk is None or chunk == "":
        return existing
    if isinstance(chunk, dict):
        merged = parse_tool_arguments(existing)
        merged.update(chunk)
        return json.dumps(merged)
    if not isinstance(chunk, str):
        chunk = str(chunk)
    if not existing:
        return chunk
    # If we already stored a complete object, merge rather than concatenate.
    if existing.lstrip().startswith("{") and chunk.lstrip().startswith("{"):
        merged = parse_tool_arguments(existing)
        extra = parse_tool_arguments(chunk)
        if extra:
            merged.update(extra)
            return json.dumps(merged)
    return existing + chunk


def extract_tool_call_from_text(text: str) -> dict | None:
    """Parse text outputs where an LLM outputted raw tool markup (e.g. <tool>...</tool> or raw JSON) instead of native tool_calls."""
    if not text or not isinstance(text, str):
        return None
    # Pattern 1: <tool>{"name": "...", "arguments": {...}}</tool> or <tool_call>...</tool_call>
    tool_match = re.search(r'<(?:tool|tool_call|function_call)>\s*({.*?})\s*</(?:tool|tool_call|function_call)>', text, re.DOTALL)
    if tool_match:
        try:
            data = json.loads(tool_match.group(1))
            name = data.get("name") or data.get("tool_name") or data.get("function")
            args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
            if name:
                return {"tool_name": name, "tool_args": parse_tool_arguments(args)}
        except Exception:
            pass

    # Pattern 2: Raw JSON like {"name": "shell_exec", "arguments": {...}}
    json_match = re.search(r'{\s*"name"\s*:\s*"([a-zA-Z0-9_]+)"\s*,\s*"(?:arguments|args|parameters)"\s*:\s*({.*?})\s*}', text, re.DOTALL)
    if json_match:
        name = json_match.group(1)
        args_str = json_match.group(2)
        try:
            args = parse_tool_arguments(args_str)
            return {"tool_name": name, "tool_args": args}
        except Exception:
            pass
    return None


# Normalizers
def normalize_openai(response_json):
    choice = response_json["choices"][0]
    message = choice["message"]
    if "tool_calls" in message and message["tool_calls"]:
        tool_call = message["tool_calls"][0]
        args = parse_tool_arguments(tool_call["function"].get("arguments", {}))
        return {
            "type": "tool_call",
            "content": None,
            "tool_name": tool_call["function"]["name"],
            "tool_args": args
        }
    else:
        content = message.get("content") or ""
        text_tc = extract_tool_call_from_text(content)
        if text_tc:
            return {
                "type": "tool_call",
                "content": None,
                "tool_name": text_tc["tool_name"],
                "tool_args": text_tc["tool_args"]
            }
        return {
            "type": "text",
            "content": content,
            "tool_name": None,
            "tool_args": None
        }

# Provider Calls
def call_gemini(history, system_prompt=None):
    if not GEMINI_API_KEY:
        raise ValueError("Gemini API key is not configured.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    contents = map_history_to_gemini(history)
    
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt or DEFAULT_SYSTEM_PROMPT}]},
        "contents": contents,
        "tools": [{
            "function_declarations": get_all_tools_schema()
        }]
    }
    
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    
    if response.status_code != 200:
        raise RuntimeError(f"Gemini API failed with status {response.status_code}: {response.text}")
        
    res_json = response.json()
    try:
        parts = res_json["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response structure: {res_json}")
        
    # Check for functionCall
    for part in parts:
        if "functionCall" in part:
            return {
                "type": "tool_call",
                "content": None,
                "tool_name": part["functionCall"]["name"],
                "tool_args": part["functionCall"].get("args", {}),
                "thought_signature": part.get("thoughtSignature")
            }
            
    # Fallback to text
    for part in parts:
        if "text" in part:
            return {
                "type": "text",
                "content": part["text"],
                "tool_name": None,
                "tool_args": None
            }
            
    return {"type": "text", "content": "", "tool_name": None, "tool_args": None}

def call_pollinations(history, system_prompt=None):
    if not POLLINATIONS_API_KEY:
        raise ValueError("Pollinations API key is not configured.")
        
    url = "https://text.pollinations.ai/openai"
    messages = map_history_to_openai(history, system_prompt)
    
    payload = {
        "model": "openai",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Pollinations API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())

def call_openai_compatible(url, headers, payload, stream_callback=None, timeout=25):
    """Generic OpenAI SSE streaming wrapper supporting real-time token streaming and tool calls."""
    use_stream = stream_callback is not None
    payload["stream"] = use_stream

    if not use_stream:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"API request to {url} failed with status {response.status_code}: {response.text}")
        return normalize_openai(response.json())

    response = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"Streaming API request to {url} failed with status {response.status_code}: {response.text}")

    accumulated_content = ""
    tool_call_builder = {"id": "", "name": "", "arguments": ""}
    has_tool_call = False
    in_think = False

    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8", errors="replace")
        if not line_str.startswith("data: "):
            continue
        data_str = line_str[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            choices = data.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought")
            content_chunk = delta.get("content")

            if reasoning_chunk:
                if not in_think:
                    in_think = True
                    accumulated_content += "<think>"
                    if stream_callback:
                        try:
                            stream_callback("<think>")
                        except Exception:
                            break

                accumulated_content += reasoning_chunk
                if stream_callback:
                    try:
                        stream_callback(reasoning_chunk)
                    except Exception:
                        break

            if content_chunk:
                if in_think:
                    in_think = False
                    accumulated_content += "</think>"
                    if stream_callback:
                        try:
                            stream_callback("</think>")
                        except Exception:
                            break

                accumulated_content += content_chunk
                if stream_callback:
                    try:
                        stream_callback(content_chunk)
                    except Exception:
                        break

            tool_calls = delta.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                has_tool_call = True
                tc = tool_calls[0]
                if tc.get("id"):
                    tool_call_builder["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_call_builder["name"] = fn["name"]
                if "arguments" in fn:
                    tool_call_builder["arguments"] = accumulate_stream_arguments(
                        tool_call_builder["arguments"], fn.get("arguments")
                    )
        except Exception:
            pass

    if in_think:
        accumulated_content += "</think>"
        if stream_callback:
            try:
                stream_callback("</think>")
            except Exception:
                pass

    if has_tool_call and tool_call_builder["name"]:
        parsed_args = parse_tool_arguments(tool_call_builder["arguments"])
        return {
            "type": "tool_call",
            "content": None,
            "tool_name": tool_call_builder["name"],
            "tool_args": parsed_args
        }

    return {
        "type": "text",
        "content": accumulated_content,
        "tool_name": None,
        "tool_args": None
    }


def call_groq(history, system_prompt=None, stream_callback=None):
    if not GROQ_API_KEY:
        raise ValueError("Groq API key is not configured.")
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    messages = map_history_to_openai(history, system_prompt)
    
    payload = {
        "model": "qwen/qwen3.8-27b",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    
    return call_openai_compatible(url, headers, payload, stream_callback=stream_callback, timeout=20)


def call_openrouter(history, system_prompt=None, stream_callback=None):
    if not OPENROUTER_API_KEY:
        raise ValueError("OpenRouter API key is not configured.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    messages = map_history_to_openai(history, system_prompt)
    
    payload = {
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "messages": messages,
        "tools": get_openai_tools(),
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://harvis.local",
        "X-Title": "HARVIS Assistant"
    }
    
    return call_openai_compatible(url, headers, payload, stream_callback=stream_callback, timeout=20)


def call_cerebras(history, system_prompt=None, stream_callback=None):
    if not CEREBRAS_API_KEY:
        raise ValueError("Cerebras API key is not configured.")
        
    url = "https://api.cerebras.ai/v1/chat/completions"
    messages = map_history_to_openai(history[-10:], system_prompt)
    
    payload = {
        "model": "llama3.1-8b",
        "messages": messages
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CEREBRAS_API_KEY}"
    }
    
    return call_openai_compatible(url, headers, payload, stream_callback=stream_callback, timeout=15)


def call_mistral(history, system_prompt=None, stream_callback=None):
    if not MISTRAL_API_KEY:
        raise ValueError("Mistral API key is not configured.")
        
    url = "https://api.mistral.ai/v1/chat/completions"
    messages = map_history_to_openai(history, system_prompt)
    
    payload = {
        "model": "mistral-small-latest",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }
    
    return call_openai_compatible(url, headers, payload, stream_callback=stream_callback, timeout=20)

def call_cohere(history, system_prompt=None):
    if not COHERE_API_KEY:
        raise ValueError("Cohere API key is not configured.")
        
    url = "https://api.cohere.ai/v2/chat"
    messages = map_history_to_openai(history, system_prompt)
    
    payload = {
        "model": "command-r-plus-08-2024",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {COHERE_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Cohere API failed with status {response.status_code}: {response.text}")
        
    res_json = response.json()
    message = res_json.get("message", {})
    if "tool_calls" in message and message["tool_calls"]:
        tool_call = message["tool_calls"][0]
        args = tool_call.get("parameters", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        return {
            "type": "tool_call",
            "content": None,
            "tool_name": tool_call["function"]["name"],
            "tool_args": args
        }
    elif "content" in message and message["content"]:
        content_parts = message["content"]
        text_content = ""
        for part in content_parts:
            if part.get("type") == "text":
                text_content += part.get("text", "")
        return {
            "type": "text",
            "content": text_content,
            "tool_name": None,
            "tool_args": None
        }
        
    return {"type": "text", "content": "", "tool_name": None, "tool_args": None}

def _omniroute_routed_id(response, body=None, stream_model=None):
    """Best-effort upstream model id from OmniRoute headers or body."""
    if response is not None:
        for key, value in response.headers.items():
            lk = key.lower()
            if not value:
                continue
            if (
                lk in {
                    "x-omniroute-model",
                    "x-omniroute-upstream-model",
                    "x-omniroute-selected-model",
                    "x-selected-model",
                    "x-model-id",
                }
                or "upstream-model" in lk
                or (lk.startswith("x-omniroute-") and lk.endswith("model"))
            ):
                return value
    if isinstance(body, dict) and body.get("model"):
        return body["model"]
    return stream_model


def _omniroute_once(history, system_prompt, stream_callback, enable_tools, model, reasoning_effort, extra_tools=None):
    url = "http://localhost:20128/v1/chat/completions"
    messages = map_history_to_openai(history, system_prompt)
    use_stream = stream_callback is not None
    payload = {
        "model": model or "auto/smart:pro",
        "stream": use_stream,
        "messages": messages,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if enable_tools:
        payload["tools"] = get_openai_tools()
    if extra_tools:
        payload["tools"] = list(payload.get("tools", [])) + [
            {"type": "function", "function": t} for t in extra_tools
        ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OMNIROUTE_API_KEY}"
    }

    # Capable models are slow. The old 8s cap made OmniRoute fail over to Groq.
    timeout = (15, 180) if reasoning_effort else (10, 90)
    logger.info(
        "OmniRoute model=%s reasoning_effort=%s timeout=%s",
        payload["model"],
        reasoning_effort or "off",
        timeout,
    )

    if not use_stream:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"OmniRoute API failed with status {response.status_code}: {response.text}")
        body = response.json()
        result = normalize_openai(body)
        result["routed_model"] = _omniroute_routed_id(response, body) or payload["model"]
        return result

    response = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"OmniRoute API failed with status {response.status_code}: {response.text}")

    accumulated_content = ""
    tool_call_builder = {"id": "", "name": "", "arguments": ""}
    has_tool_call = False
    stream_model = None

    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8", errors="replace")
        if not line_str.startswith("data: "):
            continue
        data_str = line_str[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            if data.get("model"):
                stream_model = data["model"]
            choices = data.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            content_chunk = delta.get("content")
            if content_chunk:
                accumulated_content += content_chunk
                if stream_callback:
                    stream_callback(content_chunk)

            tool_calls = delta.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                has_tool_call = True
                tc = tool_calls[0]
                if tc.get("id"):
                    tool_call_builder["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_call_builder["name"] = fn["name"]
                if "arguments" in fn:
                    tool_call_builder["arguments"] = accumulate_stream_arguments(
                        tool_call_builder["arguments"], fn.get("arguments")
                    )
        except Exception:
            pass

    routed = _omniroute_routed_id(response, stream_model=stream_model) or payload["model"]
    if has_tool_call and tool_call_builder["name"]:
        parsed_args = parse_tool_arguments(tool_call_builder["arguments"])
        return {
            "type": "tool_call",
            "content": None,
            "tool_name": tool_call_builder["name"],
            "tool_args": parsed_args,
            "routed_model": routed,
        }

    if not has_tool_call and accumulated_content:
        text_tc = extract_tool_call_from_text(accumulated_content)
        if text_tc:
            return {
                "type": "tool_call",
                "content": None,
                "tool_name": text_tc["tool_name"],
                "tool_args": text_tc["tool_args"],
                "routed_model": routed,
            }

    return {
        "type": "text",
        "content": accumulated_content,
        "tool_name": None,
        "tool_args": None,
        "routed_model": routed,
    }


def call_omniroute(history, system_prompt=None, stream_callback=None, enable_tools=True, model="auto/smart:pro", reasoning_effort=None, extra_tools=None):
    if not OMNIROUTE_API_KEY:
        raise ValueError("OmniRoute API key is not configured.")

    # Do not strip :pro on empty pools. That fail-open includes flash/lite models.
    return _omniroute_once(
        history, system_prompt, stream_callback, enable_tools, model, reasoning_effort,
        extra_tools=extra_tools,
    )


def call_opus_proxy(history, system_prompt=None, stream_callback=None, enable_tools=True, model="claude-fable-5", **kwargs):
    if not OPUS_PROXY_API_KEY:
        raise ValueError("Opus Proxy API key is not configured.")

    url = "https://opus.abhibots.com/v1/chat/completions"
    messages = map_history_to_openai(history, system_prompt)

    payload = {
        "model": model,
        "messages": messages,
    }
    if enable_tools:
        payload["tools"] = get_openai_tools()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPUS_PROXY_API_KEY}",
        "x-api-key": OPUS_PROXY_API_KEY,
    }

    logger.info(f"Opus Proxy model={model}")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=(10, 90))
        if response.status_code != 200:
            raise RuntimeError(f"Opus Proxy API failed with status {response.status_code}: {response.text}")
        
        body = response.json()
        result = normalize_openai(body)
        result["routed_model"] = model
        return result
    except Exception as e:
        fallback_model = "claude-opus-4-8" if model != "claude-opus-4-8" else "claude-sonnet-4-6"
        logger.warning(f"Opus Proxy model {model} failed ({e}). Retrying with fallback {fallback_model}...")
        payload["model"] = fallback_model
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=(10, 90))
            if response.status_code == 200:
                body = response.json()
                result = normalize_openai(body)
                result["routed_model"] = fallback_model
                return result
        except Exception:
            pass
        raise e

