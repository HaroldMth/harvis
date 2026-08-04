import json
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
    OMNIROUTE_API_KEY
)
from tools.registry import get_all_tools_schema
from brain.prompts import SYSTEM_PROMPT

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
        
        is_json = False
        parsed_content = None
        if content.startswith("{") or content.startswith("["):
            try:
                parsed_content = json.loads(content)
                is_json = True
            except Exception:
                pass
                
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
            awaiting_tool_response = False
        elif role == "assistant":
            if is_json:
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
                contents.append({"role": "model", "parts": [{"text": content}]})
                awaiting_tool_response = False
        elif role == "tool":
            if awaiting_tool_response and is_json and parsed_content.get("tool_name") is not None:
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

def map_history_to_openai(history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    active_tool_call_id = None
    for idx, msg in enumerate(history):
        role = msg["role"]
        content = msg["content"]
        
        is_json = False
        parsed_content = None
        if content.startswith("{") or content.startswith("["):
            try:
                parsed_content = json.loads(content)
                is_json = True
            except Exception:
                pass
                
        if role == "user":
            messages.append({"role": "user", "content": content})
            active_tool_call_id = None
        elif role == "assistant":
            if is_json:
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
            if is_json and parsed_content.get("tool_name") is not None:
                tool_name = parsed_content["tool_name"]
                tool_result = parsed_content.get("result", "")
            else:
                tool_result = content
                
            messages.append({
                "role": "tool",
                "tool_call_id": active_tool_call_id,
                "name": tool_name,
                "content": json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result
            })
            active_tool_call_id = None
    return messages

# Normalizers
def normalize_openai(response_json):
    choice = response_json["choices"][0]
    message = choice["message"]
    if "tool_calls" in message and message["tool_calls"]:
        tool_call = message["tool_calls"][0]
        args = tool_call["function"].get("arguments", {})
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
    else:
        return {
            "type": "text",
            "content": message.get("content") or "",
            "tool_name": None,
            "tool_args": None
        }

# Provider Calls
def call_gemini(history):
    if not GEMINI_API_KEY:
        raise ValueError("Gemini API key is not configured.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    contents = map_history_to_gemini(history)
    
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
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

def call_pollinations(history):
    if not POLLINATIONS_API_KEY:
        raise ValueError("Pollinations API key is not configured.")
        
    url = "https://text.pollinations.ai/openai"
    messages = map_history_to_openai(history)
    
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

def call_groq(history):
    if not GROQ_API_KEY:
        raise ValueError("Groq API key is not configured.")
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    messages = map_history_to_openai(history)
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Groq API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())

def call_openrouter(history):
    if not OPENROUTER_API_KEY:
        raise ValueError("OpenRouter API key is not configured.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    messages = map_history_to_openai(history)
    
    payload = {
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "messages": messages,
        "tools": get_openai_tools(),
        "max_tokens": 300
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())

def call_cerebras(history):
    if not CEREBRAS_API_KEY:
        raise ValueError("Cerebras API key is not configured.")
        
    url = "https://api.cerebras.ai/v1/chat/completions"
    # Note: Cerebras has small context (max 8192) - trim history if needed
    messages = map_history_to_openai(history[-10:]) # last 10 messages only
    
    payload = {
        "model": "llama3.1-8b",
        "messages": messages
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CEREBRAS_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Cerebras API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())

def call_mistral(history):
    if not MISTRAL_API_KEY:
        raise ValueError("Mistral API key is not configured.")
        
    url = "https://api.mistral.ai/v1/chat/completions"
    messages = map_history_to_openai(history)
    
    payload = {
        "model": "mistral-small-latest",
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"Mistral API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())

def call_cohere(history):
    if not COHERE_API_KEY:
        raise ValueError("Cohere API key is not configured.")
        
    url = "https://api.cohere.ai/v2/chat"
    messages = map_history_to_openai(history)
    
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

def call_omniroute(history):
    if not OMNIROUTE_API_KEY:
        raise ValueError("OmniRoute API key is not configured.")
        
    url = "http://localhost:20128/v1/chat/completions"
    messages = map_history_to_openai(history)
    
    payload = {
        "model": "auto",
        "stream": False,
        "messages": messages,
        "tools": get_openai_tools()
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OMNIROUTE_API_KEY}"
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"OmniRoute API failed with status {response.status_code}: {response.text}")
        
    return normalize_openai(response.json())
