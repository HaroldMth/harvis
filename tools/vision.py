"""Vision analysis tools for HARVIS - image understanding and analysis."""

import os
import base64
import logging
import requests
from typing import Optional, Dict, Any
from config import OMNIROUTE_API_KEY, GEMINI_API_KEY

logger = logging.getLogger("harvis.vision")


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """Encode an image file to base64 string."""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encode image {image_path}: {e}")
        return None


def _call_gemini_vision(base64_image: str, mime_type: str, prompt: str) -> Optional[str]:
    """Analyze an image using Gemini Vision."""
    if not GEMINI_API_KEY:
        return None
    models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"]
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64_image}}
            ]
        }]
    }
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, json=payload, timeout=25)
            if r.status_code == 200:
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.warning(f"Gemini vision call to {model} failed: {e}")
    return None


def analyze_image(image_path: str, prompt: str = "Describe this image in detail.", model: str = "auto/vision:pro") -> Dict[str, Any]:
    """
    Analyze an image using vision models (Gemini Vision primary, OmniRoute fallback).
    
    Args:
        image_path: Path to the image file
        prompt: Text prompt to guide the analysis
        model: OmniRoute model to use for fallback
    
    Returns:
        Dict with success status and analysis results
    """
    if not os.path.exists(image_path):
        return {"success": False, "error": f"Image file not found: {image_path}"}
    
    # Encode image to base64
    base64_image = encode_image_to_base64(image_path)
    if not base64_image:
        return {"success": False, "error": "Failed to encode image to base64"}
    
    # Get file extension for MIME type
    ext = os.path.splitext(image_path)[1].lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg', 
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    mime_type = mime_types.get(ext, 'image/png')

    # Priority 1: Gemini Vision (direct, reliable, no local port needed)
    if GEMINI_API_KEY:
        try:
            content = _call_gemini_vision(base64_image, mime_type, prompt)
            if content:
                return {
                    "success": True,
                    "analysis": content,
                    "output": content,
                    "model_used": "gemini-3.5-flash-lite",
                    "image_path": image_path
                }
        except Exception as e:
            logger.warning(f"Direct Gemini vision failed, trying OmniRoute: {e}")
    
    # Priority 2: OmniRoute (if configured and running)
    if OMNIROUTE_API_KEY:
        url = "http://localhost:20128/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ],
            "max_tokens": 1000
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OMNIROUTE_API_KEY}"
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=20)
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "success": True,
                    "analysis": content,
                    "output": content,
                    "model_used": model,
                    "image_path": image_path
                }
        except Exception as e:
            logger.error(f"OmniRoute vision failed: {e}")

    return {"success": False, "error": "No vision provider succeeded (Gemini Vision and OmniRoute unavailable)"}


def collaborative_vision_analysis(image_path: str, user_question: str) -> Dict[str, Any]:
    """
    Two-step vision analysis: vision model describes image, then text model answers user question.
    
    This implements the "image gen model will chat with the text model for clarity" workflow.
    
    Args:
        image_path: Path to the image file
        user_question: The user's specific question about the image
    
    Returns:
        Dict with success status and collaborative analysis results
    """
    # Step 1: Vision model provides detailed description
    vision_prompt = f"Describe this image in detail, focusing on elements that might be relevant to answering: {user_question}"
    vision_result = analyze_image(image_path, vision_prompt, model="auto/vision:pro")
    
    if not vision_result["success"]:
        return vision_result
    
    image_description = vision_result["analysis"]
    
    # Step 2: Text model uses the description to answer the specific question
    from brain.providers import call_omniroute
    
    text_prompt = f"""
Based on the following image description, please answer the user's question:

IMAGE DESCRIPTION:
{image_description}

USER QUESTION:
{user_question}

Provide a clear, specific answer based on the visual information described above.
"""
    
    try:
        text_response = call_omniroute(
            history=[{"role": "user", "content": text_prompt}],
            model="auto/smart",  # Use smart model for better reasoning
            reasoning_effort="medium",
            enable_tools=False
        )
        
        if text_response.get("type") == "text":
            return {
                "success": True,
                "image_description": image_description,
                "answer": text_response["content"],
                "workflow": "vision_description + text_analysis"
            }
        else:
            return {
                "success": False,
                "error": f"Unexpected response type from text model: {text_response.get('type')}",
                "image_description": image_description
            }
            
    except Exception as e:
        logger.error(f"Text analysis failed: {e}")
        return {
            "success": False,
            "error": f"Text analysis failed: {str(e)}",
            "image_description": image_description
        }


def screenshot_and_analyze(prompt: str = "Analyze this screenshot", copy_to_clipboard: bool = False) -> Dict[str, Any]:
    """
    Take a screenshot and analyze it using vision capabilities.
    
    Args:
        prompt: Analysis prompt for the vision model
        copy_to_clipboard: Whether to copy screenshot path to clipboard
    
    Returns:
        Dict with screenshot path and analysis results
    """
    from tools.desktop import screenshot
    
    # Take screenshot
    screenshot_result = screenshot(copy_to_clipboard=copy_to_clipboard)
    
    if not screenshot_result["success"]:
        return screenshot_result
    
    screenshot_path = screenshot_result.get("path")  # HARVIS_GEO_PATCH
    
    # Analyze the screenshot
    analysis_result = analyze_image(screenshot_path, prompt)
    
    if analysis_result["success"]:
        analysis_result["screenshot_path"] = screenshot_path
        analysis_result["output"] = analysis_result.get("analysis", "")
        return analysis_result
    else:
        return {
            "success": False,
            "error": f"Screenshot taken but analysis failed: {analysis_result['error']}",
            "screenshot_path": screenshot_path
        }