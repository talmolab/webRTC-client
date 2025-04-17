import pytest
import asyncio
from unittest import mock
from unittest.mock import AsyncMock
from sleap_client import client  # or from sleap_webrtc.client if renamed

@pytest.mark.asyncio
async def test_handle_connection_answer():
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()
    
    # Simulate receiving an "answer" message
    websocket.__aiter__.return_value = [
        '{"type": "answer", "sdp": "dummy sdp"}'
    ]

    await client.handle_connection(pc, websocket)

    # Confirm the async method was called once
    pc.setRemoteDescription.assert_called_once()


@pytest.mark.asyncio
async def test_handle_connection_no_answer():
    # Mock peer connection and channel
    pc = mock.AsyncMock()
    pc.addIceCandidate = mock.AsyncMock()
    pc.connectionState = "connected"

    # Simulate receiving different types of messages
    websocket = mock.AsyncMock()
    websocket.__aiter__.return_value = [
        '{"type": "candidate", "candidate": {"candidate": "fake", "sdpMid": "0", "sdpMLineIndex": 0}}',
        '{"type": "quit"}',
        '{"type": "unhandled_message"}',
    ]

    await client.handle_connection(pc, websocket)

    # Check that candidate triggered addIceCandidate
    pc.addIceCandidate.assert_called_once()

    # For 'quit', ICE connection is usually closed or something logged — we can check for internal state if accessible

    # Since the 'something_unexpected' triggers the else clause, we expect no crashes here 


@pytest.mark.asyncio
async def test_run_client_registers_with_server(monkeypatch):
    # Create a mock websocket object
    fake_ws = mock.AsyncMock()
    fake_ws.__aenter__.return_value = fake_ws
    fake_ws.__aexit__.return_value = mock.AsyncMock()
    
    # Simulate receiving a message from the server
    fake_ws.recv.side_effect = [
        '{"peers": ["worker1"]}',  # to simulate the server sending a list of peers
        '{"type": "answer", "sdp": "dummy sdp"}'  # to exit handle_connection
    ]

    # Patch websockets.connect to return this mock websocket
    monkeypatch.setattr(client.websockets, "connect", mock.AsyncMock(return_value=fake_ws))

    # Provide a mock RTCPeerConnection
    pc = mock.AsyncMock()
    pc.connectionState = "connected"

    await client.run_client(pc, "client1", "ws://fake-server", 8080)

    assert fake_ws.send.call_count >= 1
    

def test_entrypoint_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["client", "--server", "ws://ec2-54-153-105-27.us-west-1.compute.amazonaws.com", "--port", "8080", "--peer_id", "client1"])
    monkeypatch.setattr(client, "run_client", AsyncMock())

    client.entrypoint()
    client.run_client.assert_called_once()
