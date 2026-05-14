# handler.py
#
# Runpod Serverless handler for ComfyUI that:
# 1) accepts a base64 image OR an image_url
# 2) writes it into ComfyUI's input directory
# 3) injects the filename into your LoadImage node ("163")
# 4) submits the workflow to the local ComfyUI HTTP API
# 5) waits for completion and returns output images as base64
#
# Job input format (example):
# {
#   "input": {
#     "image_base64": "data:image/png;base64,iVBORw0K...",  // optional if image_url provided
#     "image_url": "https://example.com/image.png",         // optional if image_base64 provided
#     "workflow": { ... your workflow JSON ... },
#     "prompt": "optional prompt override",
#     "timeout_s": 300,              // overall job wait for prompt completion
#     "startup_timeout_s": 180       // time to wait for ComfyUI to become ready (cold start)
#   }
# }
#
# Notes:
# - Assumes ComfyUI server is reachable at http://127.0.0.1:8188 by default
#   (override via COMFY_HOST / COMFY_PORT env vars).
# - Uses ONLY HTTP endpoints (no ComfyUI python internals).
# - Probes common ComfyUI input paths.

import base64
import json
import os
import time
import uuid
import urllib.request
import urllib.error
from urllib.parse import urlencode, urlparse
from typing import Any, Dict, Optional, Tuple, List

import runpod


COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 60
) -> Dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTP {e.code} calling {url}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Error calling {url}: {e}") from e


def _http_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTP {e.code} calling {url}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Error calling {url}: {e}") from e


def find_comfy_dirs() -> Tuple[str, str]:
    """
    Returns (comfy_root, comfy_input_dir)
    Probes common locations used by Runpod ComfyUI images.
    """
    candidates = [
        ("/comfyui", "/comfyui/input"),
        ("/comfyui/ComfyUI", "/comfyui/ComfyUI/input"),
        ("/workspace/ComfyUI", "/workspace/ComfyUI/input"),
        ("/ComfyUI", "/ComfyUI/input"),
        ("/root/ComfyUI", "/root/ComfyUI/input"),
    ]

    for root, inp in candidates:
        if os.path.isdir(inp):
            return root, inp

    for root, inp in candidates:
        if os.path.isdir(root):
            try:
                os.makedirs(inp, exist_ok=True)
                return root, inp
            except Exception:
                pass

    raise RuntimeError("Could not locate ComfyUI input directory (tried common paths).")


def decode_base64_image(b64: str) -> bytes:
    if not b64 or not isinstance(b64, str):
        raise ValueError("input.image_base64 must be a non-empty base64 string")

    # Allow: data:image/png;base64,AAAA...
    if b64.startswith("data:") and "," in b64:
        b64 = b64.split(",", 1)[1]

    try:
        return base64.b64decode(b64, validate=False)
    except Exception as e:
        raise ValueError(f"Failed to decode base64 image: {e}") from e


def download_image(url: str, timeout: int = 60, max_bytes: int = 50 * 1024 * 1024) -> bytes:
    """
    Downloads an image over HTTP(S).
    Basic SSRF hardening: only allow http/https schemes.
    """
    if not url or not isinstance(url, str):
        raise ValueError("input.image_url must be a non-empty string")

    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("image_url must be http(s)")

    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "runpod-comfyui-handler/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"image_url response too large (>{max_bytes} bytes)")
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTP {e.code} downloading {url}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Error downloading {url}: {e}") from e


def wait_for_comfy(timeout_s: int = 300) -> None:
    """
    Wait until ComfyUI responds.
    """
    deadline = time.time() + timeout_s
    last_err = None

    while time.time() < deadline:
        try:
            # /queue is commonly present; if your build doesn't expose it, switch to /system_stats
            _http_json("GET", f"{COMFY_BASE}/queue", timeout=5)
            return
        except Exception as e:
            last_err = e
            time.sleep(1)

    raise RuntimeError(f"ComfyUI did not become ready within {timeout_s}s. Last error: {last_err}")


def submit_workflow(workflow: Dict[str, Any]) -> str:
    """
    POST /prompt with {"prompt": workflow}
    Returns prompt_id
    """
    res = _http_json("POST", f"{COMFY_BASE}/prompt", payload={"prompt": workflow}, timeout=60)
    prompt_id = res.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt response missing prompt_id: {res}")
    return prompt_id


def wait_for_prompt_done(prompt_id: str, timeout_s: int = 300, poll_s: float = 0.5) -> Dict[str, Any]:
    """
    Poll /history/{prompt_id} until it appears and has outputs.
    Returns the history JSON for that prompt.
    """
    deadline = time.time() + timeout_s
    last = None

    while time.time() < deadline:
        try:
            hist = _http_json("GET", f"{COMFY_BASE}/history/{prompt_id}", timeout=30)

            # When done, history usually has the prompt_id key or direct dict.
            if isinstance(hist, dict) and prompt_id in hist:
                hist = hist[prompt_id]

            last = hist

            # Heuristic: consider "done" once outputs exist
            if isinstance(hist, dict) and hist.get("outputs"):
                return hist
        except Exception as e:
            last = {"error": str(e)}

        time.sleep(poll_s)

    raise RuntimeError(f"Timed out waiting for prompt {prompt_id} after {timeout_s}s. Last: {last}")


def extract_saved_images_from_history(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract image references from ComfyUI history outputs.
    Returns a list of dicts with fields expected by /view.
    """
    out: List[Dict[str, Any]] = []
    outputs = history.get("outputs", {})

    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images", [])
        for im in images:
            # im typically has: filename, subfolder, type
            if isinstance(im, dict) and im.get("filename"):
                out.append(im)

    return out


def view_image_bytes(image_ref: Dict[str, Any]) -> bytes:
    """
    GET /view?filename=...&subfolder=...&type=...
    """
    filename = image_ref.get("filename")
    subfolder = image_ref.get("subfolder", "")
    img_type = image_ref.get("type", "output")

    if not filename:
        raise ValueError(f"Invalid image_ref: {image_ref}")

    q = urlencode({"filename": filename, "subfolder": subfolder, "type": img_type})
    return _http_bytes(f"{COMFY_BASE}/view?{q}", timeout=60)


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    inp = job.get("input", {}) or {}

    workflow = inp.get("workflow")
    if workflow is None:
        raise ValueError("input.workflow is required")

    # Ensure node keys are strings (ComfyUI commonly uses string keys)
    workflow = {str(k): v for k, v in workflow.items()}

    image_b64 = inp.get("image_base64")
    image_url = inp.get("image_url")

    prompt_override = inp.get("prompt")

    # timeout_s: time to wait for the prompt to complete (generation)
    timeout_s = int(inp.get("timeout_s", 300))

    # startup_timeout_s: time to wait for ComfyUI server to start responding
    startup_timeout_s = int(inp.get("startup_timeout_s", min(300, timeout_s)))

    # Ensure ComfyUI is up (this was your failure point)
    wait_for_comfy(timeout_s=startup_timeout_s)

    # Find input dir and write input image
    _, input_dir = find_comfy_dirs()
    filename = f"rp_{uuid.uuid4().hex}.png"
    img_path = os.path.join(input_dir, filename)

    if image_url:
        img_bytes = download_image(image_url, timeout=60)
    elif image_b64:
        img_bytes = decode_base64_image(image_b64)
    else:
        raise ValueError("Provide either input.image_url or input.image_base64")

    with open(img_path, "wb") as f:
        f.write(img_bytes)

    # Inject LoadImage filename (node 163)
    if "163" not in workflow:
        raise ValueError("workflow is missing node '163' (LoadImage). Update handler or your workflow IDs.")
    if "inputs" not in workflow["163"]:
        workflow["163"]["inputs"] = {}
    workflow["163"]["inputs"]["image"] = filename

    # Optional: override prompt on node 165 if present
    if prompt_override and "165" in workflow and isinstance(workflow["165"], dict):
        workflow["165"].setdefault("inputs", {})
        workflow["165"]["inputs"]["prompt"] = prompt_override

    # Submit + wait
    prompt_id = submit_workflow(workflow)
    hist = wait_for_prompt_done(prompt_id, timeout_s=timeout_s)

    # Fetch output images
    image_refs = extract_saved_images_from_history(hist)
    images_b64: List[str] = []
    for ref in image_refs:
        b = view_image_bytes(ref)
        images_b64.append(base64.b64encode(b).decode("utf-8"))

    return {
        "prompt_id": prompt_id,
        "input_filename": filename,
        "output_images_base64": images_b64,
        "output_count": len(images_b64),
    }


runpod.serverless.start({"handler": handler})
