"""Studio: owner-only workspaces, encrypted keys, the agent loop, and live preview."""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVORYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVORYA_SEED_DEMO", "0")
    monkeypatch.setenv("UNIVORYA_SECRET_KEY", "studio-test-master-key-0123456789abcdef")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client


def signup(client, username="studiomaker"):
    response = client.post("/api/auth/register", json={
        "name": "Studio Maker", "username": username,
        "email": f"{username}@example.com", "password": "A-good-password-45321",
    })
    assert response.status_code == 201, response.text
    return response.json()["user"]


def publish(client, title="Studio Playground"):
    response = client.post("/api/projects", data={
        "title": title,
        "tagline": "A real project used to exercise the AI coding workspace.",
        "description": "This project exists so Studio can scaffold a workspace, "
                       "let a coding agent edit files, and preview the result. " * 2,
        "category": "Dev Tool", "stage": "Building",
        "tech": "Python, FastAPI", "tags": "studio, ai",
        "cover_style": "violet", "repo_url": "", "demo_url": "", "website_url": "",
        "license": "MIT",
    })
    assert response.status_code == 201, response.text
    return response.json()["slug"]


def studio(c):
    from app import studio as module
    return module


def test_studio_is_owner_only(c):
    signup(c)
    slug = publish(c)
    assert c.get(f"/api/studio/projects/{slug}").status_code == 200

    c.post("/api/auth/logout")
    assert c.get(f"/api/studio/projects/{slug}").status_code == 401

    signup(c, "someoneelse")
    response = c.get(f"/api/studio/projects/{slug}")
    assert response.status_code == 403
    assert "creator" in response.json()["detail"].lower()


def test_workspace_scaffold_and_file_listing(c):
    signup(c)
    slug = publish(c)

    before = c.get(f"/api/studio/projects/{slug}").json()
    assert before["workspace"] is None
    assert before["files"] == []
    assert before["preview"]["running"] is False

    created = c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "static"})
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["workspace"]["template"] == "static"
    paths = {item["path"] for item in data["files"]}
    assert {"index.html", "style.css", "app.js", "univorya.json"} <= paths

    files = c.get(f"/api/studio/projects/{slug}/files").json()
    assert "index.html" in [item["path"] for item in files["files"]]
    assert "index.html" in files["tree"]

    again = c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "python"})
    assert again.status_code == 200
    assert again.json()["workspace"]["template"] == "static"


def test_credentials_are_encrypted_and_keep_the_existing_key(c):
    signup(c)
    response = c.put("/api/studio/credential", json={
        "provider": "openai", "model": "gpt-4.1-mini", "api_key": "sk-test-secret-1234",
    })
    assert response.status_code == 200, response.text
    public = response.json()["credential"]
    assert public["provider"] == "openai"
    assert public["key_hint"].endswith("1234")
    assert "secret" not in json.dumps(public)

    db_path = Path(os.environ["UNIVORYA_DATA_DIR"]) / "studio" / "studio.sqlite3"
    with sqlite3.connect(db_path) as con:
        stored = con.execute("SELECT api_key_enc FROM credentials").fetchone()[0]
    assert "sk-test-secret" not in stored

    kept = c.put("/api/studio/credential", json={
        "provider": "openai", "model": "gpt-4.1", "api_key": "",
    })
    assert kept.status_code == 200
    assert kept.json()["credential"]["key_hint"].endswith("1234")
    assert kept.json()["credential"]["model"] == "gpt-4.1"

    bad = c.put("/api/studio/credential", json={
        "provider": "openai", "model": "x", "api_key": "sk-x", "base_url": "http://example.com",
    })
    assert bad.status_code == 422
    assert c.delete("/api/studio/credential").json()["credential"] is None
    assert c.get("/api/studio/credential").json()["credential"] is None


def test_chat_requires_a_key(c):
    signup(c)
    slug = publish(c)
    c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "static"})
    response = c.post(f"/api/studio/projects/{slug}/chat", json={"message": "Build a page"})
    assert response.status_code == 409
    assert "key" in response.json()["detail"].lower()


def test_agent_writes_a_file_and_reports_events(c, monkeypatch):
    signup(c)
    slug = publish(c)
    c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "static"})
    c.put("/api/studio/credential", json={
        "provider": "openai", "model": "gpt-4.1-mini", "api_key": "sk-test-secret-1234",
    })

    calls = {"count": 0}

    def fake_model(cred, messages, system):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"text": "I will update the page.", "tool_calls": [{
                "id": "call-1", "name": "write_file",
                "args": {"path": "index.html", "content": "<h1>Built by Studio</h1>"},
            }], "usage": {}}
        return {"text": "Updated index.html.", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(studio(c), "_call_model", fake_model)
    response = c.post(f"/api/studio/projects/{slug}/chat", json={"message": "Build a heading"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["message"]["content"] == "Updated index.html."
    assert data["events"][0]["name"] == "write_file"
    assert data["events"][0]["ok"] is True
    assert any(item["path"] == "index.html" for item in data["files"])

    row = studio(c).get_workspace(slug)
    content = (studio(c).workspace_dir(row["slug"]) / "index.html").read_text(encoding="utf-8")
    assert "Built by Studio" in content

    saved = c.get(f"/api/studio/projects/{slug}/messages").json()["messages"]
    assert [item["role"] for item in saved] == ["user", "tool", "assistant"]


def test_live_preview_starts_and_proxies_with_a_signed_token(c):
    signup(c)
    slug = publish(c)
    c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "static"})
    module = studio(c)
    row = module.get_workspace(slug)
    root = module.workspace_dir(row["slug"])
    command = f'"{sys.executable}" -m http.server {{port}} --bind 127.0.0.1'
    (root / "univorya.json").write_text(json.dumps({"run": command}), encoding="utf-8")

    started = c.post(f"/api/studio/projects/{slug}/preview/start", json={})
    try:
        assert started.status_code == 200, started.text
        preview = started.json()["preview"]
        assert preview["running"] is True
        assert preview["url"].startswith("http://127.0.0.1:")

        status = c.get(f"/api/studio/projects/{slug}/preview").json()["preview"]
        assert status["running"] is True

        user = c.get("/api/auth/me").json()["user"]
        token = module._preview_token(slug, user["id"])
        page = c.get(f"/preview/{token}/")
        assert page.status_code == 200
        assert "Your app starts here." in page.text
        assert f'<base href="/preview/{token}/">' in page.text
        assert page.headers["x-frame-options"] == "SAMEORIGIN"
        assert "content-security-policy" not in page.headers

        assert c.get("/preview/not-a-real-token/").status_code == 404
    finally:
        c.post(f"/api/studio/projects/{slug}/preview/stop")

    stopped = c.get(f"/api/studio/projects/{slug}/preview").json()["preview"]
    assert stopped["running"] is False
    user = c.get("/api/auth/me").json()["user"]
    token = module._preview_token(slug, user["id"])
    assert c.get(f"/preview/{token}/").status_code == 409


def test_csp_allows_local_preview_frames(c):
    response = c.get("/")
    policy = response.headers["content-security-policy"]
    assert "frame-src" in policy
    assert "http://127.0.0.1:*" in policy
    assert response.headers["x-frame-options"] == "DENY"


def test_preview_can_name_the_app_origin_as_its_framer(c, monkeypatch):
    user = signup(c)
    slug = publish(c, "Framing Playground")
    module = studio(c)
    module.ensure_workspace(slug, user["id"], "static")
    token = module._preview_token(slug, user["id"])

    monkeypatch.setenv("UNIVORYA_APP_ORIGIN", "https://showcase.example.com/")
    response = c.get(f"/preview/{token}/")
    assert response.status_code == 409  # token is valid, but no preview is running
    assert "x-frame-options" not in response.headers
    assert response.headers["content-security-policy"] == \
        "frame-ancestors 'self' https://showcase.example.com"

    monkeypatch.setenv("UNIVORYA_APP_ORIGIN", "not-a-url")
    fallback = c.get(f"/preview/{token}/")
    assert fallback.headers["x-frame-options"] == "SAMEORIGIN"


def test_provider_payloads_and_anthropic_conversion(c, monkeypatch):
    module = studio(c)
    monkeypatch.setenv("UNIVORYA_SECRET_KEY", "studio-test-master-key-0123456789abcdef")
    seen = {}

    def fake_post(url, headers, payload):
        seen["url"], seen["headers"], seen["payload"] = url, headers, payload
        if url.endswith("/v1/messages"):
            return {
                "content": [
                    {"type": "text", "text": "Done"},
                    {"type": "tool_use", "id": "t1", "name": "write_file", "input": {"path": "a.txt"}},
                    {"type": "tool_use", "input": "not-a-dict"},
                    {"type": "tool_use", "name": "list_files"},
                ],
                "usage": {"output_tokens": 2},
            }
        return {
            "choices": [{"message": {
                "content": " working ",
                "tool_calls": [
                    {"id": "a", "function": {"name": "write_file", "arguments": '{"path":"x","content":"y"}'}},
                    {"id": "b", "function": {"name": "read_file", "arguments": "not-json"}},
                    {"function": {"name": "list_files", "arguments": '["not", "an", "object"]'}},
                ],
            }}],
            "usage": {"total_tokens": 3},
        }

    monkeypatch.setattr(module, "_post_json", fake_post)
    cred = {"provider": "openai", "model": "gpt-test", "base_url": "", "_api_key": "sk-test"}
    reply = module._call_openai(cred, [{"role": "user", "content": "hi"}], "SYSTEM")
    assert reply["text"] == "working"
    assert reply["usage"] == {"total_tokens": 3}
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["payload"]["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert {item["function"]["name"] for item in seen["payload"]["tools"]} >= {"write_file", "run_command"}
    assert reply["tool_calls"][0] == {"id": "a", "name": "write_file",
                                      "args": {"path": "x", "content": "y"}}
    assert reply["tool_calls"][1]["args"] == {}
    assert reply["tool_calls"][2]["id"]
    assert reply["tool_calls"][2]["args"] == {}

    openrouter = {"provider": "openrouter", "model": "x", "base_url": "https://openrouter.ai/api/v1/",
                  "_api_key": "sk-or"}
    module._call_openai(openrouter, [], "S")
    assert seen["headers"]["HTTP-Referer"] == "https://univorya.app"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"

    anth = {"provider": "anthropic", "model": "claude-test", "base_url": "", "_api_key": "sk-ant"}
    converted = module._call_anthropic(anth, [
        {"role": "assistant", "content": "thinking", "tool_calls": [
            {"id": "t0", "name": "read_file", "args": {"path": "x"}}]},
        {"role": "tool", "tool_call_id": "t0", "content": "result"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "go"},
    ], "SYS")
    assert converted["text"] == "Done"
    assert converted["tool_calls"][0]["args"] == {"path": "a.txt"}
    assert converted["tool_calls"][1]["id"]
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant"
    blocks = seen["payload"]["messages"]
    assert blocks[0]["content"][0] == {"type": "text", "text": "thinking"}
    assert blocks[0]["content"][1]["type"] == "tool_use"
    assert blocks[1]["content"][0]["type"] == "tool_result"
    assert blocks[2]["content"] == [{"type": "text", "text": ""}]
    assert blocks[3] == {"role": "user", "content": "go"}

    empty = {"provider": "openai", "model": "x", "base_url": "", "_api_key": "k"}
    monkeypatch.setattr(module, "_post_json", lambda *args: {})
    with pytest.raises(module.StudioError) as caught:
        module._call_openai(empty, [], "S")
    assert caught.value.status_code == 502

    monkeypatch.setattr(module, "_call_anthropic", lambda *args: {"text": "a"})
    monkeypatch.setattr(module, "_call_openai", lambda *args: {"text": "o"})
    assert module._call_model({"provider": "anthropic"}, [], "")["text"] == "a"
    assert module._call_model({"provider": "openai"}, [], "")["text"] == "o"


def test_post_json_maps_provider_and_transport_errors(c, monkeypatch):
    module = studio(c)
    state = {"response": None, "error": None}

    class FakeResponse:
        def __init__(self, status_code, data, invalid=False):
            self.status_code = status_code
            self._data = data
            self._invalid = invalid

        def json(self):
            if self._invalid:
                raise ValueError("bad json")
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            if state["error"]:
                raise state["error"]
            return state["response"]

    monkeypatch.setattr(module.httpx, "Client", FakeClient)

    state["response"] = FakeResponse(200, {"ok": True})
    assert module._post_json("https://x", {}, {}) == {"ok": True}

    state["response"] = FakeResponse(401, {"error": {"message": "no"}})
    with pytest.raises(module.StudioError) as caught:
        module._post_json("https://x", {}, {})
    assert caught.value.status_code == 422
    assert "rejected" in caught.value.detail

    state["response"] = FakeResponse(400, {"error": {"message": "bad model"}})
    with pytest.raises(module.StudioError) as caught:
        module._post_json("https://x", {}, {})
    assert caught.value.status_code == 422
    assert caught.value.detail == "bad model"

    state["response"] = FakeResponse(503, {"error": "upstream down"})
    with pytest.raises(module.StudioError) as caught:
        module._post_json("https://x", {}, {})
    assert caught.value.status_code == 502
    assert caught.value.detail == "upstream down"

    state["response"] = FakeResponse(200, None, invalid=True)
    with pytest.raises(module.StudioError) as caught:
        module._post_json("https://x", {}, {})
    assert caught.value.status_code == 502
    assert "invalid response" in caught.value.detail

    state["error"] = module.httpx.ConnectError("boom")
    with pytest.raises(module.StudioError) as caught:
        module._post_json("https://x", {}, {})
    assert caught.value.status_code == 502
    assert "Could not reach" in caught.value.detail


def test_studio_tools_read_write_delete_list_and_commands(c, monkeypatch):
    module = studio(c)
    user = signup(c)
    slug = publish(c, "Tool Playground")
    uid = user["id"]
    module.ensure_workspace(slug, uid, "static")
    root = module.workspace_dir(slug)

    listing = module.execute_tool(slug, uid, "list_files", {})
    assert "index.html" in listing and "style.css" in listing
    assert "Path not found" in module.execute_tool(slug, uid, "list_files", {"path": "nope"})
    assert "<!doctype html" in module.execute_tool(slug, uid, "list_files", {"path": "index.html"})
    assert "<!doctype html" in module.execute_tool(slug, uid, "read_file", {"path": "index.html"})
    assert "File not found" in module.execute_tool(slug, uid, "read_file", {"path": "missing.txt"})
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    assert "binary" in module.execute_tool(slug, uid, "read_file", {"path": "blob.bin"})
    with pytest.raises(module.StudioError):
        module.execute_tool(slug, uid, "read_file", {"path": "../outside.txt"})

    assert "Wrote" in module.execute_tool(slug, uid, "write_file",
                                         {"path": "nested/deep.txt", "content": "hello"})
    assert (root / "nested" / "deep.txt").read_text(encoding="utf-8") == "hello"
    assert "Choose a file path" in module.execute_tool(slug, uid, "write_file",
                                                      {"path": "", "content": "x"})
    with pytest.raises(module.StudioError):
        module.execute_tool(slug, uid, "write_file",
                            {"path": "huge.txt", "content": "x" * (module.MAX_FILE_WRITE + 1)})

    assert "does not exist" in module.execute_tool(slug, uid, "delete_file", {"path": "ghost"})
    assert "not empty" in module.execute_tool(slug, uid, "delete_file", {"path": "nested"})
    assert "Deleted" in module.execute_tool(slug, uid, "delete_file", {"path": "nested/deep.txt"})
    assert "Removed directory" in module.execute_tool(slug, uid, "delete_file", {"path": "nested"})
    assert "Unknown tool" in module.execute_tool(slug, uid, "does_not_exist", {})

    ok = module.execute_tool(slug, uid, "run_command", {"command": "echo studio-hello"})
    assert "exit_code: 0" in ok and "studio-hello" in ok
    with pytest.raises(module.StudioError):
        module.execute_tool(slug, uid, "run_command", {"command": ""})
    with pytest.raises(module.StudioError):
        module.run_command(slug, uid, "x" * 4001)
    assert "Preview stopped" in module.execute_tool(slug, uid, "stop_preview", {})
    status = json.loads(module.execute_tool(slug, uid, "preview_status", {}))
    assert status["running"] is False
    assert "index.html" in module.project_tree(slug, uid)

    def timeout_run(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="x", timeout=3, output="out", stderr="err")

    monkeypatch.setattr(module.subprocess, "run", timeout_run)
    timed = module.run_command(slug, uid, "whatever", timeout_seconds=3)
    assert timed["exit_code"] == 124
    assert "[timed out after 3s]" in timed["output"]

    def os_run(*args, **kwargs):
        raise OSError("no shell")

    monkeypatch.setattr(module.subprocess, "run", os_run)
    with pytest.raises(module.StudioError) as caught:
        module.run_command(slug, uid, "whatever")
    assert caught.value.status_code == 500


def test_runner_detection_preview_urls_and_rewrites(c, monkeypatch, tmp_path):
    module = studio(c)
    for template, expected in (
        ("static", "http.server"),
        ("python", "uvicorn"),
        ("node", "node server.js"),
        ("blank", ""),
    ):
        root = tmp_path / template
        module._scaffold(root, template)
        assert expected in module._read_command(root)

    def fresh(name):
        root = tmp_path / name
        root.mkdir()
        return root

    root = fresh("vite")
    (root / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8")
    assert module._read_command(root) == "npm run dev -- --host 127.0.0.1 --port {port}"
    (root / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}), encoding="utf-8")
    assert module._read_command(root) == "npm run dev -- -p {port}"
    (root / "package.json").write_text(json.dumps({"scripts": {"start": "node server.js"}}), encoding="utf-8")
    assert module._read_command(root) == "npm start"
    (root / "package.json").write_text("{not json", encoding="utf-8")
    assert module._read_command(root) == ""

    root = fresh("flask")
    (root / "main.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    assert "flask --app main" in module._read_command(root)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert module._read_command(root) == "python main.py"
    (root / "main.py").unlink()
    (root / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    assert "uvicorn app:app" in module._read_command(root)
    (root / "app.py").unlink()
    (root / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    assert "http.server" in module._read_command(root)
    (root / "index.html").unlink()
    assert module._read_command(root) == ""
    (root / "univorya.json").write_text("{bad", encoding="utf-8")
    assert module._read_command(root) == ""
    (root / "univorya.json").write_text(json.dumps({"run": "  "}), encoding="utf-8")
    assert module._read_command(root) == ""

    env = module._safe_env(root, 4321)
    assert env["PORT"] == "4321"
    assert env["HOST"] == "127.0.0.1"
    assert "UNIVORYA_SECRET_KEY" not in env
    assert module._format_command("x {port} $PORT ${PORT}", 42) == "x 42 42 42"
    assert module._free_port() > 0
    assert module._port_open(0) is False
    assert module._pid_alive(0) is False
    assert module._pid_alive(os.getpid()) is True
    assert module._kill_tree(0) is None

    token = module._preview_token("a-slug", "user-1", ttl=60)
    assert module.verify_preview_token(token) == ("a-slug", "user-1")
    assert module.verify_preview_token(token + "x") is None
    assert module.verify_preview_token(module._preview_token("a-slug", "user-1", ttl=-1)) is None
    assert module.verify_preview_token("not-base64") is None

    monkeypatch.setenv("UNIVORYA_PREVIEW_ORIGIN", "https://preview.example.com/")
    signed = module.preview_public_url("a-slug", "user-1", 9999)
    assert signed.startswith("https://preview.example.com/preview/")
    assert signed.endswith("/")
    with pytest.raises(module.StudioError):
        module.preview_target("missing", "user-1")

    assert module.rewrite_preview_html("", "preview/t") == ""
    wrapped = module.rewrite_preview_html(
        "<html><head><title>x</title></head><body>"
        "<img src='/a.png'><a href='//cdn.example/x'>c</a></body></html>", "preview/t")
    assert '<base href="/preview/t/">' in wrapped
    assert 'src=\'/preview/t/a.png\'' in wrapped
    assert "href='//cdn.example/x'" in wrapped
    already = module.rewrite_preview_html('<base href="/x/"><p>ok</p>', "preview/t")
    assert already.count("<base") == 1
    assert module.rewrite_preview_css("a{background:url('/x.png')} b{background:url(https://y/x)}",
                                      "preview/t") == \
        "a{background:url('/preview/t/x.png')} b{background:url(https://y/x)}"
    assert module.rewrite_location("", "tok") == ""
    assert module.rewrite_location("/app?x=1", "tok") == "/preview/tok/app?x=1"
    assert module.rewrite_location("http://127.0.0.1:3000/a?b=1#c", "tok") == "/preview/tok/a?b=1"
    assert module.rewrite_location("/relative", "tok") == "/preview/tok/relative"
    assert module.rewrite_location("https://example.com/x", "tok") == "https://example.com/x"
    assert module._tail(tmp_path / "missing.log") == ""


def test_credential_validation_chat_budget_and_ownership(c, monkeypatch):
    module = studio(c)
    user = signup(c)
    uid = user["id"]
    slug = publish(c, "Validation Playground")

    with pytest.raises(module.StudioError) as caught:
        module.save_credential(uid, "unknown", "model", "sk-12345678")
    assert caught.value.status_code == 422
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "", "sk-12345678")
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "gpt", "short")
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "gpt", "sk-with space-123")
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "gpt", "sk-12345678", "ftp://example.com")
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "gpt", "sk-12345678", "https://user:pass@example.com/v1")
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "openai", "gpt", "sk-12345678", "http://example.com/v1")

    saved = module.save_credential(uid, "custom", "local", "sk-12345678", "https://api.example.com/v1")
    assert saved["base_url"] == "https://api.example.com/v1"
    assert module.provider_catalog()[0]["id"] == "openai"
    assert module.delete_credential(uid) is True
    assert module.delete_credential(uid) is False

    module.save_credential(uid, "openai", "gpt", "sk-12345678", "http://localhost:11434/v1")
    with sqlite3.connect(module.studio_db_path()) as con:
        con.execute("UPDATE credentials SET api_key_enc='broken-token' WHERE user_id=?", (str(uid),))
    with pytest.raises(module.StudioError) as caught:
        module.credential_public(uid)
    assert caught.value.status_code == 409
    assert "Reconnect" in caught.value.detail

    module.ensure_workspace(slug, uid, "static")
    with pytest.raises(module.StudioError):
        module.ensure_workspace(slug, uid, "not-a-template")
    other = signup(c, "othermaker")
    with pytest.raises(module.StudioError) as caught:
        module.ensure_workspace(slug, other["id"], "static")
    assert caught.value.status_code == 403
    with pytest.raises(module.StudioError) as caught:
        module.require_workspace(slug, other["id"])
    assert caught.value.status_code == 403
    with pytest.raises(module.StudioError) as caught:
        module.require_workspace("missing", uid)
    assert caught.value.status_code == 404

    with pytest.raises(module.StudioError):
        module.chat(slug, uid, "   ")
    with pytest.raises(module.StudioError):
        module.chat(slug, uid, "x" * (module.MAX_MESSAGE_CHARS + 1))
    with pytest.raises(module.StudioError) as caught:
        module.chat(slug, uid, "no key yet")
    assert caught.value.status_code == 409

    monkeypatch.setattr(module, "CHAT_LIMIT", 1)
    module._CHAT_ATTEMPTS.clear()
    module._check_chat_budget(uid)
    with pytest.raises(module.StudioError) as caught:
        module._check_chat_budget(uid)
    assert caught.value.status_code == 429
    module._CHAT_ATTEMPTS[str(uid)].appendleft(0)
    monkeypatch.setattr(module, "CHAT_LIMIT", 5)
    module._check_chat_budget(uid)

    row = module.require_workspace(slug, uid)
    module._save_message(row["id"], "assistant", "hello", None)
    with sqlite3.connect(module.studio_db_path()) as con:
        con.execute("UPDATE messages SET meta='{broken' WHERE workspace_id=?", (row["id"],))
    messages = module.messages_for(slug, uid)
    assert messages[-1]["meta"] == {}
    assert module.project_tree("never-created", uid) == "(no workspace yet)"


def test_preview_failure_paths_and_secret_file(c, monkeypatch):
    module = studio(c)
    user = signup(c)
    slug = publish(c, "Preview Failure")
    uid = user["id"]
    module.ensure_workspace(slug, uid, "blank")

    with pytest.raises(module.StudioError) as caught:
        module.preview_target(slug, uid)
    assert caught.value.status_code == 409

    root = module.workspace_dir(slug)
    (root / "README.md").unlink()
    with pytest.raises(module.StudioError) as caught:
        module.start_preview(slug, uid)
    assert caught.value.status_code == 422
    assert "No run command" in caught.value.detail

    (root / "univorya.json").write_text(
        json.dumps({"run": "definitely-not-a-real-command-xyz"}), encoding="utf-8")
    with pytest.raises(module.StudioError) as caught:
        module.start_preview(slug, uid)
    assert caught.value.status_code == 422
    assert "did not start" in caught.value.detail
    row = module.get_workspace(slug)
    assert row["status"] == "error"
    assert row["last_error"]
    assert "definitely-not-a-real-command-xyz" in module.preview_log(slug, uid)

    module._kill_tree(0)
    import shutil as _shutil
    _shutil.rmtree(root)
    with pytest.raises(module.StudioError) as caught:
        module.start_preview(slug, uid)
    assert caught.value.status_code == 404

    monkeypatch.delenv("UNIVORYA_SECRET_KEY", raising=False)
    module.init_if_needed()
    first = module._signing_key()
    second = module._signing_key()
    assert first == second
    assert (module.root_dir() / "secret.key").exists()
    with pytest.raises(sqlite3.OperationalError):
        with module._db() as con:
            con.execute("SELECT * FROM definitely_not_a_table")



# ------------------------------------------------------------------- git + agent CLI

def _require_git(module):
    info = module.git_available()
    if not info["available"]:
        pytest.skip("git is not installed in this environment")
    return info


def _seed_remote(module, tmp_path):
    bare = tmp_path / "remote.git"
    module._run_git(tmp_path, ["init", "--bare", "--initial-branch=main", str(bare)])
    seed = tmp_path / "seed"
    seed.mkdir()
    module._run_git(seed, ["init", "--initial-branch=main"])
    (seed / "README.md").write_text("seed repository\n", encoding="utf-8")
    module._run_git(seed, ["add", "."])
    module._run_git(seed, ["-c", "user.name=Seed", "-c", "user.email=seed@example.com",
                           "commit", "-m", "seed"])
    module._run_git(seed, ["remote", "add", "origin", str(bare)])
    module._run_git(seed, ["push", "-u", "origin", "main"])
    return bare


def test_git_only_urls_are_rejected(c):
    module = studio(c)
    for raw in ("", "   ", "-delete-everything", "ssh://git@example.com/a.git",
                "git://example.com/a.git", "https://user:pass@example.com/a.git",
                "http://example.com/a.git", "ftp://example.com/a.git"):
        with pytest.raises(module.StudioError) as caught:
            module._valid_git_url(raw)
        assert caught.value.status_code == 422
    assert module._valid_git_url("https://github.com/owner/repo.git").startswith("https://")
    assert module._valid_git_url("http://127.0.0.1:8081/repo.git").startswith("http://")
    assert module._valid_git_url(str(Path.cwd())) == str(Path.cwd().resolve())


def test_git_credential_is_encrypted_and_validated(c):
    module = studio(c)
    user = signup(c)
    uid = user["id"]

    with pytest.raises(module.StudioError):
        module.save_git_credential(uid, "short")
    with pytest.raises(module.StudioError):
        module.save_git_credential(uid, "ghp-with space-123456")
    with pytest.raises(module.StudioError):
        module.save_git_credential(uid, "ghp_1234567890abcdef", "bad user")

    public = module.save_git_credential(uid, "ghp_1234567890abcdef", "octocat")
    assert public["connected"] is True
    assert public["username"] == "octocat"
    assert public["hint"].endswith("cdef")
    assert "ghp_1234567890" not in json.dumps(public)

    with sqlite3.connect(module.studio_db_path()) as con:
        stored = con.execute("SELECT token_enc FROM git_credentials WHERE user_id=?",
                             (str(uid),)).fetchone()[0]
    assert "ghp_1234567890abcdef" not in stored
    saved = module.get_git_credential(uid)
    assert saved["_token"] == "ghp_1234567890abcdef"
    assert "token" not in module.git_credential_public(uid)
    assert module.delete_git_credential(uid) is True
    assert module.delete_git_credential(uid) is False
    assert module.get_git_credential(uid) is None
    assert module.git_credential_public(uid)["connected"] is False


def test_git_clone_commit_push_pull_and_agent_tools(c, tmp_path):
    module = studio(c)
    _require_git(module)
    user = signup(c)
    uid = user["id"]
    slug = publish(c, "Git Playground")
    module.ensure_workspace(slug, uid, "static")
    bare = _seed_remote(module, tmp_path)

    with pytest.raises(module.StudioError) as caught:
        module.git_clone(slug, uid, str(bare))
    assert caught.value.status_code == 409

    status = module.git_clone(slug, uid, str(bare), replace=True)
    assert status["is_repo"] is True
    assert status["branch"] == "main"
    root = module.workspace_dir(slug)
    assert (root / "README.md").exists()
    assert list((root / ".univorya" / "backups").rglob("index.html"))

    (root / "README.md").write_text("seed repository\nstudio edit\n", encoding="utf-8")
    (root / "notes.txt").write_text("untracked\n", encoding="utf-8")
    status = module.git_status(slug, uid)
    assert status["clean"] is False
    assert any(item["path"] == "README.md" for item in status["changes"])
    assert any(item["path"] == "notes.txt" for item in status["changes"])
    assert "studio edit" in module.git_diff(slug, uid)

    committed = module.git_commit(slug, uid, "Studio edit")
    assert committed["clean"] is True
    assert committed["log"][0]["subject"] == "Studio edit"
    pushed = module.git_push(slug, uid)
    assert pushed["ahead"] == 0
    remote_log = module._run_git(tmp_path, ["--git-dir", str(bare), "log", "--pretty=%s", "main"])
    assert "Studio edit" in remote_log["output"]

    (root / "tool.txt").write_text("written by an agent tool\n", encoding="utf-8")
    (root / "README.md").write_text("seed repository\nstudio edit\ntool change\n", encoding="utf-8")
    assert json.loads(module.execute_tool(slug, uid, "git_status", {}))["is_repo"] is True
    assert "tool change" in module.execute_tool(slug, uid, "git_diff", {})
    assert "Committed." in module.execute_tool(slug, uid, "git_commit", {"message": "Tool commit"})
    assert "Pushed." in module.execute_tool(slug, uid, "git_push", {})

    other = tmp_path / "other"
    module._run_git(tmp_path, ["clone", str(bare), str(other)])
    (other / "remote.txt").write_text("from the remote\n", encoding="utf-8")
    module._run_git(other, ["add", "."])
    module._run_git(other, ["-c", "user.name=Other", "-c", "user.email=other@example.com",
                            "commit", "-m", "Remote change"])
    module._run_git(other, ["push", "origin", "main"])
    pulled = module.git_pull(slug, uid)
    assert pulled["behind"] == 0
    assert (root / "remote.txt").exists()

    assert "Pulled." in module.execute_tool(slug, uid, "git_pull", {})
    with pytest.raises(module.StudioError) as caught:
        module.git_commit(slug, uid, "Nothing changed")
    assert "Nothing to commit" in caught.value.detail
    with pytest.raises(module.StudioError):
        module.git_commit(slug, uid, "")
    with pytest.raises(module.StudioError):
        module.git_commit(slug, uid, "two\nlines")
    with pytest.raises(module.StudioError) as caught:
        module.git_clone(slug, uid, str(bare))
    assert caught.value.status_code == 409


def test_git_api_routes_are_owner_only_and_persist(c, tmp_path):
    module = studio(c)
    _require_git(module)
    signup(c)
    slug = publish(c, "Git API Playground")
    c.put(f"/api/studio/projects/{slug}/workspace", json={"template": "blank"})
    base = f"/api/studio/projects/{slug}/git"

    empty = c.get(base).json()
    assert empty["git"]["is_repo"] is False
    assert empty["available"]["available"] is True
    assert c.post(f"{base}/commit", json={"message": "x"}).status_code == 409
    assert c.get(f"{base}/diff").status_code == 409
    assert c.post(f"{base}/push").status_code == 409
    assert c.post(f"{base}/clone", json={"url": "ssh://git@example.com/x.git"}).status_code == 422
    assert c.post(f"{base}/clone", json={"url": ""}).status_code == 422

    assert c.put("/api/studio/git/credential", json={"token": "short"}).status_code == 422
    saved = c.put("/api/studio/git/credential",
                  json={"token": "ghp_1234567890abcdef", "username": "octocat"})
    assert saved.status_code == 200
    assert saved.json()["credential"]["connected"] is True
    assert saved.json()["credential"]["hint"].endswith("cdef")
    assert "ghp_" not in json.dumps(saved.json()["credential"])

    bare = _seed_remote(module, tmp_path)
    cloned = c.post(f"{base}/clone", json={"url": str(bare), "replace": True})
    assert cloned.status_code == 200, cloned.text
    assert cloned.json()["git"]["is_repo"] is True
    assert any(item["path"] == "README.md" for item in cloned.json()["files"])

    root = module.workspace_dir(slug)
    (root / "README.md").write_text("changed through the API\n", encoding="utf-8")
    diff = c.get(f"{base}/diff").json()["diff"]
    assert "changed through the API" in diff
    committed = c.post(f"{base}/commit", json={"message": "API commit"})
    assert committed.status_code == 200, committed.text
    assert committed.json()["git"]["clean"] is True
    assert c.post(f"{base}/push").status_code == 200

    assert c.delete("/api/studio/git/credential").json()["credential"] is None

    c.post("/api/auth/logout")
    signup(c, "gitintruder")
    assert c.get(base).status_code == 403
    assert c.post(f"{base}/push").status_code == 403


def test_cli_provider_validation_and_command_building(c):
    module = studio(c)
    user = signup(c)
    uid = user["id"]
    catalog = {item["id"]: item for item in module.provider_catalog()}
    assert "cli" in catalog
    assert {"claude", "codex", "gemini", "custom"} <= set(catalog["cli"]["agents"])
    assert catalog["cli"]["available"]["custom"] is True

    with pytest.raises(module.StudioError):
        module.save_credential(uid, "cli", "unknown-agent", "")
    with pytest.raises(module.StudioError) as caught:
        module.save_credential(uid, "cli", "custom", "", "")
    assert "command" in caught.value.detail.lower()
    with pytest.raises(module.StudioError):
        module.save_credential(uid, "cli", "custom", "", "agent --go\nrm -rf /")
    saved = module.save_credential(uid, "cli", "custom", "", "agent --print {prompt}")
    assert saved["keyless"] is True
    assert saved["model"] == "custom"
    assert saved["base_url"] == "agent --print {prompt}"
    kept = module.save_credential(uid, "cli", "claude", "")
    assert kept["keyless"] is True
    assert kept["base_url"] == ""

    assert module._split_command('"C:/Program Files/Agent/agent.exe" --yes {prompt}') == [
        "C:/Program Files/Agent/agent.exe", "--yes", "{prompt}"]
    assert module._cli_argv({"model": "custom", "base_url": "agent --go {prompt}"},
                            "hello world") == ["agent", "--go", "hello world"]
    with pytest.raises(module.StudioError):
        module._cli_argv({"model": "custom", "base_url": "agent --go"}, "x")
    with pytest.raises(module.StudioError):
        module._cli_argv({"model": "nope"}, "x")
    assert module._cli_argv({"model": "claude"}, "hi")[0] == "claude"


def test_chat_runs_a_configured_cli_agent(c, monkeypatch):
    module = studio(c)
    user = signup(c)
    uid = user["id"]
    slug = publish(c, "CLI Playground")
    module.ensure_workspace(slug, uid, "blank")
    code = ("import pathlib,sys; "
            "pathlib.Path('cli-output.txt').write_text(sys.argv[1], encoding='utf-8'); "
            "print('agent handled it')")
    monkeypatch.setitem(module.CLI_AGENTS, "fakeagent", {
        "label": "Fake agent", "binary": sys.executable,
        "args": ["-c", code, "{prompt}"], "env": "FAKE_AGENT_KEY",
    })
    module.save_credential(uid, "cli", "fakeagent", "sk-cli-secret-9999")
    reply = module.chat(slug, uid, "Write the output file")
    assert "agent handled it" in reply["message"]["content"]
    assert reply["events"][0]["name"] == "cli_agent"
    assert reply["events"][0]["args"]["agent"] == "fakeagent"
    written = (module.workspace_dir(slug) / "cli-output.txt").read_text(encoding="utf-8")
    assert "You are Univorya Studio" in written
    assert "Write the output file" in written

    monkeypatch.setitem(module.CLI_AGENTS, "failing", {
        "label": "Failing agent", "binary": sys.executable,
        "args": ["-c", "import sys; print('boom'); sys.exit(2)"], "env": "",
    })
    module.save_credential(uid, "cli", "failing", "")
    with pytest.raises(module.StudioError) as caught:
        module.chat(slug, uid, "Do the thing")
    assert caught.value.status_code == 502
    assert "boom" in caught.value.detail

    monkeypatch.setitem(module.CLI_AGENTS, "missing", {
        "label": "Missing agent", "binary": "definitely-not-installed-cli-xyz",
        "args": ["{prompt}"], "env": "",
    })
    module.save_credential(uid, "cli", "missing", "")
    with pytest.raises(module.StudioError) as caught:
        module.chat(slug, uid, "Do the thing")
    assert caught.value.status_code == 422
    assert "not installed" in caught.value.detail
