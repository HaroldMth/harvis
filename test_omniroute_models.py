#!/usr/bin/env python3
"""
Test script to compare different OmniRoute auto-combo models for intelligence and speed.
We'll test: auto, auto/smart, auto/fast, auto/coding with and without reasoning_effort.
"""

import time
import json
from brain.providers import call_omniroute

# Test prompts that require different levels of intelligence
TEST_PROMPTS = [
    {
        "name": "Simple Question",
        "prompt": "What is the capital of France?",
        "expected_complexity": "low"
    },
    {
        "name": "Complex Reasoning", 
        "prompt": "If a train leaves New York at 8 AM traveling at 60 mph, and another leaves Chicago at 9 AM traveling at 75 mph, and the distance between cities is 800 miles, at what time will they meet?",
        "expected_complexity": "medium"
    },
    {
        "name": "Code Analysis",
        "prompt": "Write a Python function to find the longest palindromic substring in a given string. Explain your approach and time complexity.",
        "expected_complexity": "high"
    },
    {
        "name": "Creative Reasoning",
        "prompt": "In a world where dreams can be recorded and shared, what would be the societal implications? Analyze economic, psychological, and ethical aspects.",
        "expected_complexity": "high"
    }
]

# Models to test
MODELS_TO_TEST = [
    {"model": "auto", "reasoning_effort": None, "description": "Balanced default"},
    {"model": "auto/smart", "reasoning_effort": None, "description": "Quality-first with exploration"},
    {"model": "auto/smart", "reasoning_effort": "high", "description": "Quality-first with high reasoning"},
    {"model": "auto/fast", "reasoning_effort": None, "description": "Low-latency prioritized"},
    {"model": "auto/coding", "reasoning_effort": None, "description": "Code-optimized"},
    {"model": "auto/coding", "reasoning_effort": "medium", "description": "Code-optimized with reasoning"},
]

def test_model(model_config, test_prompt):
    """Test a single model with a single prompt."""
    history = [{"role": "user", "content": test_prompt["prompt"]}]
    
    try:
        start_time = time.time()
        response = call_omniroute(
            history=history,
            model=model_config["model"],
            reasoning_effort=model_config["reasoning_effort"],
            enable_tools=False
        )
        end_time = time.time()
        
        latency = end_time - start_time
        
        if response.get("type") == "text":
            content = response.get("content", "")
            return {
                "success": True,
                "latency": latency,
                "content_length": len(content),
                "content_preview": content[:200] + "..." if len(content) > 200 else content
            }
        else:
            return {
                "success": False,
                "error": f"Unexpected response type: {response.get('type')}",
                "latency": latency
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "latency": time.time() - start_time
        }

def main():
    print("🧪 OmniRoute Model Comparison Test")
    print("=" * 60)
    
    results = []
    
    for model_config in MODELS_TO_TEST:
        print(f"\n📊 Testing: {model_config['description']}")
        print(f"   Model: {model_config['model']}, Reasoning: {model_config['reasoning_effort']}")
        print("-" * 60)
        
        model_results = {
            "config": model_config,
            "tests": []
        }
        
        for test_prompt in TEST_PROMPTS:
            print(f"   Testing: {test_prompt['name']}...")
            result = test_model(model_config, test_prompt)
            result["test_name"] = test_prompt["name"]
            result["expected_complexity"] = test_prompt["expected_complexity"]
            
            if result["success"]:
                print(f"   ✅ Success - Latency: {result['latency']:.2f}s, Length: {result['content_length']} chars")
            else:
                print(f"   ❌ Failed - {result['error']}")
            
            model_results["tests"].append(result)
        
        results.append(model_results)
    
    # Summary analysis
    print("\n" + "=" * 60)
    print("📈 SUMMARY ANALYSIS")
    print("=" * 60)
    
    for model_result in results:
        config = model_result["config"]
        tests = model_result["tests"]
        
        successful_tests = [t for t in tests if t["success"]]
        avg_latency = sum(t["latency"] for t in successful_tests) / len(successful_tests) if successful_tests else 0
        avg_length = sum(t["content_length"] for t in successful_tests) / len(successful_tests) if successful_tests else 0
        success_rate = len(successful_tests) / len(tests) * 100
        
        print(f"\n{config['description']}:")
        print(f"  Success Rate: {success_rate:.1f}%")
        print(f"  Avg Latency: {avg_latency:.2f}s")
        print(f"  Avg Response Length: {avg_length:.0f} chars")
    
    # Save detailed results
    with open("omniroute_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Detailed results saved to omniroute_test_results.json")

if __name__ == "__main__":
    main()