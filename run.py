"""Start the chatbot: python run.py, then open http://127.0.0.1:8000"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"ChatBot running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port)
