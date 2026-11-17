import os
import uvicorn

if __name__ == "__main__":
    port_raw = os.environ.get("PORT", "8000").strip()
    try:
        port = int(port_raw)
    except (ValueError, TypeError):
        # Handle cases where $PORT string was passed unexpanded
        port = 8000

    print(f"Starting NapsterTec Engine on 0.0.0.0:{port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, workers=1)
