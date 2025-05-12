import asyncio
import argparse
import sys
import websockets
import json
import logging
import os

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel

# setup logging
logging.basicConfig(level=logging.INFO)

# global variables
CHUNK_SIZE = 64 * 1024

# directory to save files received from client
SAVE_DIR = "results"
received_files = {}

async def clean_exit(pc, websocket):
    logging.info("Closing WebRTC connection...")
    await pc.close()

    logging.info("Closing websocket connection...")
    await websocket.close()

    logging.info("Client shutdown complete. Exiting...")


async def handle_connection(pc: RTCPeerConnection, websocket):
    """Handles receiving SDP answer from Worker and ICE candidates from Worker.

    Args:
        pc: RTCPeerConnection object
        websocket: websocket connection object 
    
    Returns:
		None
        
    Raises:
		JSONDecodeError: Invalid JSON received
		Exception: An error occurred while handling the message
    """

    try:
        async for message in websocket:
            data = json.loads(message)

            # 1. receive answer SDP from worker and set it as this peer's remote description
            if data.get('type') == 'answer':
                logging.info(f"Received answer from worker: {data}")

                await pc.setRemoteDescription(RTCSessionDescription(sdp=data.get('sdp'), type=data.get('type')))

            # 2. to handle "trickle ICE" for non-local ICE candidates (might be unnecessary)
            elif data.get('type') == 'candidate':
                logging.info("Received ICE candidate")
                candidate = data.get('candidate')
                await pc.addIceCandidate(candidate)

            elif data.get('type') == 'quit': # NOT initiator, received quit request from worker
                logging.info("Worker has quit. Closing connection...")
                await clean_exit(pc, websocket)
                break

            # 3. error handling
            else:
                logging.debug(f"Unhandled message: {data}")
                logging.debug("exiting...")
                break
    
    except json.JSONDecodeError:
        logging.DEBUG("Invalid JSON received")

    except Exception as e:
        logging.DEBUG(f"Error handling message: {e}")


async def run_client(peer_id: str, DNS: str, port_number: str, file_path: str = None, CLI: bool = True):
    """Sends initial SDP offer to worker peer and establishes both connection & datachannel to be used by both parties.
	
		Initializes websocket to select worker peer and sends datachannel object to worker.
	
    Args:
		pc: RTCPeerConnection object
		peer_id: unique str identifier for client
        
    Returns:
		None
        
    Raises:
		Exception: An error occurred while running the client
    """

    logging.info("---PIP PACKAGE UPDATED---")
    pc = RTCPeerConnection()
    channel = pc.createDataChannel("my-data-channel")
    logging.info("channel(%s) %s" % (channel.label, "created by local party."))

    async def keep_ice_alive(channel):
        while True:
            await asyncio.sleep(15)
            if channel.readyState == "open":
                channel.send(b"KEEP_ALIVE")


    async def send_client_messages():
        """Handles typed messages from client to be sent to worker peer.
        
		  Takes input from client and sends it to worker peer via datachannel. Additionally, prompts for file upload to be sent to worker.
	
        Args:
			None
        
		Returns:
			None
        
        """
        message = input("Enter message to send (type 'file' to prompt file or type 'quit' to exit): ")
        data = None

        if message.lower() == "quit": # client is initiator, send quit request to worker
            logging.info("Quitting...")
            await pc.close()
            return 
        
        if channel.readyState != "open":
            logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
            return 
        
        if message.lower() == "file":
            logging.info("Prompting file...")
            file_path = input("Enter file path: (or type 'quit' to exit): ")
            if not file_path:
                logging.info("No file path entered.")
                return
            if file_path.lower() == "quit":
                logging.info("Quitting...")
                await pc.close()
                return
            if not os.path.exists(file_path):
                logging.info("File does not exist.")
                return
            else: 
                logging.info(f"Sending {file_path} to worker...")
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                
                # Send metadata first
                channel.send(f"FILE_META::{file_name}:{file_size}")  

                # Send file in chunks (32 KB)
                with open(file_path, "rb") as file:
                    logging.info(f"File opened: {file_path}")
                    while chunk := file.read(CHUNK_SIZE):
                        while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB 
                            await asyncio.sleep(0.1)

                        channel.send(chunk)

                channel.send("END_OF_FILE")
                logging.info(f"File sent to worker.")
                    
                # Flag data to True to prevent reg msg from being sent
                data = True

        if not data: # no file
          channel.send(message)
          logging.info(f"Message sent to worker.")
        
        # else: # file present
        #   logging.info(f"Sending {file_path} to worker...")
        #   file_name = os.path.basename(file_path)
        #   file_size = os.path.getsize(file_path)

        #   # Send metadata first
        #   channel.send(f"{file_name}:{file_size}")  

        #   # Send file in chunks
        #   channel.send(data)
        #   channel.send("END_OF_FILE")
        #   logging.info(f"File sent to worker.")


    async def send_client_file():
        """Handles direct, one-way file transfer from client to be sent to worker peer.
        
		  Takes file from client and sends it to worker peer via datachannel. Doesn't require typed responses.
	
        Args:
			None
        
		Returns:
			None
        
        """
        
        if channel.readyState != "open":
            logging.info(f"Data channel not open. Ready state is: {channel.readyState}")
            return 

        logging.info(f"Given file path {file_path}")
        if not file_path:
            logging.info("No file path entered.")
            return
        if not os.path.exists(file_path):
            logging.info("File does not exist.")
            return
        else: 
            logging.info(f"Sending {file_path} to worker...")

            # Obtain metadata
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            # Send metadata first
            channel.send(f"FILE_META::{file_name}:{file_size}")  

            # Send file in chunks (32 KB)
            with open(file_path, "rb") as file:
                logging.info(f"File opened: {file_path}")
                while chunk := file.read(CHUNK_SIZE):
                    while channel.bufferedAmount is not None and channel.bufferedAmount > 16 * 1024 * 1024: # Wait if buffer >16MB 
                        await asyncio.sleep(0.1)

                    channel.send(chunk)

            channel.send("END_OF_FILE")
            logging.info(f"File sent to worker.")
            
        return

    @channel.on("open")
    async def on_channel_open():
        """Event handler function for when the datachannel is open.
        Args:
			None
            
        Returns:
			None
        """

        asyncio.create_task(keep_ice_alive(channel))
        logging.info(f"{channel.label} is open")
        
        if CLI:
            await send_client_messages()
        else:
            await send_client_file()
    

    @channel.on("message")
    async def on_message(message):
        logging.info(f"Client received: {message}")
        
		# global received_files dictionary
        global received_files
        
        if isinstance(message, str):
            if message == b"KEEP_ALIVE":
                    logging.info("Keep alive message received.")
                    return
            
            if message == "END_OF_FILE":
                # File transfer complete, save to disk
                file_name, file_data = list(received_files.items())[0]
                # save_dir = QFileDialog.getExistingDirectory(
                #     None,
                #     f"Select directory to save received file: {file_name}",
                #     os.getcwd()
                # )
                
                try: 
                    file_save_dir = "models"
                    os.makedirs(file_save_dir, exist_ok=True)
                    file_path = os.path.join(file_save_dir, file_name)

                    with open(file_path, "wb") as file:
                        file.write(file_data)
                    logging.info(f"File saved as: {file_path}") 
                except PermissionError:
                    logging.error(f"Permission denied when writing to: {file_save_dir}")
                except Exception as e:
                    logging.error(f"Failed to save file: {e}")
                
                received_files.clear()

                if CLI:
                    # Prompt for next message
                    logging.info("File transfer complete. Enter next message:")
                    await send_client_messages()
                else:
                    await clean_exit(pc, websocket)

            elif "FILE_META::" in message: 
                # Metadata received (file name & size)
                _, meta = message.split("FILE_META::", 1)
                file_name, file_size, file_save_dir = meta.split(":")

                if file_name not in received_files:
                    received_files[file_name] = bytearray()  # Initialize as bytearray
                logging.info(f"File name received: {file_name}, of size {file_size}, saving to {file_save_dir}")

            else:
                logging.info(f"Worker sent: {message}")
                # await send_client_messages()
                
        elif isinstance(message, bytes):
            if message == b"KEEP_ALIVE":
                logging.info("Keep alive message received.")
                return

            file_name = list(received_files.keys())[0]
            if file_name not in received_files:
              received_files[file_name] = bytearray()
            received_files[file_name].extend(message)
                
        # await send_client_messages()


    # @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(f"ICE connection state is now {pc.iceConnectionState}")
        if pc.iceConnectionState in ["connected", "completed"]:
            logging.info("ICE connection established.")
            # connected_event.set()
        elif pc.iceConnectionState in ["failed", "disconnected"]:
            logging.info("ICE connection failed/disconnected. Closing connection.")
            await clean_exit(pc, websocket)
            return
        elif pc.iceConnectionState == "closed":
            logging.info("ICE connection closed.")
            await clean_exit(pc, websocket)
            return
        
    # Register the event handler explicitly
    pc.on("iceconnectionstatechange", on_iceconnectionstatechange)


    # 1. client registers with the signaling server via websocket connection
    # this is how the client will know the worker peer exists
    async with websockets.connect(f"{DNS}:{port_number}") as websocket:
        # 1a. register the client with the signaling server
        await websocket.send(json.dumps({'type': 'register', 'peer_id': peer_id}))
        logging.info(f"{peer_id} sent to signaling server for registration!")

        # 1b. query for available workers
        await websocket.send(json.dumps({'type': 'query'}))
        response = await websocket.recv()
        available_workers = json.loads(response)["peers"]
        logging.info(f"Available workers: {available_workers}")

        # 1c. select a worker to connect to (will implement firebase auth later)
        target_worker = available_workers[0] if available_workers else None
        logging.info(f"Selected worker: {target_worker}")

        if not target_worker:
            logging.info("No workers available")
            return
        
        # 2. create and send SDP offer to worker peer
        await pc.setLocalDescription(await pc.createOffer())
        await websocket.send(json.dumps({'type': pc.localDescription.type, 'target': target_worker, 'sdp': pc.localDescription.sdp}))
        logging.info('Offer sent to worker')

        # 3. handle incoming messages from server (e.g. answer from worker)
        await handle_connection(pc, websocket)

    await pc.close()
    await websocket.close()
    

def entrypoint():
    """Main CLI function to run the client.
      Args:
        None
      Returns:
        None
    """
    parser = argparse.ArgumentParser(description="SLEAP webRTC Client")

    parser.add_argument("--server", type=str, default="ws://ec2-54-153-105-27.us-west-1.compute.amazonaws.com", help="WebSocket server DNS/address, ex. 'ws://ec2-54-158-36-90.compute-1.amazonaws.com'")
    parser.add_argument("--port", type=int, default=8080, help="WebSocket server port number, ex. '8080'")
    parser.add_argument("--peer_id", type=str, default="client1", help="Unique identifier for the client")

    args = parser.parse_args()

    DNS = args.server
    port_number = args.port
    peer_id = args.peer_id

    try: 
        asyncio.run(run_client(peer_id, DNS, port_number, file_path=None, CLI=True))
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt: Exiting...")
    finally:
        logging.info("exited")


if __name__ == "__main__":
    entrypoint()

    