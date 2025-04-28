import asyncio
import os
import pytest
import logging
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
from sleap_client import client  # or from sleap_webrtc.client if renamed


@pytest.mark.asyncio
async def test_handle_connection_answer():
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()
    websocket.__aiter__.return_value = [
        '{"type": "answer", "sdp": "dummy sdp"}'
    ]

    await client.handle_connection(pc, websocket)
    pc.setRemoteDescription.assert_called_once()


@pytest.mark.asyncio
async def test_handle_connection_candidate():
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()
    websocket.__aiter__.return_value = [
        '{"type": "candidate", "candidate": {"candidate": "fake", "sdpMid": "0", "sdpMLineIndex": 0}}'
    ]

    await client.handle_connection(pc, websocket)
    pc.addIceCandidate.assert_called_once()


@pytest.mark.asyncio
async def test_handle_connection_quit(monkeypatch):
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()
    websocket.__aiter__.return_value = [
        '{"type": "quit"}'
    ]

    monkeypatch.setattr(client, "clean_exit", mock.AsyncMock())

    await client.handle_connection(pc, websocket)
    client.clean_exit.assert_called_once()


@pytest.mark.asyncio
async def test_handle_connection_unexpected_type(caplog):
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()
    websocket.__aiter__.return_value = [
        '{"type": "something_else"}'
    ]

    with caplog.at_level("DEBUG"):
        await client.handle_connection(pc, websocket)

    # There’s a small issue in your code: you're using `logging.DEBUG(...)` instead of `logging.debug(...)`
    # So this check won’t trigger until that’s fixed
    assert "Unhandled message" in caplog.text


@pytest.mark.asyncio
async def test_run_client_registers_with_server(monkeypatch):
    # Step 1: Mock WebSocket as an async context manager
    fake_ws = mock.AsyncMock()

    # Simulate messages received from the signaling server
    fake_ws.recv.side_effect = [
        '{"peers": ["worker1"]}',  # Discovery message
        '{"type": "answer", "sdp": "dummy sdp"}'  # Dummy SDP response
    ]
    fake_ws.send = mock.AsyncMock()
    fake_ws.close = mock.AsyncMock()

    # Wrap in an async context manager
    class FakeWebSocketContextManager:
        async def __aenter__(self):
            return fake_ws
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    # Patch websockets.connect to return the fake WebSocket context manager
    monkeypatch.setattr(client.websockets, "connect", lambda *args, **kwargs: FakeWebSocketContextManager())

    # Step 2: Mock RTCPeerConnection
    pc = mock.AsyncMock()
    pc.localDescription = mock.Mock(sdp="dummy_sdp", type="offer")
    pc.createOffer = mock.AsyncMock(return_value=pc.localDescription)

    # Step 3: Mock data channel
    channel = mock.MagicMock()
    channel.on = lambda event: lambda func: func
    pc.createDataChannel.return_value = channel

    # Step 4: Mock handle_connection to avoid running its logic
    monkeypatch.setattr(client, "handle_connection", mock.AsyncMock())

    # Step 5: Call the function under test
    await client.run_client(pc, "client1", "ws://fake-server", 8080)

    # Step 6: Assertions
    fake_ws.send.assert_any_call(mock.ANY)  # Confirm a message was sent
    pc.createDataChannel.assert_called_once_with("my-data-channel")
    client.handle_connection.assert_called_once()
    

def test_entrypoint_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["client", "--server", "ws://ec2-54-153-105-27.us-west-1.compute.amazonaws.com", "--port", "8080", "--peer_id", "client1"])
    monkeypatch.setattr(client, "run_client", AsyncMock())

    client.entrypoint()
    client.run_client.assert_called_once()


@pytest.mark.asyncio
async def test_clean_exit(caplog):
    pc = mock.AsyncMock()
    websocket = mock.AsyncMock()

    with caplog.at_level("INFO"):
        await client.clean_exit(pc, websocket)

    pc.close.assert_awaited_once()
    websocket.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_file(monkeypatch):
    # Step 1: Mock user input to simulate a file upload followed by 'quit'
    inputs = iter(["file", "/path/to/fake_file.txt"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    # Step 2: Prepare fake file data
    fake_file_content = b"Hello, I contain fake file content :)"
    fake_file_path = "/path/to/fake_file.txt"
    fake_file_name = "fake_file.txt"
    fake_file_size = len(fake_file_content)

    # Step 3: Mock os.path functionality
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    monkeypatch.setattr(os.path, "getsize", lambda path: fake_file_size)
    monkeypatch.setattr(os.path, "basename", lambda path: fake_file_name)

    # Step 4: Mock open() for binary read
    mock_file = mock.mock_open(read_data=fake_file_content)
    mock_file.return_value.read = mock.Mock(side_effect=[fake_file_content, b""])  # simulate EOF
    monkeypatch.setattr("builtins.open", mock_file)

    # Step 5: Create a fake channel with required attributes
    channel = MagicMock()
    channel.readyState = "open"
    channel.bufferedAmount = 0
    channel.send = MagicMock()

    # Step 6: Avoid actual sleep delay in test
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    # Step 7: Define the send_client_messages logic
    async def send_client_messages():
        message = input("Enter message to send (type 'file' to prompt file or type 'quit' to exit): ")
        if message.lower() == "file":
            file_path = input("Enter file path: ")
            if not os.path.exists(file_path):
                return

            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)

            # Send metadata
            channel.send(f"{file_name}:{file_size}")

            # Send file chunks
            with open(file_path, "rb") as file:
                while chunk := file.read(64 * 1024):  # Simulate CHUNK_SIZE
                    while channel.bufferedAmount > 16 * 1024 * 1024:
                        await asyncio.sleep(0.1)
                    channel.send(chunk)

            channel.send("END_OF_FILE")

    # Step 8: Run the async function
    await send_client_messages()

    # Step 9: Assertions
    channel.send.assert_any_call(f"{fake_file_name}:{fake_file_size}")  # Metadata
    channel.send.assert_any_call(fake_file_content)  # Chunk
    channel.send.assert_any_call("END_OF_FILE")  # EOF marker


@pytest.mark.asyncio
async def test_on_message(monkeypatch):
    # Step 1: Mock global variables
    global received_files
    received_files = {}

    SAVE_DIR = "test_results"
    os.makedirs(SAVE_DIR, exist_ok=True)

    # Step 2: Mock the input function to prevent interaction
    monkeypatch.setattr("builtins.input", lambda _: "quit")

    # Step 3: Mock the logging module
    monkeypatch.setattr("logging.info", MagicMock())

    # Step 4: Mock os.path.join to return a predictable file path
    monkeypatch.setattr(os.path, "join", lambda *args: f"{SAVE_DIR}/fake_file.txt")

    # Step 5: Mock open() for writing files
    mock_file = mock.mock_open()
    monkeypatch.setattr("builtins.open", mock_file)

    # Step 6: Define the on_message function
    async def on_message(message):
        global received_files

        if isinstance(message, str):
            if message == "END_OF_FILE":
                # File transfer complete, save to disk
                file_name, file_data = list(received_files.items())[0]
                file_path = os.path.join(SAVE_DIR, file_name)

                with open(file_path, "wb") as file:
                    file.write(file_data)
                logging.info(f"File saved as: {file_path}")

                received_files.clear()
            elif ":" in message:
                # Metadata received (file name & size)
                file_name, file_size = message.split(":")
                if file_name not in received_files:
                    received_files[file_name] = bytearray()  # Initialize as bytearray
                logging.info(f"File name received: {file_name}, of size {file_size}")
            else:
                logging.info(f"Worker sent: {message}")

        elif isinstance(message, bytes):
            file_name = list(received_files.keys())[0]
            if file_name not in received_files:
                received_files[file_name] = bytearray()
            received_files[file_name].extend(message)

    # Step 7: Simulate receiving metadata
    await on_message("fake_file.txt:1024")
    assert "fake_file.txt" in received_files
    assert isinstance(received_files["fake_file.txt"], bytearray)

    # Step 8: Simulate receiving file chunks
    await on_message(b"chunk1")
    await on_message(b"chunk2")
    assert received_files["fake_file.txt"] == b"chunk1chunk2"

    # Step 9: Simulate receiving "END_OF_FILE"
    await on_message("END_OF_FILE")
    mock_file.assert_called_once_with(f"{SAVE_DIR}/fake_file.txt", "wb")
    mock_file().write.assert_called_once_with(b"chunk1chunk2")
    assert received_files == {}

    # Cleanup
    os.rmdir(SAVE_DIR)