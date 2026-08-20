import json
import requests

API_URL = "http://localhost:8000/v1/chat/completions/stream"

def run_agent_test(agent_name: str, prompt: str):
    print(f"\n{'='*60}")
    print(f"🤖 Testing Agent: {agent_name}")
    print(f"{'='*60}")

    # Removed the "model" field to match your strict CompletionRequest schema
    payload = {
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    try:
        response = requests.post(API_URL, json=payload, stream=True, timeout=60)

        # Handle validation or server errors explicitly
        if response.status_code != 200:
            print(f"❌ Error HTTP {response.status_code}: Unprocessable Content / Validation Failure")
            print("Detailed Server Diagnostic:")
            try:
                print(json.dumps(response.json(), indent=2))
            except Exception:
                print(response.text)
            return

        print("Response Stream Output:")
        print("-" * 40)
        
        # Stream response chunk by chunk
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode("utf-8")
                
                # Strip SSE prefix if present
                if decoded_line.startswith("data: "):
                    decoded_line = decoded_line[6:]
                
                if decoded_line.strip() == "[DONE]":
                    break
                
                print(decoded_line, end="", flush=True)

        print("\n" + "-" * 40)
        print("✅ Stream Completed Successfully.")

    except requests.exceptions.RequestException as e:
        print(f"❌ Connection Failure: {e}")

if __name__ == "__main__":
    print("Initializing NapsterTec Intelligence Engine (NIE) Agent Tests...\n")

    # 1. SecurityAuditor Agent Test (Triggers System Inspector Tool)
    run_agent_test(
        agent_name="🛡️ SecurityAuditor Agent (System Inspector Tool)",
        prompt="Perform a complete system inspection and report local hardware/OS details."
    )

    # 2. FullStackArchitect Agent Test (Triggers Code Runner Tool)
    run_agent_test(
        agent_name="🏗️ FullStackArchitect Agent (Code Runner Tool)",
        prompt="Execute Python code to compute the first 10 Fibonacci numbers and print the results."
    )