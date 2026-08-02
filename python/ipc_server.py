"""
ipc_server.py — Named Pipe Server (Python ← AHK)
==================================================
Listens on a Windows named pipe for JSON queries from the AHK layer.
Dispatches queries to the vision service, container manager, and
reconnect pipeline. Returns JSON responses.
"""

import json
import logging
import struct
import threading
import time
from typing import Any, Callable, Dict, Optional

import pywintypes
import win32file
import win32pipe

logger = logging.getLogger(__name__)


class IPCServer:
    """
    Windows named pipe server for AHK↔Python communication.

    Protocol:
    - AHK sends a JSON message (UTF-8 bytes)
    - Python reads, dispatches, and writes back a JSON response
    - Pipe runs in message mode (PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE)
    """

    PIPE_NAME = r"\\.\pipe\RenzeiMacroVision"
    BUFFER_SIZE = 65536

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False

    def register_handler(self, command: str, handler: Callable[[dict], dict]):
        """
        Register a handler for a specific command.

        Args:
            command: The "cmd" field value to match.
            handler: Function that takes the query dict and returns a response dict.
        """
        self._handlers[command] = handler
        logger.debug("Registered handler for command: %s", command)

    def start(self):
        """Start the IPC server in a background thread."""
        if self._is_running:
            logger.warning("IPC server is already running")
            return

        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(target=self._server_loop, daemon=True, name="IPCServer")
        self._thread.start()
        logger.info("IPC server started on %s", self.PIPE_NAME)

    def stop(self):
        """Stop the IPC server."""
        if not self._is_running:
            return

        self._stop_event.set()
        self._is_running = False

        # Create a dummy connection to unblock ConnectNamedPipe
        try:
            win32file.CreateFile(
                self.PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
        except Exception:
            pass

        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

        logger.info("IPC server stopped")

    def _server_loop(self):
        """Main server loop: create pipe, wait for connections, handle messages."""
        while not self._stop_event.is_set():
            pipe_handle = None
            try:
                # Create the named pipe
                pipe_handle = win32pipe.CreateNamedPipe(
                    self.PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    (win32pipe.PIPE_TYPE_MESSAGE
                     | win32pipe.PIPE_READMODE_MESSAGE
                     | win32pipe.PIPE_WAIT),
                    1,  # Max instances
                    self.BUFFER_SIZE,
                    self.BUFFER_SIZE,
                    0,  # Default timeout
                    None,  # Default security
                )

                # Wait for a client to connect
                logger.debug("Waiting for pipe connection...")
                win32pipe.ConnectNamedPipe(pipe_handle, None)

                if self._stop_event.is_set():
                    break

                logger.info("Client connected to IPC pipe")

                # Handle messages from this client until disconnected
                self._handle_client(pipe_handle)

            except pywintypes.error as e:
                if not self._stop_event.is_set():
                    logger.error("Pipe error: %s", e)
                    time.sleep(1)  # Avoid tight loop on persistent errors
            finally:
                if pipe_handle:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass

    def _handle_client(self, pipe_handle):
        """Handle messages from a connected client until disconnect."""
        while not self._stop_event.is_set():
            try:
                # Read message from pipe
                hr, data = win32file.ReadFile(pipe_handle, self.BUFFER_SIZE)
                if hr != 0:
                    logger.warning("ReadFile returned hr=%d", hr)
                    break

                message = data.decode("utf-8")
                logger.debug("Received: %s", message[:200])

                # Parse and dispatch
                try:
                    query = json.loads(message)
                except json.JSONDecodeError as e:
                    response = {"error": f"Invalid JSON: {e}"}
                    self._send_response(pipe_handle, response)
                    continue

                response = self._dispatch(query)
                self._send_response(pipe_handle, response)

            except pywintypes.error as e:
                error_code = e.args[0]
                if error_code == 109:  # ERROR_BROKEN_PIPE
                    logger.info("Client disconnected")
                else:
                    logger.error("Read error: %s", e)
                break
            except Exception as e:
                logger.error("Unexpected error handling client: %s", e)
                try:
                    self._send_response(pipe_handle, {"error": str(e)})
                except Exception:
                    break

    def _dispatch(self, query: dict) -> dict:
        """Dispatch a query to the appropriate handler."""
        cmd = query.get("cmd", "")

        if cmd in self._handlers:
            try:
                start = time.perf_counter()
                result = self._handlers[cmd](query)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.debug("Handler '%s' completed in %.1fms", cmd, elapsed_ms)
                return result
            except Exception as e:
                logger.error("Handler '%s' failed: %s", cmd, e)
                return {"error": f"Handler error: {e}"}
        else:
            logger.warning("Unknown command: %s", cmd)
            return {"error": f"Unknown command: {cmd}"}

    def _send_response(self, pipe_handle, response: dict):
        """Send a JSON response back through the pipe."""
        try:
            data = json.dumps(response).encode("utf-8")
            win32file.WriteFile(pipe_handle, data)
            logger.debug("Sent: %s", json.dumps(response)[:200])
        except Exception as e:
            logger.error("Failed to send response: %s", e)
            raise
