import hashlib
import json
import logging
from db.store import get_history, get_cache, set_cache, update_provider_status
from brain.providers import (
    call_gemini,
    call_pollinations,
    call_groq,
    call_openrouter,
    call_cerebras,
    call_mistral,
    call_cohere
)

logger = logging.getLogger("harvis.router")

PROVIDERS = [
    {"name": "gemini", "func": call_gemini},
    {"name": "pollinations", "func": call_pollinations},
    {"name": "groq", "func": call_groq},
    {"name": "openrouter", "func": call_openrouter},
    {"name": "cerebras", "func": call_cerebras},
    {"name": "mistral", "func": call_mistral},
    {"name": "cohere", "func": call_cohere}
]

def calculate_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def ask(session_id, prompt, role="user", use_cache=True):
    """
    Queries the brain fallback chain for a response, checking the SQLite cache first.
    Returns: normalized response dict:
    {
      "type": "text" | "tool_call",
      "content": "...",
      "tool_name": "...",
      "tool_args": {...},
      "model": "..."
    }
    """
    # 1. Check cache first
    # Keep cache entries distinct by message role. Older versions keyed only on
    # text, so a prior tool response or a cached tool call could be replayed for
    # an unrelated turn (for example, a plain "hi").
    prompt_hash = calculate_hash(f"{role}:{prompt}")
    if use_cache and role == "user":
        cached = get_cache(prompt_hash)
        if cached:
            try:
                res = json.loads(cached["response"])
                # Tool calls have side effects and must always be chosen fresh.
                if res.get("type") == "text":
                    res["model"] = cached["model"]
                    logger.info(f"Cache hit! Model: {cached['model']}")
                    return res
            except Exception as e:
                logger.warning(f"Failed to parse cached response: {e}")

    # 2. Get history from SQLite
    # Load recent conversation context, then append the new prompt in memory if not already present
    history = get_history(session_id)
    if prompt:
        if not history or history[-1]["content"] != prompt or history[-1]["role"] != role:
            history.append({"role": role, "content": prompt})

    # 3. Try each provider in the strict fallback order
    for provider in PROVIDERS:
        name = provider["name"]
        func = provider["func"]
        
        logger.info(f"Attempting provider: {name}...")
        try:
            res = func(history)
            # Record success in DB
            update_provider_status(name, True)
            res["model"] = name
            
            # Write response to cache
            # Cache only plain responses. Replaying a cached tool call would
            # execute an action without a new model decision.
            if use_cache and role == "user" and res.get("type") == "text":
                set_cache(prompt_hash, json.dumps(res), name)
                
            logger.info(f"Provider {name} succeeded.")
            return res
        except Exception as e:
            logger.warning(f"Provider {name} failed: {e}")
            # Record failure in DB
            update_provider_status(name, False)
            
    # Fallback response when all providers fail
    err_res = {
        "type": "text",
        "content": "I'm sorry, but all of my brain connections are currently unavailable. Please check your network and API keys.",
        "tool_name": None,
        "tool_args": None,
        "model": "error"
    }
    return err_res
