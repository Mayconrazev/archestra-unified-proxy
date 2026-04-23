```python
import httpx
import json
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from typing import Dict, Any

app = FastAPI(title="Maia Unified LLM Proxy")

# Mapeamento de Provedores
PROVIDERS = {
    "claude": {
        "url": "https://api.anthropic.com/v1/messages",
        "headers": lambda key: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "headers": lambda key: {"Content-Type": "application/json"}
    }
}

def map_openai_to_anthropic(body: Dict[str, Any]) -> Dict[str, Any]:
    """Converte o corpo OpenAI para o formato Anthropic Messages API."""
    return {
        "model": body.get("model", "claude-3-sonnet-20240229"),
        "messages": [{"role": m["role"], "content": m["content"]} for m in body["messages"] if m["role"] != "system"],
        "system": next((m["content"] for m in body["messages"] if m["role"] == "system"), ""),
        "max_tokens": body.get("max_tokens", 1024),
        "stream": body.get("stream", False),
        "temperature": body.get("temperature", 1.0)
    }

async def stream_anthropic(response, model_name):
    """Gerador de Stream que traduz Anthropic SSE para OpenAI SSE."""
    async for line in response.aiter_lines():
        if not line or not line.startswith("data: "): continue
        data = json.loads(line[6:])
        
        # Traduzindo tipos de eventos
        content = ""
        if data["type"] == "content_block_delta":
            content = data["delta"].get("text", "")
        
        chunk = {
            "id": "chatcmpl-" + str(time.time()),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    yield "data: [DONE]\n\n"

@app.post("/v1/chat/completions")
async def unified_proxy(request: Request):
    body = await request.json()
    model = body.get("model", "")
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")

    if not api_key:
        raise HTTPException(status_code=401, detail="API Key missing")

    # Lógica de Roteamento Simples
    if "claude" in model or "gpt" in model: # Exemplo: mapeando gpt para claude
        target = PROVIDERS["claude"]
        payload = map_openai_to_anthropic(body)
        headers = target["headers"](api_key)
        
        if body.get("stream"):
            client = httpx.AsyncClient()
            # Usando requisição de stream
            req = client.build_request("POST", target["url"], json=payload, headers=headers)
            resp = await client.send(req, stream=True)
            return StreamingResponse(stream_anthropic(resp, model), media_type="text/event-stream")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(target["url"], json=payload, headers=headers)
            data = resp.json()
            
            # Resposta Final formatada como OpenAI
            return {
                "id": data["id"],
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
"message": {"role": "assistant", "content": data["content"][0]["text"]},
                    "finish_reason": "stop", "index": 0
                }]
            }

    return {"error": "Model not supported yet"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```
