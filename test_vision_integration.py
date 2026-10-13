#!/usr/bin/env python3
"""
Test script to verify vision integration in HARVIS.
Tests the new vision tools and mode functionality.
"""

import os
import sys
import tempfile
from PIL import Image
import numpy as np

# Add the HARVIS directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools.vision import analyze_image, collaborative_vision_analysis, screenshot_and_analyze
from brain.modes import parse_slash, omniroute_options, MODES, ALIASES

def create_test_image():
    """Create a simple test image with text."""
    # Create a simple image with some text
    from PIL import Image, ImageDraw, ImageFont
    
    img = Image.new('RGB', (400, 200), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw some shapes and text
    draw.rectangle([50, 50, 150, 150], fill='blue')
    draw.rectangle([200, 50, 300, 150], fill='red')
    
    try:
        # Try to use a default font
        font = ImageFont.load_default()
        draw.text((50, 170), "Test Image for HARVIS Vision", fill='black', font=font)
    except:
        # Fallback if font loading fails
        draw.text((50, 170), "Test Image", fill='black')
    
    # Save to temp file
    temp_path = os.path.join(tempfile.gettempdir(), "harvis_vision_test.png")
    img.save(temp_path)
    return temp_path

def test_vision_mode():
    """Test that vision mode is properly configured."""
    print("🧪 Testing Vision Mode Configuration")
    print("=" * 60)
    
    # Check if vision is in MODES
    print(f"✅ Vision in MODES: {'vision' in MODES}")
    
    # Check aliases
    vision_aliases = [k for k, v in ALIASES.items() if v == "vision"]
    print(f"✅ Vision aliases: {vision_aliases}")
    
    # Check omniroute options
    vision_route = omniroute_options("vision")
    print(f"✅ Vision route config: {vision_route}")
    
    # Test slash parsing
    test_commands = ["/vision", "/image", "/see", "/look"]
    for cmd in test_commands:
        parsed = parse_slash(cmd)
        if parsed:
            print(f"✅ Parsed '{cmd}': {parsed}")
        else:
            print(f"❌ Failed to parse '{cmd}'")

def test_vision_tools():
    """Test the vision tools directly."""
    print("\n🧪 Testing Vision Tools")
    print("=" * 60)
    
    # Create test image
    print("Creating test image...")
    test_image = create_test_image()
    print(f"✅ Test image created: {test_image}")
    
    # Test basic image analysis
    print("\nTesting basic image analysis...")
    try:
        result = analyze_image(test_image, "Describe what you see in this image.")
        if result["success"]:
            print(f"✅ Basic analysis successful")
            print(f"   Analysis: {result['analysis'][:100]}...")
        else:
            print(f"❌ Basic analysis failed: {result['error']}")
    except Exception as e:
        print(f"❌ Basic analysis error: {e}")
    
    # Test collaborative analysis
    print("\nTesting collaborative vision analysis...")
    try:
        result = collaborative_vision_analysis(test_image, "What shapes and colors are in this image?")
        if result["success"]:
            print(f"✅ Collaborative analysis successful")
            print(f"   Description: {result['image_description'][:100]}...")
            print(f"   Answer: {result['answer'][:100]}...")
        else:
            print(f"❌ Collaborative analysis failed: {result['error']}")
    except Exception as e:
        print(f"❌ Collaborative analysis error: {e}")
    
    # Clean up
    if os.path.exists(test_image):
        os.remove(test_image)
        print(f"✅ Cleaned up test image")

def test_registry_integration():
    """Test that vision tools are in the registry."""
    print("\n🧪 Testing Registry Integration")
    print("=" * 60)
    
    try:
        from tools.registry import get_all_tools_schema, TOOL_DISPATCH
        
        all_tools = get_all_tools_schema()
        tool_names = [tool["name"] for tool in all_tools]
        
        vision_tools = ["analyze_image", "collaborative_vision_analysis", "screenshot_and_analyze"]
        
        for tool_name in vision_tools:
            if tool_name in tool_names:
                print(f"✅ {tool_name} in tools schema")
            else:
                print(f"❌ {tool_name} NOT in tools schema")
        
        # Check dispatch functions
        for tool_name in vision_tools:
            if tool_name in TOOL_DISPATCH:
                print(f"✅ {tool_name} in dispatch table")
            else:
                print(f"❌ {tool_name} NOT in dispatch table")
                
    except Exception as e:
        print(f"❌ Registry integration test failed: {e}")

def main():
    print("🔍 HARVIS Vision Integration Test Suite")
    print("=" * 60)
    
    test_vision_mode()
    test_vision_tools()
    test_registry_integration()
    
    print("\n" + "=" * 60)
    print("🎉 Vision Integration Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    main()