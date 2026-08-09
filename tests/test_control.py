"""P2 デーモン骨格のテスト: 起動・エンドポイント・WebRTC エコー往復。

追加プラグイン (pytest-asyncio 等) を避けるため、各テストは asyncio.run で
コルーチンを回す。実行: uv run pytest tests/test_control.py
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack

from relay.control import build_app


async def _serve() -> tuple[web.AppRunner, str]:
    """空きポートでサーバーを起動し、(runner, base_url) を返す。"""
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def test_status_and_index() -> None:
    async def scenario() -> None:
        import aiohttp

        runner, base = await _serve()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/status") as r:
                    assert r.status == 200
                    assert (await r.json())["state"] == "idle"
                async with s.get(f"{base}/") as r:
                    assert r.status == 200
                    assert "voice-relay" in await r.text()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_webrtc_echo_roundtrip() -> None:
    """送信した音声トラックがエコーバックされ、フレームを受信できる。"""

    async def scenario() -> None:
        import aiohttp

        runner, base = await _serve()
        pc = RTCPeerConnection()
        frames = 0
        done = asyncio.Event()

        pc.addTrack(AudioStreamTrack())

        @pc.on("track")
        def on_track(track):
            async def pull():
                nonlocal frames
                try:
                    for _ in range(5):
                        await track.recv()
                        frames += 1
                except Exception:
                    pass
                done.set()

            asyncio.ensure_future(pull())

        try:
            await pc.setLocalDescription(await pc.createOffer())
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{base}/offer",
                    json={
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type,
                    },
                ) as r:
                    answer = await r.json()
            await pc.setRemoteDescription(RTCSessionDescription(**answer))

            await asyncio.wait_for(done.wait(), timeout=15)
            assert frames >= 3, f"expected echoed frames, got {frames}"

            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/status") as r:
                    assert (await r.json())["state"] == "active"
        finally:
            await pc.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_offer_answers_immediately_and_aborts_on_bg_failure() -> None:
    """/offer はパイプライン起動を待たず answer を返す (接続と起動の並列化)。
    バックグラウンド起動が失敗したら全停止し、/status に原因が載る。"""

    async def scenario() -> None:
        import aiohttp

        from relay.config import RelayConfig
        from relay.control import RelayServer, SessionState

        server = RelayServer(RelayConfig(mode="browser"))
        started = asyncio.Event()

        async def boom() -> None:
            started.set()
            await asyncio.sleep(0.3)  # SDP 交換が先に完了する (並列性の検証)
            raise RuntimeError("beatrice exploded")

        server._ensure_pipeline = boom  # 起動失敗を注入

        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        try:
            await pc.setLocalDescription(await pc.createOffer())
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{port}/offer",
                    json={
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type,
                    },
                ) as r:
                    assert r.status == 200
                    payload = await r.json()
                    assert payload.get("sdp"), "起動失敗でも answer は返る"

            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0.8)  # 失敗発生 + _abort_session の完了を待つ

            assert server._state == SessionState.IDLE
            assert not server._pcs
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{port}/status") as r:
                    status = await r.json()
                    assert "beatrice exploded" in status["error"]
        finally:
            await pc.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_start_pipeline_failure_returns_json_error() -> None:
    """/start の失敗も JSON で返す (ページの prewarm がエラー文を表示できるように)。"""

    async def scenario() -> None:
        import aiohttp

        from relay.config import RelayConfig
        from relay.control import RelayServer

        server = RelayServer(RelayConfig(mode="browser"))

        async def boom() -> None:
            raise RuntimeError("no devices")

        server._ensure_pipeline = boom

        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{port}/start") as r:
                    assert r.status == 500
                    payload = await r.json()
                    assert "no devices" in payload["error"]
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_stop_is_idempotent() -> None:
    async def scenario() -> None:
        import aiohttp

        runner, base = await _serve()
        try:
            async with aiohttp.ClientSession() as s:
                for _ in range(2):
                    async with s.post(f"{base}/stop") as r:
                        assert r.status == 200
                        assert (await r.json())["state"] == "idle"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
