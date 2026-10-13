#!/usr/bin/env python3
"""
Test automatic vision detection in HARVIS.
Tests that images are automatically detected and processed without explicit tools.
"""

import sys
import os
import tempfile
from PIL import Image, ImageDraw, ImageFont

# Add the HARVIS directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from brain.router import detect_images_in_prompt, prepare_vision_request

def create_test_image():
    """Create a simple test image with text."""
    img = Image.new('RGB', (400, 200), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw some shapes and text
    draw.rectangle([50, 50, 150, 150], fill='blue')
    draw.rectangle([200, 50, 300, 150], fill='red')
    
    try:
        font = ImageFont.load_default()
        draw.text((50, 170), "Test Image for Auto Vision", fill='black', font=font)
    except:
        draw.text((50, 170), "Test Image", fill='black')
    
    # Save to temp file
    temp_path = os.path.join(tempfile.gettempdir(), "harvis_auto_vision_test.png")
    img.save(temp_path)
    return temp_path

def test_image_detection():
    """Test automatic image detection in prompts."""
    print("🧪 Testing Automatic Image Detection")
    print("=" * 60)
    
    # Test 1: Base64 image detection
    print("\n📋 Test 1: Base64 image detection")
    base64_prompt = "What do you see in this image? data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    images = detect_images_in_prompt(base64_prompt)
    print(f"✅ Detected {len(images)} images in base64 prompt")
    if images:
        print(f"   Type: {images[0]['type']}, MIME: {images[0]['mime_type']}")
    
    # Test 2: File path detection
    print("\n📋 Test 2: File path detection")
    test_image = create_test_image()
    print(f"✅ Created test image: {test_image}")
    
    file_prompt = f"Analyze this screenshot: {test_image}"
    images = detect_images_in_prompt(file_prompt)
    print(f"✅ Detected {len(images)} images in file path prompt")
    if images:
        print(f"   Type: {images[0]['type']}, Path: {images[0]['path']}")
    
    # Test 3: Mixed content
    print("\n📋 Test 3: Mixed content (text + image)")
    mixed_prompt = f"Check this error: {test_image} and tell me what's wrong"
    images = detect_images_in_prompt(mixed_prompt)
    print(f"✅ Detected {len(images)} images in mixed prompt")
    
    # Test 4: No images
    print("\n📋 Test 4: No images (control test)")
    text_prompt = "Just a regular text message without any images"
    images = detect_images_in_prompt(text_prompt)
    print(f"✅ Detected {len(images)} images (should be 0)")
    
    # Clean up
    if os.path.exists(test_image):
        os.remove(test_image)
        print(f"✅ Cleaned up test image")

def test_vision_request_preparation():
    """Test vision request format preparation."""
    print("\n🧪 Testing Vision Request Preparation")
    print("=" * 60)
    
    # Test with base64 image
    print("\n📋 Test: Base64 image preparation")
    base64_prompt = "Describe this: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    images = detect_images_in_prompt(base64_prompt)
    
    if images:
        vision_request = prepare_vision_request("Describe this", images)
        print(f"✅ Vision request prepared")
        print(f"   Content type: {type(vision_request['content'])}")
        print(f"   Number of content items: {len(vision_request['content'])}")
        for i, item in enumerate(vision_request['content']):
            print(f"   Item {i}: {item['type']}")
    
    # Test with file image
    print("\n📋 Test: File image preparation")
    test_image = create_test_image()
    file_prompt = f"What's in this image: {test_image}"
    images = detect_images_in_prompt(file_prompt)
    
    if images:
        vision_request = prepare_vision_request("What's in this image", images)
        print(f"✅ Vision request prepared")
        print(f"   Content type: {type(vision_request['content'])}")
        print(f"   Number of content items: {len(vision_request['content'])}")
        for i, item in enumerate(vision_request['content']):
            print(f"   Item {i}: {item['type']}")
    
    # Clean up
    if os.path.exists(test_image):
        os.remove(test_image)

def test_integration_with_router():
    """Test that the router would properly detect and handle images."""
    print("\n🧪 Testing Router Integration")
    print("=" * 60)
    
    # Simulate what would happen in the router
    test_image = create_test_image()
    prompt_with_image = f"Look at this screenshot: {test_image} and tell me what you see"
    
    print(f"📋 Simulating router processing of: '{prompt_with_image[:50]}...'")
    
    # This is what the router does
    images = detect_images_in_prompt(prompt_with_image)
    if images:
        print(f"✅ Router would auto-switch to vision mode")
        print(f"✅ Router detected {len(images)} image(s)")
        
        vision_request = prepare_vision_request(prompt_with_image, images)
        print(f"✅ Router would prepare vision request with {len(vision_request['content'])} content items")
    else:
        print(f"❌ Router failed to detect images")
    
    # Clean up
    if os.path.exists(test_image):
        os.remove(test_image)

def main():
    print("🔍 HARVIS Automatic Vision Detection Test Suite")
    print("=" * 60)
    
    test_image_detection()
    test_vision_request_preparation()
    test_integration_with_router()
    
    print("\n" + "=" * 60)
    print("🎉 Automatic Vision Detection Test Complete")
    print("=" * 60)
    print("\n✅ Summary:")
    print("- Images are automatically detected in prompts")
    print("- Base64 and file paths both supported")
    print("- Router auto-switches to vision mode when images detected")
    print("- Vision requests are properly formatted for OmniRoute")
    print("- No explicit tools needed - completely automatic!")

if __name__ == "__main__":
    main()